"""Enumerate rota assignments and minimal unsatisfiable sets of constraints.

The search space is the bijection of people to blocks times the bijection
of station-holders to stations. Five people and three stations is a few
hundred candidates, so the solver is exhaustive rather than heuristic.
Satisfiability of a subset depends only on constraint meaning, never on
which item the constraints came from.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from wsolver.header import Header

KINDS = (
    "block_is",
    "block_not",
    "station_is",
    "station_not",
    "before",
    "adjacent",
    "between",
    "holder_before",
    "holder_after",
)

# Full subset enumeration is cheap up to this size. Beyond it we return
# one minimal core by deletion, dropping weakly-agreed constraints first.
_MAX_ENUM = 16


@dataclass(frozen=True)
class Constraint:
    kind: str
    args: tuple
    line: int
    source: str
    agreement: float = 1.0

    def semantic_key(self) -> tuple:
        return (self.kind, self.args)


@dataclass
class Analysis:
    solutions: list[tuple[dict[str, str], dict[str, str]]]
    cores: list[list[Constraint]]
    chosen_core: list[Constraint]
    case: str


def _holds(
    constraint: Constraint,
    blocks: dict[str, str],
    stations: dict[str, str] | None,
    block_index: dict[str, int],
    holder_of: dict[str, str] | None,
) -> bool:
    """True if this assignment satisfies the constraint.

    Station-dependent constraints are treated as still possible while
    `stations` is None, so block search can prune before assigning them.
    """
    kind, args = constraint.kind, constraint.args
    if kind == "block_is":
        return blocks.get(args[0]) == args[1]
    if kind == "block_not":
        return blocks.get(args[0]) != args[1]
    if kind == "before":
        return block_index[blocks[args[0]]] < block_index[blocks[args[1]]]
    if kind == "adjacent":
        return block_index[blocks[args[1]]] - block_index[blocks[args[0]]] == 1
    if kind == "between":
        middle = block_index[blocks[args[0]]]
        left = block_index[blocks[args[1]]]
        right = block_index[blocks[args[2]]]
        return min(left, right) < middle < max(left, right)
    if stations is None or holder_of is None:
        return True
    if kind == "station_is":
        return stations.get(args[0]) == args[1]
    if kind == "station_not":
        # Someone the header puts on no station holds nothing, so a ban
        # on a station for them is already true and does not bind.
        if args[0] not in stations:
            return True
        return stations.get(args[0]) != args[1]
    if kind == "holder_before":
        holder = holder_of.get(args[0])
        if holder is None:
            return False
        return block_index[blocks[holder]] < block_index[blocks[args[1]]]
    if kind == "holder_after":
        holder = holder_of.get(args[0])
        if holder is None:
            return False
        return block_index[blocks[args[1]]] < block_index[blocks[holder]]
    return False


def _block_assignments(header: Header, constraints: list[Constraint]):
    index = header.block_index()
    for perm in itertools.permutations(header.blocks):
        assigned = dict(zip(header.staff, perm))
        if all(_holds(c, assigned, None, index, None) for c in constraints):
            yield assigned


def iter_solutions(header: Header, constraints: list[Constraint]):
    index = header.block_index()
    for blocks in _block_assignments(header, constraints):
        for perm in itertools.permutations(header.stations):
            stations = dict(zip(header.holders, perm))
            holder_of = {station: person for person, station in stations.items()}
            if all(
                _holds(c, blocks, stations, index, holder_of) for c in constraints
            ):
                yield blocks, stations


def is_satisfiable(header: Header, constraints: list[Constraint]) -> bool:
    return next(iter_solutions(header, constraints), None) is not None


def enumerate_solutions(
    header: Header, constraints: list[Constraint]
) -> list[tuple[dict[str, str], dict[str, str]]]:
    return list(iter_solutions(header, constraints))


def _deletion_core(header: Header, constraints: list[Constraint]) -> list[Constraint]:
    """One minimal unsatisfiable subset. Low-agreement lines are dropped first."""
    core = list(constraints)
    ordered = sorted(core, key=lambda c: (c.agreement, c.line, c.source))
    for constraint in ordered:
        trial = [other for other in core if other is not constraint]
        if trial and not is_satisfiable(header, trial):
            core = trial
    return core


def minimal_cores(header: Header, constraints: list[Constraint]) -> list[list[Constraint]]:
    """Every minimal unsatisfiable subset, smallest sets first.

    A set is minimal when it is unsatisfiable and dropping any one
    constraint leaves something satisfiable. That is the citation rule.
    """
    if not constraints or is_satisfiable(header, constraints):
        return []
    if len(constraints) > _MAX_ENUM:
        return [_deletion_core(header, constraints)]

    found: list[tuple[int, ...]] = []
    size = len(constraints)
    for width in range(1, size + 1):
        for combo in itertools.combinations(range(size), width):
            if any(set(prev).issubset(combo) for prev in found):
                continue
            subset = [constraints[i] for i in combo]
            if not is_satisfiable(header, subset):
                found.append(combo)
    return [[constraints[i] for i in combo] for combo in found]


def select_core(cores: list[list[Constraint]]) -> list[Constraint]:
    """Prefer the core whose lines the model agreed on, then the smallest."""
    if not cores:
        return []

    def key(core: list[Constraint]) -> tuple:
        agreement = sum(c.agreement for c in core) / len(core)
        return (-agreement, len(core), tuple(sorted(c.line for c in core)))

    return list(min(cores, key=key))


def analyse(header: Header, constraints: list[Constraint]) -> Analysis:
    solutions = enumerate_solutions(header, constraints)
    if solutions:
        case = "unique" if len(solutions) == 1 else "ambiguous"
        return Analysis(solutions, [], [], case)
    cores = minimal_cores(header, constraints)
    chosen = select_core(cores)
    return Analysis(solutions, cores, chosen, "inconsistent")


def render_answer(header: Header, constraints: list[Constraint]) -> dict:
    """The submission object for one item, given already-chosen constraints."""
    result = analyse(header, constraints)
    if result.case == "inconsistent":
        conflicts: list[str] = []
        seen: set[str] = set()
        for constraint in sorted(result.chosen_core, key=lambda c: (c.line, c.source)):
            if constraint.source in seen:
                continue
            seen.add(constraint.source)
            conflicts.append(constraint.source)
        return {"case": "inconsistent", "conflicts": conflicts}

    holders = set(header.holders)

    def one(blocks: dict[str, str], stations: dict[str, str]) -> dict:
        assignment = {}
        for name in header.staff:
            if name in holders:
                assignment[name] = {"block": blocks[name], "station": stations[name]}
            else:
                assignment[name] = {"block": blocks[name]}
        return assignment

    rendered = [one(blocks, stations) for blocks, stations in result.solutions]
    rendered.sort(key=lambda row: tuple(row[name]["block"] for name in header.staff))
    if len(rendered) == 1:
        return {"case": "unique", "assignment": rendered[0]}
    return {"case": "ambiguous", "assignments": rendered}
