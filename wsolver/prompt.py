"""Prompt for per-line constraint translation.

The worked example uses a made-up facility (different names, blocks and
stations from the task). It teaches the meaning of each type, including
the phrasings a small model otherwise collapses: order versus adjacency,
a station's unnamed holder versus a named person, and a hedged fact
versus a wish, a memory or an open question.
"""

from __future__ import annotations

import json

FEWSHOT_HEADER = (
    "Shift notes, Bay 2. 5 staff on the rota: Alice, Bob, Carla, Dev, Eve. "
    "Blocks run 08:00, 10:00, 12:00, 14:00, 16:00, one person per block, and "
    "each person works exactly one block. There are 3 stations, one person on "
    "each: intake, packing, dispatch. The people on a station are Alice, "
    "Carla, Eve; the rest are on no station."
)

# (line text, gloss, object without line/gloss). Field order is the order
# we want the model to emit.
_EXAMPLE_LINES: list[tuple[str, str, dict]] = [
    (
        "Speaking from memory, Carla is the one holding intake.",
        "Carla holds intake",
        {"type": "station_is", "person": "Carla", "station": "intake"},
    ),
    (
        "Bob and Carla both complained about the coffee.",
        "chatter about coffee, nobody is placed",
        {"type": "none"},
    ),
    (
        "Bob comes on directly after Alice finishes.",
        "Bob's block is immediately after Alice's",
        {"type": "adjacent", "earlier": "Alice", "later": "Bob"},
    ),
    (
        "Alice had been hoping for the 16:00 block but it went to someone else.",
        "a wish Alice did not get; the line does not say where she is",
        {"type": "none"},
    ),
    (
        "Whoever has packing is finished before Bob arrives.",
        "the unnamed packing holder works earlier than Bob",
        {"type": "holder_order", "station": "packing", "person": "Bob", "person_is": "later"},
    ),
    (
        "Bob's slot is not the 08:00 one.",
        "Bob is not on 08:00",
        {"type": "block_not", "person": "Bob", "block": "08:00"},
    ),
    (
        "Carla sits somewhere between Alice and Bob in the running order.",
        "Carla is between Alice and Bob",
        {"type": "between", "middle": "Carla", "ends": ["Alice", "Bob"]},
    ),
    (
        "Bob is on after one of Dev and Carla and before the other.",
        "Bob is between Dev and Carla",
        {"type": "between", "middle": "Bob", "ends": ["Dev", "Carla"]},
    ),
    (
        "It bears repeating: Alice is on later than Dev.",
        "Dev's block is earlier than Alice's",
        {"type": "before", "earlier": "Dev", "later": "Alice"},
    ),
    (
        "Eve was on packing back in the winter.",
        "a past arrangement, not the current rota",
        {"type": "none"},
    ),
    (
        "Packing is covered by someone other than Alice.",
        "Alice does not hold packing",
        {"type": "station_not", "person": "Alice", "station": "packing"},
    ),
    (
        "Whoever drew the 16:00 block, it was Dev.",
        "Dev is on 16:00",
        {"type": "block_is", "person": "Dev", "block": "16:00"},
    ),
    (
        "Alice then Eve, back to back.",
        "Eve's block is immediately after Alice's",
        {"type": "adjacent", "earlier": "Alice", "later": "Eve"},
    ),
    (
        "The person on intake precedes Carla.",
        "the unnamed intake holder works earlier than Carla, not necessarily immediately",
        {"type": "holder_order", "station": "intake", "person": "Carla", "person_is": "later"},
    ),
    (
        "Speaking from memory, Dev is done before Bob starts.",
        "Dev's block is earlier than Bob's",
        {"type": "before", "earlier": "Dev", "later": "Bob"},
    ),
    (
        "Eve has not been put on packing.",
        "Eve does not hold packing",
        {"type": "station_not", "person": "Eve", "station": "packing"},
    ),
    (
        "Nobody could remember whether Alice was down for 12:00.",
        "an unresolved question, not an assignment",
        {"type": "none"},
    ),
    (
        "Alice hands straight over to Eve.",
        "Eve's block is immediately after Alice's",
        {"type": "adjacent", "earlier": "Alice", "later": "Eve"},
    ),
    (
        "There is no block between Alice's and Bob's, in that order.",
        "Bob's block is immediately after Alice's",
        {"type": "adjacent", "earlier": "Alice", "later": "Bob"},
    ),
    (
        "Carla works earlier in the day than whoever has dispatch.",
        "Carla works earlier than the unnamed dispatch holder",
        {"type": "holder_order", "station": "dispatch", "person": "Carla", "person_is": "earlier"},
    ),
    (
        "Eve takes over from Alice later in the day.",
        "Alice's block is earlier than Eve's",
        {"type": "before", "earlier": "Alice", "later": "Eve"},
    ),
    (
        "Put Dev between Alice and Bob, though not necessarily next to either.",
        "Dev is between Alice and Bob",
        {"type": "between", "middle": "Dev", "ends": ["Alice", "Bob"]},
    ),
    (
        "Bob is unavailable at 10:00.",
        "Bob is not on 10:00",
        {"type": "block_not", "person": "Bob", "block": "10:00"},
    ),
    (
        "10:00 is when Bob is scheduled.",
        "Bob is on 10:00",
        {"type": "block_is", "person": "Bob", "block": "10:00"},
    ),
    (
        "Bob put in for the 14:00 block and was turned down.",
        "a request that was refused; it does not say which block Bob is on",
        {"type": "none"},
    ),
]


