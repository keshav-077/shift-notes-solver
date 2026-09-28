"""Dev-only check that the symbolic solver is enough when extraction is perfect.

The patterns below describe the visible phrasing pool. They are a measuring
stick for extraction accuracy during development. The answering pipeline
does not import this module, and a held-out set worded differently would
not match these patterns on purpose.
"""

from __future__ import annotations

import re

from wsolver.header import split_notes, resolve_header
from wsolver.solver import Constraint, render_answer

# Lead-ins the scorer itself treats as removable. Used only so the oracle
# can see the sentence underneath; the pipeline does not use this list.
_HEDGES = (
    "i'm fairly sure ",
    "from what i recall, ",
    "as far as i know, ",
    "i believe ",
    "going off the roster, ",
    "if memory serves, ",
    "my recollection is that ",
    "unless i have this wrong, ",
    "speaking from memory, ",
    "i'm reasonably confident ",
)


def _bare(line: str) -> str:
    text = line
    head, sep, tail = text.partition(": ")
    if sep and len(head) < 60:
        text = tail
    lowered = text.lower()
    for hedge in _HEDGES:
        if lowered.startswith(hedge):
            text = text[len(hedge):]
            break
    return text[:1].upper() + text[1:] if text else text


def _classify(sentence: str, names: list[str], stations: list[str]):
    name = r"(" + "|".join(names) + r")"
    station = r"(" + "|".join(stations) + r")"
    block = r"(\d\d:\d\d)"
    rules = [
        (rf"^{name} sits between {name} and {name} on the rota\.$", lambda m: ("between", m[1], m[2], m[3])),
        (rf"^Put {name} between {name} and {name}, though not necessarily next to either\.$", lambda m: ("between", m[1], m[2], m[3])),
        (rf"^Whichever way round {name} and {name} are, {name} is between them\.$", lambda m: ("between", m[3], m[1], m[2])),
        (rf"^{name} is on after one of {name} and {name} and before the other\.$", lambda m: ("between", m[1], m[2], m[3])),
        (rf"^{name}'s block falls between {name}'s and {name}'s, in one order or the other\.$", lambda m: ("between", m[1], m[2], m[3])),
        (rf"^{name} works at some point between {name} and {name}, not necessarily adjacent to either\.$", lambda m: ("between", m[1], m[2], m[3])),
        (rf"^The {station} station is covered earlier in the day than {name}'s block\.$", lambda m: ("holder_before", m[1], m[2])),
        (rf"^Whoever is on {station} works earlier in the day than {name}\.$", lambda m: ("holder_before", m[1], m[2])),
        (rf"^{name} is on later than whoever has {station}\.$", lambda m: ("holder_before", m[2], m[1])),
        (rf"^The person on {station} precedes {name}\.$", lambda m: ("holder_before", m[1], m[2])),
        (rf"^Whoever has {station} is done before {name} starts\.$", lambda m: ("holder_before", m[1], m[2])),
        (rf"^{station} is covered before {name} comes on\.$", lambda m: ("holder_before", m[1], m[2])),
        (rf"^{station} is covered by someone other than {name}\.$", lambda m: ("station_not", m[2], m[1])),
        (rf"^{name} has not been put on {station}\.$", lambda m: ("station_not", m[1], m[2])),
        (rf"^{name} is not on {station}\.$", lambda m: ("station_not", m[1], m[2])),
        (rf"^You can rule {name} out for {station}\.$", lambda m: ("station_not", m[1], m[2])),
        (rf"^{station} is not {name}'s station\.$", lambda m: ("station_not", m[2], m[1])),
        (rf"^{name} is not assigned to {station}\.$", lambda m: ("station_not", m[1], m[2])),
        (rf"^{station} is {name}'s station\.$", lambda m: ("station_is", m[2], m[1])),
        (rf"^{name} is assigned to {station}\.$", lambda m: ("station_is", m[1], m[2])),
        (rf"^The {station} station is down to {name}\.$", lambda m: ("station_is", m[2], m[1])),
        (rf"^{name} has {station} this week\.$", lambda m: ("station_is", m[1], m[2])),
        (rf"^{station} is covered by {name}\.$", lambda m: ("station_is", m[2], m[1])),
        (rf"^{name} is on {station}\.$", lambda m: ("station_is", m[1], m[2])),
        (rf"^Whoever drew the {block} block, it was {name}\.$", lambda m: ("block_is", m[2], m[1])),
        (rf"^The {block} block is {name}'s\.$", lambda m: ("block_is", m[2], m[1])),
        (rf"^{name} is the one who opens up at {block}\.$", lambda m: ("block_is", m[1], m[2])),
        (rf"^{name} takes {block}, as things stand\.$", lambda m: ("block_is", m[1], m[2])),
        (rf"^{block} is when {name} is scheduled\.$", lambda m: ("block_is", m[2], m[1])),
        (rf"^{name} is on the {block} block\.$", lambda m: ("block_is", m[1], m[2])),
        (rf"^{name} has a standing commitment that rules out the {block} block entirely\.$", lambda m: ("block_not", m[1], m[2])),
        (rf"^{block} is the one block {name} is definitely not on\.$", lambda m: ("block_not", m[2], m[1])),
        (rf"^{name} is unavailable at {block}\.$", lambda m: ("block_not", m[1], m[2])),
        (rf"^You will not find {name} on the {block} block\.$", lambda m: ("block_not", m[1], m[2])),
        (rf"^{name} is not on the {block} block\.$", lambda m: ("block_not", m[1], m[2])),
        (rf"^The {block} block is not {name}'s\.$", lambda m: ("block_not", m[2], m[1])),
        (rf"^{name} relieves {name} directly, with no block in between\.$", lambda m: ("adjacent", m[2], m[1])),
        (rf"^{name} is on the block directly after {name}\.$", lambda m: ("adjacent", m[2], m[1])),
        (rf"^{name} then {name}, back to back\.$", lambda m: ("adjacent", m[1], m[2])),
        (rf"^{name} hands straight over to {name}\.$", lambda m: ("adjacent", m[1], m[2])),
        (rf"^{name} is on the block immediately before {name}\.$", lambda m: ("adjacent", m[1], m[2])),
        (rf"^There is no block between {name}'s and {name}'s, in that order\.$", lambda m: ("adjacent", m[1], m[2])),
        (rf"^{name} comes later in the day than {name}\.$", lambda m: ("before", m[2], m[1])),
        (rf"^By the time {name} starts, {name} has already been on\.$", lambda m: ("before", m[2], m[1])),
        (rf"^{name}'s block falls somewhere earlier than {name}'s\.$", lambda m: ("before", m[1], m[2])),
        (rf"^{name} works earlier in the day than {name}\.$", lambda m: ("before", m[1], m[2])),
        (rf"^{name} takes over from {name} later in the day\.$", lambda m: ("before", m[2], m[1])),
        (rf"^{name} is done before {name} starts\.$", lambda m: ("before", m[1], m[2])),
    ]
    for pattern, build in rules:
        matched = re.match(pattern, sentence, flags=re.IGNORECASE)
        if matched:
            return build(matched)
    return None


