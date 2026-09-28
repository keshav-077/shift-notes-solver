#!/usr/bin/env python3
"""Re-run the system with one component removed and score every arm.

    python ablate.py --items candidate_package/items.json \\
        --key candidate_package/visible_key.json --budgets 1x,3x

Each arm is a real `run.py` invocation, which is what a re-run executes.
Post-processing arms (validate, dedupe) can be replayed from a logged run
with --replay-from so they do not spend a second set of model calls; the
model outputs stay identical and only the removed component changes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "candidate_package"))

from score import score  # noqa: E402

from wsolver.types import COMPONENTS

# Arms that only change how logged completions are interpreted. Replaying
# them keeps the model output fixed, which is the fair ablation.
REPLAYABLE = ("validate", "dedupe")


def _run(cmd: list[str]) -> None:
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def _score(key_path: Path, answers: Path, items: Path) -> dict:
    key = json.loads(key_path.read_text(encoding="utf-8"))
    sub = json.loads(answers.read_text(encoding="utf-8"))
    item_list = json.loads(items.read_text(encoding="utf-8"))
    result = score(key, sub, item_list)
    result.pop("per_item", None)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--budgets", default="1x,3x,10x")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "ablation")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--replay-from",
        type=Path,
        default=None,
        help="Log dir of a full run. validate/dedupe arms replay it instead of calling the model.",
    )
    parser.add_argument("--include-naive", action="store_true")
    args = parser.parse_args(argv)

    budgets = [part.strip() for part in args.budgets.split(",") if part.strip()]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    python = sys.executable

    for budget in budgets:
        for repeat in range(1, args.repeats + 1):
            tag = f"{budget}-full-r{repeat}"
            out = args.out_dir / f"{tag}.json"
            log = args.out_dir / "logs" / tag
            if not out.exists():
                _run(
                    [
                        python, "run.py", str(args.items),
                        "--budget", budget,
                        "--out", str(out),
                        "--workers", str(args.workers),
                        "--log-dir", str(log),
                    ]
                )
            result = _score(args.key, out, args.items)
            rows.append({
                "arm": "full",
                "budget": budget,
                "repeat": repeat,
                "runs": 1,
                "how": "live",
                **_brief(result),
            })

        for component in COMPONENTS:
            if budget == "10x" and component not in {"vote", "repair", "validate", "dedupe"}:
                continue
            tag = f"{budget}-no-{component}"
            out = args.out_dir / f"{tag}.json"
            log = args.out_dir / "logs" / tag
            how = "live"
            replay_root = args.replay_from
            if component in REPLAYABLE and replay_root is not None:
                source = replay_root / f"{budget}-full-r1"
                if not source.exists():
                    source = replay_root
                how = f"replay of {source}"
            if out.exists():
                result = _score(args.key, out, args.items)
                rows.append({
                    "arm": f"no-{component}",
                    "budget": budget,
                    "repeat": 1,
                    "runs": 1,
                    "how": how,
                    **_brief(result),
                })
                continue
            cmd = [
                python, "run.py", str(args.items),
                "--budget", budget,
                "--disable", component,
                "--out", str(out),
                "--workers", str(args.workers),
                "--log-dir", str(log),
            ]
            if how != "live":
                cmd.extend(["--replay-dir", how.removeprefix("replay of ")])
            _run(cmd)
            result = _score(args.key, out, args.items)
            rows.append({
                "arm": f"no-{component}",
                "budget": budget,
                "repeat": 1,
                "runs": 1,
                "how": how,
                **_brief(result),
            })

        if args.include_naive:
            tag = f"{budget}-naive"
            out = args.out_dir / f"{tag}.json"
            if not out.exists():
                _run(
                    [
                        python, "run.py", str(args.items),
                        "--budget", budget,
                        "--mode", "naive",
                        "--out", str(out),
                        "--workers", str(args.workers),
                    ]
                )
            result = _score(args.key, out, args.items)
            rows.append({
                "arm": "naive",
                "budget": budget,
                "repeat": 1,
                "runs": 1,
                "how": "live",
                **_brief(result),
            })

    (args.out_dir / "results.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    # The root ABLATION.md is the scored table cited by the writeup.
    # A re-run writes its own copy here and does not replace that file.
    written = args.out_dir / "ABLATION.md"
    _write_markdown(written, rows, args.repeats)
    print(f"wrote {written}")
    return 0


def _brief(result: dict) -> dict:
    return {
        "macro_exact_match": result["macro_exact_match"],
        "per_case_rate": result["per_case_rate"],
        "exact_match": result["exact_match"],
        "ambiguous_partial": result["ambiguous_partial"],
        "inconsistent_label_only": result["inconsistent_label_only"],
        "confusion": result["confusion"],
    }


def _write_markdown(path: Path, rows: list[dict], repeats: int) -> None:
    lines = [
        "# Ablation",
        "",
        "Headline is macro exact match: the mean of the unique, ambiguous and",
        "inconsistent rates. Each arm is `run.py` with one component disabled.",
        f"Repeats requested: {repeats}. Sampling is temperature 1.0, so arms",
        "are not expected to match to the third decimal; the table is the run",
        "that was actually scored.",
        "",
        "| arm | budget | runs | how | macro | unique | ambiguous | inconsistent | ambiguous_partial | inconsistent_label_only |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        rates = row["per_case_rate"]
        lines.append(
            "| {arm} | {budget} | {runs} | {how} | {macro:.3f} | {u} | {a} | {i} | {ap} | {il} |".format(
                arm=row["arm"],
                budget=row["budget"] + (f" r{row['repeat']}" if row["repeat"] != 1 else ""),
                runs=row.get("runs", 1),
                how=row.get("how", "live"),
                macro=row["macro_exact_match"],
                u=_fmt(rates.get("unique")),
                a=_fmt(rates.get("ambiguous")),
                i=_fmt(rates.get("inconsistent")),
                ap=_fmt(row["ambiguous_partial"]),
                il=_fmt(row["inconsistent_label_only"]),
            )
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _fmt(value) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())