def _system(gloss: bool) -> str:
    gloss_rule = (
        'Put a short "gloss" first: the line with the hedge and any lead-in removed, '
        "keeping every person, time and station exactly as written. Then set the type.\n"
        if gloss
        else "Do not add a gloss. Set the type directly.\n"
    )
    gloss_field = '"gloss": "...", ' if gloss else ""
    return f"""You translate one page of shift notes into scheduling constraints. You do not solve the rota. You never drop, merge or soften a line because it might conflict with another line. Translate every numbered line on its own.

Domain
- Each person works exactly one time block. Blocks run earlier-to-later in the order the header lists, one person per block.
- The header names the people who hold a station. Each of them holds exactly one station and each station is held by exactly one of them. Everyone else holds no station.

For every numbered line write one JSON object. {gloss_rule}
Types, with these exact field names
- {{"line": n, {gloss_field}"type": "block_is", "person": P, "block": B}} — P is on block B.
- {{"line": n, {gloss_field}"type": "block_not", "person": P, "block": B}} — P is not on block B.
- {{"line": n, {gloss_field}"type": "station_is", "person": P, "station": S}} — P holds station S.
- {{"line": n, {gloss_field}"type": "station_not", "person": P, "station": S}} — P does not hold station S.
- {{"line": n, {gloss_field}"type": "before", "earlier": P, "later": Q}} — P's block is earlier in the day than Q's. A gap is allowed. "Precedes", "earlier than", "before", "is done before Q starts" and "by the time Q starts, P has already been on" are before, not adjacent.
- {{"line": n, {gloss_field}"type": "adjacent", "earlier": P, "later": Q}} — Q's block is the very next one after P's. Use this only when the blocks touch: directly after, immediately before, back to back, hands straight over, relieves directly, no block in between. "A then B" means earlier is A and later is B.
- {{"line": n, {gloss_field}"type": "between", "middle": P, "ends": [Q, R]}} — P is strictly between Q and R, in either order, not necessarily beside either. "P is on after one of Q and R and before the other" is between.
- {{"line": n, {gloss_field}"type": "holder_order", "station": S, "person": P, "person_is": "later"}} — P works later than the unnamed person who holds station S. Set "person_is" to "earlier" when P works earlier than that holder. "Whoever has S", "the person on S" and "whoever is on S" mean that unnamed holder. This does not say which station P holds, and S is not a person's name. "P is later than whoever has S" is "later". "Whoever has S is finished before P" is also "later". "P works earlier than whoever has S" is "earlier".
- {{"line": n, {gloss_field}"type": "none"}} — the line asserts nothing about who is on which block or station on the current rota.

How to read a line
- A hedge is not doubt. Phrases such as "I'm fairly sure", "as far as I know", "my recollection is", "going off the roster", "speaking from memory" and "I believe" change nothing. Translate the sentence as if the hedge were not there.
- A lead-in such as "it bears repeating", "noted twice" or "worth restating" changes nothing. Translate the sentence after the colon.
- Negation is a real constraint. "not on", "has not been put on", "not assigned to", "ruled out", "is not someone's station" and "covered by someone other than" are block_not or station_not.
- Naming a person and a time does not by itself make a constraint. The following are none, because they do not state the current rota:
  - chatter that places nobody, such as keys, shared rides, surveys, training, deliveries, catering, lost property or who sat on a panel;
  - the past. A line is about the past only when it names a past time, such as last month, last week, yesterday, in the spring, a previous cycle, the old arrangement, or "used to". "X is done before Y starts" and "by the time Y starts, X has already been on" order today's blocks; they are not the past;
  - wishes and hypotheticals, such as wanted, asked, lobbied, was turned down, would have been better, "if they had taken" or "had it gone the other way";
  - anything unresolved, such as left open, nothing written down, nobody could remember, or a disagreement about whether.
- Copy person names, block times and station names exactly as this page's header spells them. Never invent one, and never reuse a name from the worked example unless the header actually lists it.

Output one JSON object and nothing else:
{{"roster": {{"staff": [...], "blocks": [...], "stations": [...], "on_station": [...]}}, "constraints": [one object per numbered line, in order]}}"""


