"""Shared flags and budget rules.

Components are independent on/off switches so an ablation can turn one
of them off without rewriting the pipeline. Sampling settings are not
flags: every model call uses the required temperature, top_p and
reasoning switch.
"""

from __future__ import annotations

from dataclasses import dataclass

COMPONENTS = (
    "gloss",
    "fewshot",
    "json_mode",
    "validate",
    "dedupe",
    "vote",
    "repair",
)

BUDGET_CAP = {"1x": 1, "3x": 3, "10x": 10}


@dataclass(frozen=True)
class Flags:
    """Which architectural components are active.

    `vote` and `repair` only change the call pattern when the budget
    allows more than one call. At 1x every item is exactly one call
    regardless of these two flags.
    """

    gloss: bool = True
    fewshot: bool = True
    json_mode: bool = True
    validate: bool = True
    dedupe: bool = True
    vote: bool = True
    repair: bool = True

    @classmethod
    def from_disabled(cls, disabled: list[str]) -> "Flags":
        unknown = [d for d in disabled if d and d not in COMPONENTS]
        if unknown:
            raise ValueError(
                f"unknown component(s): {', '.join(unknown)}; "
                f"known: {', '.join(COMPONENTS)}"
            )
        off = set(disabled)
        return cls(**{name: name not in off for name in COMPONENTS})


def allocation(budget: str, flags: Flags) -> tuple[int, int]:
    """Return (full-page votes, repair-call allowance) within the cap.

    Repair calls are spent only while some line is still uncertain, so
    the allowance is an upper bound. The sum never exceeds the cap.
    At 1x the allowance is (1, 0): exactly one call, no second pass.
    """
    if budget not in BUDGET_CAP:
        raise ValueError(f"budget must be one of {sorted(BUDGET_CAP)}")
    cap = BUDGET_CAP[budget]
    if cap == 1:
        return 1, 0
    if flags.vote and flags.repair:
        if cap == 3:
            return 2, 1
        return 4, cap - 4
    if flags.vote:
        return cap, 0
    if flags.repair:
        return 1, cap - 1
    return 1, 0
