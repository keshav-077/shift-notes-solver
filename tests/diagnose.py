"""Compare a logged run with the dev-only oracle. Not part of answering.

    python tests/diagnose.py --log runs/1x --items candidate_package/items.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.oracle import oracle_constraints  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--items", type=Path, required=True)
    args = parser.parse_args()
    items = json.loads(args.items.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in items}
    line_right = line_total = 0
    missed = Counter()
    invented = Counter()
    items_perfect = items_scored = 0
    for path in sorted(args.log.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        item = by_id.get(payload.get("id") or path.stem)
        if item is None or "lines" not in payload:
            continue
        items_scored += 1
        _, constraints = oracle_constraints(item)
        gold = {c.line: (c.kind, *c.args) for c in constraints}
        perfect = True
        for row in payload["lines"]:
            index = row["line"]
            want = gold.get(index, ("none",))
            got = tuple(row["canon"])
            line_total += 1
            if got == want:
                line_right += 1
                continue
            perfect = False
            if want == ("none",):
                invented[got[0]] += 1
            else:
                missed[(want[0], got[0])] += 1
            text = row["text"][:90]
            print(f"{item['id']} L{index + 1} want={want} got={got} | {text}")
        items_perfect += perfect
    print(
        f"\nlines {line_right}/{line_total} "
        f"({(line_right / line_total if line_total else 0):.3f}) "
        f"items perfect {items_perfect}/{items_scored}"
    )
    print("missed (gold -> predicted)", dict(missed))
    print("invented from filler", dict(invented))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
