"""End-to-end baseline: the model emits the final JSON itself.

No constraint schema and no solver. Extra budget, when voting is on, is
spent on independent samples of that same answer. This is the reference
arm an ablation compares the architecture against, not a submission mode.
"""

from __future__ import annotations

import json
from collections import Counter

from wsolver.extract import _loads_first
from wsolver.header import split_notes
from wsolver.types import BUDGET_CAP, Flags

CASES = ("unique", "ambiguous", "inconsistent")

NAIVE_SYSTEM = """You solve a shift-notes rota and answer with one JSON object.

Each person works exactly one time block. Blocks are listed earliest to latest in the header, one person per block. The header names who holds a station; each of them holds exactly one, and everyone else holds none. Give "station" only for the people the header puts on a station, and name every person.

A hedge ("I'm fairly sure", "as far as I know", "my recollection is", "speaking from memory", "going off the roster") is not doubt. Read the sentence as if the hedge were absent. Wishes, the past, and questions nobody resolved are not assignments.

Exactly one of these cases is right:
- unique: one assignment fits. {"case": "unique", "assignment": {"<name>": {"block": "<time>", "station": "<station>"}, "<name with no station>": {"block": "<time>"}}}
- ambiguous: more than one assignment fits. Return every one. {"case": "ambiguous", "assignments": [ {assignment}, {assignment} ]}
- inconsistent: the notes contradict each other. Cite a minimal set of lines that cannot all be true, such that dropping any one of them would leave the rest possible. Quote each line in full. {"case": "inconsistent", "conflicts": ["<full line>", "<full line>"]}

Copy names, times and stations exactly as the notes spell them. Output only the JSON object."""


def naive_messages(text: str) -> list[dict]:
    header, body = split_notes(text)
    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(body, start=1))
    return [
        {"role": "system", "content": NAIVE_SYSTEM},
        {
            "role": "user",
            "content": f"Header: {header}\n\n{numbered}",
        },
    ]


def interpret_naive(text: str) -> dict | None:
    parsed = _loads_first(text)
    if not isinstance(parsed, dict):
        return None
    if parsed.get("case") in CASES:
        return parsed
    # A bare assignment object is a unique claim, matching the scorer.
    if parsed and all(isinstance(value, dict) and "block" in value for value in parsed.values()):
        return {"case": "unique", "assignment": parsed}
    return None


def _fingerprint(answer: dict) -> str:
    case = answer.get("case")
    if case == "unique":
        return json.dumps(answer.get("assignment"), sort_keys=True, ensure_ascii=False)
    if case == "ambiguous":
        rows = answer.get("assignments") or []
        encoded = [json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows if isinstance(row, dict)]
        return json.dumps(sorted(encoded), ensure_ascii=False)
    conflicts = answer.get("conflicts") or answer.get("conflict_lines") or []
    return json.dumps(list(conflicts), ensure_ascii=False)


def _vote(answers: list[dict]) -> dict:
    if not answers:
        return {"case": "ambiguous", "assignments": []}
    counts = Counter(answer["case"] for answer in answers)
    best = max(counts.values())
    tied = [case for case, count in counts.items() if count == best]
    # A tie between cases is reported as ambiguous: extra solutions rather
    # than a silent unique guess.
    case = "ambiguous" if len(tied) > 1 else tied[0]
    subset = [answer for answer in answers if answer["case"] == case]
    # The samples disagreed about the case and none of them said
    # ambiguous. Report that disagreement instead of crashing.
    if not subset:
        return {"case": "ambiguous", "assignments": []}
    winner = _most_common(subset)
    if case == "unique":
        return {"case": "unique", "assignment": winner.get("assignment") or {}}
    if case == "ambiguous":
        return {"case": "ambiguous", "assignments": winner.get("assignments") or []}
    conflicts = winner.get("conflicts")
    if conflicts is None:
        conflicts = winner.get("conflict_lines") or []
    return {"case": "inconsistent", "conflicts": list(conflicts)}


def _most_common(answers: list[dict]) -> dict:
    counts = Counter(_fingerprint(answer) for answer in answers)
    best = max(counts.values())
    for answer in answers:
        if counts[_fingerprint(answer)] == best:
            return answer
    return answers[0]


def solve_naive(item: dict, budget: str, flags: Flags, client) -> tuple[dict, dict]:
    cap_samples = BUDGET_CAP[budget] if flags.vote else 1
    if budget == "1x":
        cap_samples = 1
    messages = naive_messages(item.get("text") or "")
    raw: list[dict] = []
    parsed: list[dict] = []
    while len(raw) < cap_samples and client.calls < client.cap:
        try:
            content, finish = client.complete(
                messages, json_mode=flags.json_mode, max_tokens=2500
            )
        except Exception as exc:
            raw.append({"error": f"{type(exc).__name__}: {exc}"})
            break
        raw.append({"content": content, "finish": finish})
        answer = interpret_naive(content)
        if answer is not None:
            parsed.append(answer)
    debug = {"id": item.get("id"), "calls": client.calls, "responses": raw, "mode": "naive"}
    return _vote(parsed), debug
