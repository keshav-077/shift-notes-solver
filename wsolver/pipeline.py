"""Run one item: translate with the weak model, then solve symbolically.

Call pattern by budget, with every component on:
  1x   exactly one full-page translation
  3x   two full-page translations, then one repair call if a line is uncertain
  10x  four full-page translations, then up to six repair calls

A repair call re-translates only the uncertain lines. It is another vote,
not a request to remove a contradiction. The answer is a pure function of
those translations plus the header.
"""

from __future__ import annotations

from wsolver.extract import (
    NONE,
    VoteBoard,
    decide,
    mentions_schedule_entity,
    roster_of,
    votes_from_completion,
)
from wsolver.header import resolve_header, split_notes
from wsolver.naive import solve_naive
from wsolver.prompt import messages_for, repair_messages_for
from wsolver.solver import analyse, render_answer
from wsolver.types import BUDGET_CAP, Flags, allocation

# One repair call stays short enough that a small model can finish it.
MAX_REPAIR_LINES = 12
FULL_MAX_TOKENS = 3000
REPAIR_MAX_TOKENS = 2000


def choose_flagged(body, header, board: VoteBoard, decisions, core_lines: set[int]) -> list[int]:
    """Lines worth a second look, most urgent first.

    Disagreement first, then a contradiction we are about to cite and have
    not yet re-read, then a "none" that still names a block or a station
    and so might be a missed constraint. A line that merely names a person
    is not re-read: most chatter does.
    """
    scored: list[tuple[int, int, int]] = []
    for index, decision in enumerate(decisions):
        priority = None
        # A single isolated re-read is not allowed to keep the item open
        # until it outvotes the page. On the 10x log, when the two
        # disagreed, the page was right 23 times and the re-read 8, and
        # the usual re-read error was to drop a real constraint.
        if decision.disagreement:
            priority = 0
        elif index in core_lines and decision.repair_n == 0:
            priority = 1
        elif (
            decision.canon == NONE
            and decision.repair_n == 0
            and mentions_schedule_entity(body[index], header)
        ):
            priority = 2
        if priority is not None:
            scored.append((priority, decision.repair_n, index))
    scored.sort()
    return [index for _, _, index in scored[:MAX_REPAIR_LINES]]


def _fallback(reason: str) -> dict:
    # Parses, fails the item, and does not pretend to a unique rota.
    return {"case": "ambiguous", "assignments": [], "error": reason}


def _public(answer: dict) -> dict:
    """Drop diagnostic keys so the submission matches the scored schema."""
    case = answer.get("case")
    if case == "unique":
        return {"case": "unique", "assignment": answer.get("assignment") or {}}
    if case == "ambiguous":
        return {"case": "ambiguous", "assignments": answer.get("assignments") or []}
    if case == "inconsistent":
        return {"case": "inconsistent", "conflicts": answer.get("conflicts") or []}
    return {"case": "ambiguous", "assignments": []}


def solve_item(item: dict, budget: str, flags: Flags, client, *, mode: str = "extract") -> tuple[dict, dict]:
    if mode == "naive":
        return solve_naive(item, budget, flags, client)
    if budget not in BUDGET_CAP:
        raise ValueError(budget)

    text = item.get("text") or ""
    header_text, body = split_notes(text)
    n_staff = item.get("n_staff")
    n_stations = item.get("n_stations")
    header = resolve_header(text, n_staff=n_staff, n_stations=n_stations)
    n_votes, n_repair = allocation(budget, flags)
    raw: list[dict] = []

    def call(messages: list[dict], max_tokens: int, kind: str) -> tuple[str, str] | None:
        if client.calls >= client.cap:
            return None
        try:
            content, finish = client.complete(
                messages, json_mode=flags.json_mode, max_tokens=max_tokens
            )
        except Exception as exc:
            raw.append({"kind": kind, "error": f"{type(exc).__name__}: {exc}"})
            return None
        raw.append({"kind": kind, "content": content, "finish": finish})
        return content, finish

    page = messages_for(header_text, body, gloss=flags.gloss, fewshot=flags.fewshot)
    vote_records: list[tuple[str, str]] = []
    while len(vote_records) < n_votes:
        got = call(page, FULL_MAX_TOKENS, "vote")
        if got is None:
            break
        vote_records.append(got)
        if header is None:
            header = resolve_header(
                text,
                roster_of(got[0]),
                n_staff=n_staff,
                n_stations=n_stations,
            )

    if client.calls < 1:
        raise RuntimeError(f"{item.get('id')}: no model call was made")

    debug = {
        "id": item.get("id"),
        "calls": client.calls,
        "responses": raw,
        "mode": mode,
    }
    if header is None or not body:
        debug["calls"] = client.calls
        debug["error"] = "no_header" if header is None else "no_body"
        return _public(_fallback(debug["error"])), debug

    board = VoteBoard(len(body))
    for content, finish in vote_records:
        votes = votes_from_completion(
            content,
            finish,
            header,
            len(body),
            validate=flags.validate,
            body=body,
        )
        if votes:
            board.add(votes, repair=False)

    repairs_done = 0
    while repairs_done < n_repair and client.calls < client.cap:
        constraints, decisions = decide(board, body, header, dedupe=flags.dedupe)
        core_lines = {c.line for c in analyse(header, constraints).chosen_core}
        flagged = choose_flagged(body, header, board, decisions, core_lines)
        if not flagged:
            break
        repair = repair_messages_for(
            header_text,
            body,
            flagged,
            gloss=flags.gloss,
            fewshot=flags.fewshot,
        )
        got = call(repair, REPAIR_MAX_TOKENS, "repair")
        repairs_done += 1
        if got is None:
            break
        votes = votes_from_completion(
            got[0],
            got[1],
            header,
            len(body),
            validate=flags.validate,
            expected=set(flagged),
            body=body,
        )
        if votes:
            board.add(votes, repair=True)

    constraints, decisions = decide(board, body, header, dedupe=flags.dedupe)
    answer = render_answer(header, constraints)
    debug["calls"] = client.calls
    debug["header_source"] = header.source
    debug["n_constraints"] = len(constraints)
    debug["lines"] = [
        {
            "line": index,
            "text": body[index],
            "canon": list(decision.canon),
            "agreement": round(decision.agreement, 3),
            "disagreement": decision.disagreement,
            "base_n": decision.base_n,
            "repair_n": decision.repair_n,
        }
        for index, decision in enumerate(decisions)
    ]
    debug["case"] = answer["case"]
    if answer["case"] == "inconsistent":
        debug["n_conflicts"] = len(answer["conflicts"])
    elif answer["case"] == "ambiguous":
        debug["n_solutions"] = len(answer["assignments"])
    else:
        debug["n_solutions"] = 1
    return answer, debug
