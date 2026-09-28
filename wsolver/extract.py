"""Turn a model completion into typed constraints.

Validation is symbolic and generic: names, blocks and stations are coerced
onto the header's own strings, a station mentioned where a person belongs
becomes a constraint on that station's holder, and a constraint the header
makes impossible on its own is dropped as a bad translation. Nothing here
matches the wording of a body line.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from wsolver.header import Header
from wsolver.solver import KINDS, Constraint

NONE = ("none",)

_ALIASES = {
    "block": "block_is",
    "on_block": "block_is",
    "block_is": "block_is",
    "block_not": "block_not",
    "not_block": "block_not",
    "station": "station_is",
    "station_is": "station_is",
    "station_not": "station_not",
    "not_station": "station_not",
    "before": "before",
    "earlier": "before",
    "adjacent": "adjacent",
    "between": "between",
    "holder_order": "holder_order",
    "holder_before": "holder_before",
    "station_before": "holder_before",
    "holder_after": "holder_after",
    "station_after": "holder_after",
    "none": "none",
    "filler": "none",
    "irrelevant": "none",
    "n/a": "none",
}


@dataclass
class LineDecision:
    canon: tuple
    agreement: float
    disagreement: bool
    base_n: int
    repair_n: int


@dataclass
class VoteBoard:
    """Per-line tallies. Repair votes are extra looks, not replacements."""

    n_lines: int
    base: list[Counter] = field(init=False)
    repair: list[Counter] = field(init=False)

    def __post_init__(self) -> None:
        self.base = [Counter() for _ in range(self.n_lines)]
        self.repair = [Counter() for _ in range(self.n_lines)]

    def add(self, votes: dict[int, tuple], *, repair: bool) -> None:
        target = self.repair if repair else self.base
        for index, canon in votes.items():
            if 0 <= index < self.n_lines:
                target[index][canon] += 1


def _loads_first(text: str) -> object | None:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start < 0:
        start = stripped.find("[")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(stripped, start)
        return obj
    except json.JSONDecodeError:
        return None


def _salvage_objects(text: str) -> list[dict]:
    """Recover complete objects from a truncated completion."""
    decoder = json.JSONDecoder()
    found: list[dict] = []
    cursor = 0
    while True:
        start = text.find("{", cursor)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        if isinstance(obj, dict) and "type" in obj:
            found.append(obj)
        cursor = max(end, start + 1)
    return found


def payload_of(text: str, finish: str) -> tuple[dict | None, list[dict], bool]:
    """Return (top-level object or None, constraint objects, truncated?)."""
    parsed = _loads_first(text)
    truncated = finish == "length"
    if isinstance(parsed, list):
        objects = [obj for obj in parsed if isinstance(obj, dict)]
        return {"constraints": objects}, objects, truncated
    if isinstance(parsed, dict):
        raw = parsed.get("constraints")
        if not isinstance(raw, list):
            raw = parsed.get("lines")
        if isinstance(raw, list):
            objects = [obj for obj in raw if isinstance(obj, dict)]
            return parsed, objects, truncated
        salvaged = _salvage_objects(text)
        return parsed, salvaged, True
    salvaged = _salvage_objects(text)
    return None, salvaged, True


def _line_number(obj: dict) -> int | None:
    raw = obj.get("line", obj.get("n", obj.get("id")))
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _kind(obj: dict) -> str | None:
    raw = obj.get("type", obj.get("kind"))
    if not isinstance(raw, str):
        return None
    return _ALIASES.get(raw.strip().casefold().replace("-", "_").replace(" ", "_"))


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parts = re.sub(r"\s+and\s+", ", ", value, flags=re.IGNORECASE)
        return [part.strip() for part in parts.split(",") if part.strip()]
    return []


def _entity(header: Header, value: object, *, exact: bool) -> tuple[str, str] | None:
    """('person', name) or ('station', name), using the header's spelling."""
    if not isinstance(value, str):
        return None
    person = header.match_name(value, exact=exact)
    station = header.match_station(value, exact=exact)
    if person and station:
        return ("person", person)
    if person:
        return ("person", person)
    if station:
        return ("station", station)
    return None


def _structurally_ok(header: Header, kind: str, args: tuple) -> bool:
    """False when the header alone makes this one constraint impossible or empty."""
    staff = set(header.staff)
    holders = set(header.holders)
    if kind in {"block_is", "block_not"}:
        return args[0] in staff and args[1] in header.blocks
    if kind == "station_is":
        # The header already says who holds a station. A translation that
        # puts a non-holder on one contradicts the header, so it is a
        # misread rather than a rota fact.
        return args[0] in holders and args[1] in header.stations
    if kind == "station_not":
        if args[0] not in holders or args[1] not in header.stations:
            return False
        return True
    if kind in {"before", "adjacent"}:
        return (
            args[0] in staff
            and args[1] in staff
            and args[0] != args[1]
        )
    if kind == "between":
        middle, left, right = args
        people = {middle, left, right}
        return people <= staff and len(people) == 3
    if kind in {"holder_before", "holder_after"}:
        return args[0] in header.stations and args[1] in staff
    return False


