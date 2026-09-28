"""Read the rota header: who exists, which blocks exist, who holds a station.

The header is ground truth. Body lines are not interpreted here. A regex
covers a straightforward roster sentence; if that fails, the caller can
fall back to the roster the model copied out of the same page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


def _split_list(blob: str) -> list[str]:
    blob = blob.strip().rstrip(".")
    blob = re.sub(r"\s+and\s+", ", ", blob, flags=re.IGNORECASE)
    return [part.strip() for part in blob.split(",") if part.strip()]


@dataclass
class Header:
    staff: list[str]
    blocks: list[str]
    stations: list[str]
    holders: list[str]
    source: str = "regex"

    def __post_init__(self) -> None:
        self._name_cf = {n.casefold(): n for n in self.staff}
        self._station_cf = {s.casefold(): s for s in self.stations}
        self._block_cf = {b.casefold(): b for b in self.blocks}

    @property
    def complete(self) -> bool:
        return bool(
            self.staff
            and self.blocks
            and self.stations
            and self.holders
            and len(self.staff) == len(self.blocks)
            and len(self.holders) == len(self.stations)
            and all(h in self.staff for h in self.holders)
        )

    def block_index(self) -> dict[str, int]:
        return {b: i for i, b in enumerate(self.blocks)}

    def vocab_tokens(self) -> list[str]:
        return [*self.staff, *self.blocks, *self.stations]

    def match_name(self, text: object, *, exact: bool = False) -> str | None:
        return _match(text, self.staff, self._name_cf, exact=exact)

    def match_station(self, text: object, *, exact: bool = False) -> str | None:
        return _match(text, self.stations, self._station_cf, exact=exact)

    def match_block(self, text: object, *, exact: bool = False) -> str | None:
        if not isinstance(text, str):
            return None
        raw = text.strip()
        if exact:
            return raw if raw in self.blocks else None
        hit = self._block_cf.get(raw.casefold())
        if hit:
            return hit
        found = re.search(r"\d{1,2}:\d{2}", raw)
        if not found:
            return None
        token = found.group(0)
        for block in self.blocks:
            if block == token or block.lstrip("0") == token.lstrip("0"):
                return block
        return None


def _match(
    text: object,
    values: list[str],
    folded: dict[str, str],
    *,
    exact: bool,
) -> str | None:
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    if exact:
        return raw if raw in values else None
    hit = folded.get(raw.casefold())
    if hit:
        return hit
    # "Alice's block" still names Alice. Only accept when exactly one
    # vocabulary item appears, so "Alice and Bob" is not silently clipped.
    hits = [
        value
        for value in values
        if re.search(rf"\b{re.escape(value)}\b", raw, flags=re.IGNORECASE)
    ]
    if len(hits) == 1 and len(raw) <= len(hits[0]) + 16:
        return hits[0]
    return None


_STAFF = re.compile(
    r"(\d+)\s+staff on the rota:\s*([^.]+)\.",
    re.IGNORECASE,
)
_BLOCKS = re.compile(
    r"blocks run\s+((?:\d{1,2}:\d{2}\s*,\s*)*\d{1,2}:\d{2})",
    re.IGNORECASE,
)
_STATIONS = re.compile(r"one person on each:\s*([^.]+)\.", re.IGNORECASE)
_STATIONS_ALT = re.compile(
    r"\bstations(?:\s+are|:)\s*([^.]+)\.",
    re.IGNORECASE,
)
_HOLDERS = re.compile(
    r"(?:people|staff|those) on a station are\s+([^.;]+)",
    re.IGNORECASE,
)
_HOLDERS_ALT = re.compile(
    r"station[- ]holders(?:\s+are|:)\s+([^.;]+)",
    re.IGNORECASE,
)


def split_notes(text: str) -> tuple[str, list[str]]:
    """Header paragraph, then every non-empty body line in order."""
    lines = text.split("\n")
    index = 0
    header_lines: list[str] = []
    while index < len(lines) and lines[index].strip():
        header_lines.append(lines[index].strip())
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    body = [line.strip() for line in lines[index:] if line.strip()]
    return " ".join(header_lines), body


def _remap(holders: list[str], staff: list[str]) -> list[str] | None:
    out: list[str] = []
    folded = {name.casefold(): name for name in staff}
    for holder in holders:
        hit = folded.get(holder.casefold())
        if hit is None:
            return None
        out.append(hit)
    return out


def _build(
    staff: list[str],
    blocks: list[str],
    stations: list[str],
    holders: list[str],
    source: str,
) -> Header | None:
    mapped = _remap(holders, staff)
    if mapped is None:
        return None
    header = Header(
        staff=staff,
        blocks=blocks,
        stations=stations,
        holders=mapped,
        source=source,
    )
    if not header.complete:
        return None
    if len(set(staff)) != len(staff) or len(set(blocks)) != len(blocks):
        return None
    if len(set(stations)) != len(stations) or len(set(mapped)) != len(mapped):
        return None
    return header


def parse_header_text(header_text: str) -> Header | None:
    staff_m = _STAFF.search(header_text)
    blocks_m = _BLOCKS.search(header_text)
    stations_m = _STATIONS.search(header_text) or _STATIONS_ALT.search(header_text)
    holders_m = _HOLDERS.search(header_text) or _HOLDERS_ALT.search(header_text)
    if not (staff_m and blocks_m and stations_m and holders_m):
        return None
    declared = int(staff_m.group(1))
    staff = _split_list(staff_m.group(2))
    if declared != len(staff):
        return None
    blocks = [part.strip() for part in blocks_m.group(1).split(",")]
    stations = _split_list(stations_m.group(1))
    holders = _split_list(holders_m.group(1))
    return _build(staff, blocks, stations, holders, "regex")


def header_from_roster(
    roster: object,
    *,
    n_staff: int | None = None,
    n_stations: int | None = None,
) -> Header | None:
    """Roster copied by the model. Used only when the header regex fails."""
    if not isinstance(roster, dict):
        return None

    def names(*keys: str) -> list[str]:
        for key in keys:
            value = roster.get(key)
            if isinstance(value, list) and value:
                return [str(item).strip() for item in value if str(item).strip()]
        return []

    staff = names("staff", "names", "rota")
    blocks = names("blocks")
    stations = names("stations")
    holders = names("on_station", "holders", "station_holders")
    if n_staff is not None and len(staff) != n_staff:
        return None
    if n_stations is not None and len(stations) != n_stations:
        return None
    return _build(staff, blocks, stations, holders, "model")


def resolve_header(
    text: str,
    roster: object = None,
    *,
    n_staff: int | None = None,
    n_stations: int | None = None,
) -> Header | None:
    """Prefer the header sentence. Fall back to a model-copied roster."""
    header_text, _ = split_notes(text)
    parsed = parse_header_text(header_text)
    if parsed is not None:
        if n_staff is not None and len(parsed.staff) != n_staff:
            parsed = None
        elif n_stations is not None and len(parsed.stations) != n_stations:
            parsed = None
    if parsed is not None:
        return parsed
    return header_from_roster(roster, n_staff=n_staff, n_stations=n_stations)