def _canon_args(kind: str, raw: tuple, header) -> tuple | None:
    def person(value: str) -> str | None:
        return header.match_name(value)

    def station(value: str) -> str | None:
        return header.match_station(value)

    def block(value: str) -> str | None:
        return header.match_block(value)

    if kind == "between":
        mid, a, b = person(raw[0]), person(raw[1]), person(raw[2])
        if not (mid and a and b):
            return None
        ends = tuple(sorted((a, b)))
        return (mid, ends[0], ends[1])
    if kind in {"holder_before"}:
        where, who = station(raw[0]), person(raw[1])
        return (where, who) if where and who else None
    if kind in {"station_is", "station_not"}:
        who, where = person(raw[0]), station(raw[1])
        return (who, where) if who and where else None
    if kind in {"block_is", "block_not"}:
        who, when = person(raw[0]), block(raw[1])
        return (who, when) if who and when else None
    if kind in {"adjacent", "before"}:
        early, late = person(raw[0]), person(raw[1])
        return (early, late) if early and late else None
    return None


def oracle_constraints(item: dict) -> tuple:
    """Header plus one constraint per hard line. Filler and non-assertions are absent."""
    header = resolve_header(item["text"], n_staff=item.get("n_staff"), n_stations=item.get("n_stations"))
    if header is None:
        raise ValueError(item.get("id"))
    _, body = split_notes(item["text"])
    constraints = []
    for index, line in enumerate(body):
        classified = _classify(_bare(line), header.staff, header.stations)
        if classified is None:
            continue
        kind, *raw = classified
        args = _canon_args(kind, tuple(raw), header)
        if args is None:
            continue
        constraints.append(
            Constraint(kind=kind, args=args, line=index, source=line, agreement=1.0)
        )
    return header, constraints


def oracle_answer(item: dict) -> dict:
    header, constraints = oracle_constraints(item)
    return render_answer(header, constraints)