def canonical(obj: dict, header: Header, *, validate: bool) -> tuple | None:
    """A hashable constraint, ('none',), or None if the object is unusable.

    `validate` turns on coercion onto header strings and rejection of
    constraints the header makes impossible. With it off, only exact
    header strings are accepted and a non-holder can still be placed on
    a station.
    """
    kind = _kind(obj)
    if kind is None:
        return None
    if kind == "none":
        return NONE
    exact = not validate
    if kind == "holder_order":
        return _holder_order(obj, header, exact=exact, validate=validate)
    if kind not in KINDS and kind not in {"holder_before", "holder_after"}:
        return None
    # holder_before / holder_after are legacy names. "after" is read the way
    # the word reads: the named person is after the holder, so the holder is
    # earlier. person_is overrides that when the model states it.
    if kind in {"holder_before", "holder_after"}:
        return _holder_order(obj, header, exact=exact, validate=validate, legacy=kind)

    def person(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        return header.match_name(value, exact=exact)

    def block(value: object) -> str | None:
        return header.match_block(value, exact=exact)

    def station(value: object) -> str | None:
        return header.match_station(value, exact=exact)

    built: tuple | None = None
    if kind in {"block_is", "block_not"}:
        who = person(obj.get("person") or obj.get("name"))
        when = block(obj.get("block") or obj.get("time"))
        if who and when:
            built = (kind, who, when)
    elif kind in {"station_is", "station_not"}:
        who = person(obj.get("person") or obj.get("name"))
        where = station(obj.get("station"))
        if who and where:
            built = (kind, who, where)
    elif kind in {"before", "adjacent"}:
        built = _order(kind, obj, header, exact=exact, validate=validate)
    elif kind == "between":
        built = _between(obj, header, exact=exact)
    if built is None:
        return None
    if validate and not _structurally_ok(header, built[0], built[1:]):
        return None
    return built


def _holder_order(obj, header, *, exact: bool, validate: bool, legacy: str | None = None):
    """Map a holder/person order onto holder_before or holder_after.

    holder_before in the solver means the holder works earlier than the
    named person. person_is "later" is that case. person_is "earlier" is
    the opposite. A bare "holder_after" with no person_is is read as the
    named person coming after the holder: that is how the word "after"
    is used, and the field the person sits in is not trusted.
    """
    raw_station = obj.get("station")
    raw_person = obj.get("person") or obj.get("later") or obj.get("earlier") or obj.get("name")
    where = header.match_station(raw_station, exact=exact) if isinstance(raw_station, str) else None
    who = header.match_name(raw_person, exact=exact) if isinstance(raw_person, str) else None
    if not where or not who:
        return None
    person_is = str(obj.get("person_is") or "").strip().casefold()
    # The gloss is written before the label and, on this model, tracks the
    # sentence when the label is flipped. If the two disagree, keep the gloss.
    from_gloss = _gloss_direction(obj.get("gloss"), who)
    if from_gloss and person_is in {"earlier", "before", "later", "after"}:
        label_says_later = person_is in {"later", "after"}
        if from_gloss == "later" and not label_says_later:
            person_is = "later"
        elif from_gloss == "earlier" and label_says_later:
            person_is = "earlier"
    if person_is in {"earlier", "before"}:
        kind = "holder_after"
    elif person_is in {"later", "after"} or legacy == "holder_after":
        kind = "holder_before"
    elif legacy == "holder_before" or legacy is None:
        kind = "holder_before"
    else:
        return None
    if legacy is None and person_is not in {"earlier", "before", "later", "after"}:
        return None
    built = (kind, where, who)
    if validate and not _structurally_ok(header, kind, built[1:]):
        return None
    return built


def _gloss_direction(gloss: object, person: str) -> str | None:
    """Whether the gloss says this person is earlier or later than the holder.

    Returns None when the gloss does not say. Used only to break a conflict
    with the structured label, never to invent an order the gloss omitted.
    """
    if not isinstance(gloss, str) or not person:
        return None
    text = " ".join(gloss.casefold().split())
    name = re.escape(person.casefold())
    if re.search(rf"\b{name}(?:'s|’s)?\s+block\s+is\s+later\b", text):
        return "later"
    if re.search(rf"\b{name}\s+is\s+later\b", text) or re.search(rf"\b{name}\s+works\s+later\b", text):
        return "later"
    if re.search(rf"\b{name}(?:'s|’s)?\s+block\s+is\s+earlier\b", text):
        return "earlier"
    if re.search(rf"\b{name}\s+is\s+earlier\b", text) or re.search(rf"\b{name}\s+works\s+earlier\b", text):
        return "earlier"
    if re.search(rf"\bearlier\b.{{0,40}}\bthan\s+{name}\b", text):
        return "later"
    if re.search(rf"\blater\b.{{0,40}}\bthan\s+{name}\b", text):
        return "earlier"
    return None


def _mentioned(line: str, tokens: list[str]) -> list[str]:
    return [
        token
        for token in tokens
        if re.search(rf"\b{re.escape(token)}\b", line, flags=re.IGNORECASE)
    ]


def _fix_one(value: str, mentioned: list[str]) -> str | None:
    if value in mentioned:
        return value
    if len(mentioned) == 1:
        return mentioned[0]
    return None


def _fix_people(args: tuple, mentioned: list[str]) -> tuple | None:
    """Replace a single person who does not appear in the line.

    Swapping several people at once would be a guess, so that constraint
    is dropped instead.
    """
    if all(person in mentioned for person in args):
        return args
    bad = [person for person in args if person not in mentioned]
    unused = [person for person in mentioned if person not in args]
    if len(bad) == 1 and len(unused) == 1:
        return tuple(unused[0] if person not in mentioned else person for person in args)
    return None


def ground(canon: tuple, line: str, header: Header) -> tuple:
    """Drop or repair a constraint that names someone the line never names.

    The model chooses the relation. It does not get to introduce a person,
    block or station the line does not contain. When the line names exactly
    one of those and the model named a different one, the line's entity is
    used. Anything else is discarded, which under-constrains rather than
    inventing a fact.
    """
    if not canon or canon == NONE:
        return canon or NONE
    kind = canon[0]
    people = _mentioned(line, header.staff)
    stations = _mentioned(line, header.stations)
    blocks = _mentioned(line, header.blocks)
    if kind in {"block_is", "block_not"}:
        who, when = _fix_one(canon[1], people), _fix_one(canon[2], blocks)
        return (kind, who, when) if who and when else NONE
    if kind in {"station_is", "station_not"}:
        who, where = _fix_one(canon[1], people), _fix_one(canon[2], stations)
        return (kind, who, where) if who and where else NONE
    if kind in {"before", "adjacent"}:
        fixed = _fix_people((canon[1], canon[2]), people)
        if fixed is None or fixed[0] == fixed[1]:
            return NONE
        return (kind, fixed[0], fixed[1])
    if kind == "between":
        fixed = _fix_people((canon[1], canon[2], canon[3]), people)
        if fixed is None or len(set(fixed)) != 3:
            return NONE
        ends = tuple(sorted((fixed[1], fixed[2])))
        return ("between", fixed[0], ends[0], ends[1])
    if kind in {"holder_before", "holder_after"}:
        where, who = _fix_one(canon[1], stations), _fix_one(canon[2], people)
        return (kind, where, who) if where and who else NONE
    return canon


def _order(
    kind: str,
    obj: dict,
    header: Header,
    *,
    exact: bool,
    validate: bool,
) -> tuple | None:
    earlier = _entity(header, obj.get("earlier"), exact=exact)
    later = _entity(header, obj.get("later"), exact=exact)
    if earlier is None or later is None:
        return None
    # Adjacency is a relation between two people. A station in that slot
    # is the unnamed holder, and the only well-typed reading is a holder
    # order in the same direction. Losing the "immediately" is a miss
    # toward too many solutions, not a fabricated contradiction.
    if earlier[0] == "person" and later[0] == "person":
        if earlier[1] == later[1]:
            return None
        return (kind, earlier[1], later[1])
    if not validate:
        return None
    if earlier[0] == "station" and later[0] == "person":
        return ("holder_before", earlier[1], later[1])
    if earlier[0] == "person" and later[0] == "station":
        return ("holder_after", later[1], earlier[1])
    return None


def _between(obj: dict, header: Header, *, exact: bool) -> tuple | None:
    middle = header.match_name(obj.get("middle") or obj.get("person") or "", exact=exact)
    ends = _as_list(obj.get("ends") or obj.get("between") or [])
    if len(ends) != 2:
        ends = [obj.get("end1"), obj.get("end2")]
    people = [header.match_name(value, exact=exact) if isinstance(value, str) else None for value in ends]
    if middle is None or any(person is None for person in people):
        return None
    left, right = people
    if left is None or right is None:
        return None
    ordered = tuple(sorted((left, right)))
    return ("between", middle, ordered[0], ordered[1])


def votes_from_completion(
    text: str,
    finish: str,
    header: Header,
    n_lines: int,
    *,
    validate: bool,
    expected: set[int] | None = None,
    body: list[str] | None = None,
) -> dict[int, tuple] | None:
    """Map 0-based line index to a canonical constraint.

    A completion that does not parse at all is discarded (None), so it
    does not count as a vote that every line is filler. A parsed
    completion that simply omits a line votes none for that line. A
    truncated completion votes only for the lines it actually finished.
    `expected` limits the none-fill to lines this call was asked about,
    so a repair call cannot vote "none" on lines it never saw.
    """
    top, objects, truncated = payload_of(text, finish)
    if top is None and not objects:
        return None
    by_line: dict[int, tuple] = {}
    numbers: list[int] = []
    for obj in objects:
        number = _line_number(obj)
        if number is None:
            continue
        numbers.append(number)
        canon = canonical(obj, header, validate=validate)
        if canon is None:
            continue
        by_line[number] = canon
    if not by_line and not numbers and top is None:
        return None
    # Models occasionally count from 0. Shift only when the whole set
    # sits in 0..n-1 and includes 0.
    if numbers and 0 in by_line and n_lines not in by_line and max(numbers) <= n_lines - 1:
        by_line = {number + 1: canon for number, canon in by_line.items()}
    if validate and body is not None:
        for number, canon in list(by_line.items()):
            if 1 <= number <= len(body):
                by_line[number] = ground(canon, body[number - 1], header)
    votes: dict[int, tuple] = {}
    for number, canon in by_line.items():
        index = number - 1
        if 0 <= index < n_lines:
            votes[index] = canon
    if truncated:
        return votes
    # An explicit, complete translation that skips a line means none.
    # Repair calls only fill the lines they were shown.
    fill = range(n_lines) if expected is None else expected
    for index in fill:
        votes.setdefault(index, NONE)
    return votes


def roster_of(text: str) -> dict | None:
    top, _, _ = payload_of(text, "stop")
    if isinstance(top, dict) and isinstance(top.get("roster"), dict):
        return top["roster"]
    return None


def statement_core(line: str, header: Header) -> str:
    """Identity of a restatement: drop a short lead-in that names nobody.

    Two lines that say the same sentence after a hedge or a "noted twice"
    prefix share a core. The lead-in is detected by not containing a
    header token, not by a fixed list of hedge phrases.
    """
    text = " ".join(line.split())
    tokens = header.vocab_tokens()

    def mentions(blob: str) -> bool:
        return any(
            re.search(rf"\b{re.escape(token)}\b", blob, flags=re.IGNORECASE)
            for token in tokens
        )

    if ": " in text:
        head, tail = text.split(": ", 1)
        if len(head) < 60 and not mentions(head):
            text = tail
    if ", " in text:
        head, tail = text.split(", ", 1)
        if len(head.split()) <= 10 and not mentions(head):
            text = tail
    return " ".join(text.split()).casefold()


def _majority(counts: Counter) -> tuple[tuple, float, bool]:
    if not counts:
        return NONE, 0.0, False
    ranking = counts.most_common()
    canon, best = ranking[0]
    second = ranking[1][1] if len(ranking) > 1 else 0
    total = sum(counts.values())
    if best == second:
        # No unique reading. Dropping the line under-constrains the rota,
        # which surfaces as extra solutions rather than a false contradiction.
        return NONE, best / total, True
    return canon, best / total, False


def decide(
    board: VoteBoard,
    body: list[str],
    header: Header,
    *,
    dedupe: bool,
) -> tuple[list[Constraint], list[LineDecision]]:
    counters = [board.base[i] + board.repair[i] for i in range(board.n_lines)]
    if dedupe:
        groups: dict[str, list[int]] = defaultdict(list)
        for index, line in enumerate(body):
            groups[statement_core(line, header)].append(index)
        for indexes in groups.values():
            if len(indexes) < 2:
                continue
            pooled: Counter = Counter()
            for index in indexes:
                pooled += counters[index]
            for index in indexes:
                counters[index] = pooled

    decisions: list[LineDecision] = []
    constraints: list[Constraint] = []
    seen: set[str] = set()
    for index, line in enumerate(body):
        canon, agreement, disagreement = _majority(counters[index])
        decisions.append(
            LineDecision(
                canon=canon,
                agreement=agreement,
                disagreement=disagreement,
                base_n=sum(board.base[index].values()),
                repair_n=sum(board.repair[index].values()),
            )
        )
        if canon == NONE or not canon:
            continue
        if dedupe:
            key = statement_core(line, header)
            if key in seen:
                continue
            seen.add(key)
        constraints.append(
            Constraint(
                kind=canon[0],
                args=canon[1:],
                line=index,
                source=line,
                agreement=agreement,
            )
        )
    return constraints, decisions


def mentions_schedule_entity(line: str, header: Header) -> bool:
    """True when the line names a block time or a station, not merely a person."""
    for token in [*header.blocks, *header.stations]:
        if re.search(rf"\b{re.escape(token)}\b", line, flags=re.IGNORECASE):
            return True
    return False