def _example_object(index: int, gloss_text: str, fields: dict, *, gloss: bool) -> dict:
    obj: dict = {"line": index}
    if gloss:
        obj["gloss"] = gloss_text
    obj.update(fields)
    return obj


def _fewshot_messages(gloss: bool) -> list[dict]:
    numbered = "\n".join(
        f"{i}. {text}" for i, (text, _, _) in enumerate(_EXAMPLE_LINES, start=1)
    )
    roster = {
        "staff": ["Alice", "Bob", "Carla", "Dev", "Eve"],
        "blocks": ["08:00", "10:00", "12:00", "14:00", "16:00"],
        "stations": ["intake", "packing", "dispatch"],
        "on_station": ["Alice", "Carla", "Eve"],
    }
    constraints = [
        _example_object(i, gloss_text, fields, gloss=gloss)
        for i, (_, gloss_text, fields) in enumerate(_EXAMPLE_LINES, start=1)
    ]
    assistant = json.dumps(
        {"roster": roster, "constraints": constraints},
        ensure_ascii=False,
        indent=2,
    )
    user = f"Header: {FEWSHOT_HEADER}\n\n{numbered}"
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


def messages_for(
    header_text: str,
    body: list[str],
    *,
    gloss: bool,
    fewshot: bool,
) -> list[dict]:
    msgs = [{"role": "system", "content": _system(gloss)}]
    if fewshot:
        msgs.extend(_fewshot_messages(gloss))
    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(body, start=1))
    msgs.append(
        {
            "role": "user",
            "content": (
                f"Header: {header_text}\n\n"
                f"There are {len(body)} lines. Translate each one.\n\n"
                f"{numbered}"
            ),
        }
    )
    return msgs


def repair_messages_for(
    header_text: str,
    body: list[str],
    line_indexes: list[int],
    *,
    gloss: bool,
    fewshot: bool,
) -> list[dict]:
    """Same translation task, restricted to lines that are still uncertain.

    The wording deliberately does not ask the model to restore consistency.
    A real contradiction has to stay a contradiction.
    """
    msgs = [{"role": "system", "content": _system(gloss)}]
    if fewshot:
        msgs.extend(_fewshot_messages(gloss))
    numbered = "\n".join(f"{i + 1}. {body[i]}" for i in line_indexes)
    msgs.append(
        {
            "role": "user",
            "content": (
                f"Header: {header_text}\n\n"
                "Translate only the lines below. The numbers are their original "
                "line numbers. Judge each line on its own, the same way you would "
                "inside a full page. Do not try to make the lines agree with each "
                "other, and do not skip a line because it looks inconsistent.\n\n"
                f"{numbered}"
            ),
        }
    )
    return msgs
