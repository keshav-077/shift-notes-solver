#!/usr/bin/env python3
"""Entrypoint: python run.py <items.json> --budget 1x|3x|10x --out answers.json

Components can be turned off for an ablation with --disable gloss,fewshot,...
--mode naive is the end-to-end baseline with no solver. Neither flag is
required for a normal run.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from wsolver.client import ItemClient, ReplayClient, load_settings
from wsolver.pipeline import solve_item
from wsolver.types import BUDGET_CAP, COMPONENTS, Flags


def _load_items(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise SystemExit(f"{path} is not a list of items")
    return data


def _load_replay(path: Path) -> dict[str, list[tuple[str, str]]]:
    logs: dict[str, list[tuple[str, str]]] = {}
    for file in sorted(path.glob("*.json")):
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        item_id = payload.get("id") or file.stem
        pairs = []
        for entry in payload.get("responses") or []:
            if "content" in entry:
                pairs.append((entry["content"], entry.get("finish") or "stop"))
        if pairs:
            logs[item_id] = pairs
    return logs


def _write(path: Path, items: list[dict], answers: dict) -> None:
    ordered = {item["id"]: answers[item["id"]] for item in items if item["id"] in answers}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for attempt in range(8):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.25 * (attempt + 1))


def _one(
    item: dict,
    budget: str,
    flags: Flags,
    mode: str,
    settings,
    replay: dict | None,
    log_dir: Path | None,
) -> tuple[str, dict, dict]:
    cap = BUDGET_CAP[budget]
    item_id = item["id"]
    if replay is not None:
        client = ReplayClient(item_id, cap, replay.get(item_id, []))
    else:
        client = ItemClient(item_id, cap, settings)
    try:
        answer, debug = solve_item(item, budget, flags, client, mode=mode)
    except Exception:
        traceback.print_exc()
        # A crash after the call must still leave a parsable answer. A crash
        # before any call is re-raised so a graded run does not silently
        # answer with zero model calls.
        if client.calls < 1:
            raise
        answer, debug = {"case": "ambiguous", "assignments": []}, {
            "id": item_id,
            "calls": client.calls,
            "error": traceback.format_exc(),
        }
    debug["calls"] = client.calls
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{item_id}.json").write_text(
            json.dumps(debug, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return item_id, answer, debug


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Solve shift-note items with a fixed weak model.")
    parser.add_argument("items", type=Path)
    parser.add_argument("--budget", required=True, choices=sorted(BUDGET_CAP))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--disable",
        default="",
        help=f"Comma-separated components to turn off: {', '.join(COMPONENTS)}",
    )
    parser.add_argument("--mode", choices=("extract", "naive"), default="extract")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--replay-dir", type=Path, default=None, help="Dev only: replay logged completions.")
    parser.add_argument("--limit", type=int, default=0, help="Dev only: solve the first N items.")
    args = parser.parse_args(argv)

    disabled = [part.strip() for part in args.disable.split(",") if part.strip()]
    flags = Flags.from_disabled(disabled)
    items = _load_items(args.items)
    if args.limit:
        items = items[: args.limit]
    settings = None if args.replay_dir else load_settings()
    replay = _load_replay(args.replay_dir) if args.replay_dir else None

    answers: dict = {}
    lock = threading.Lock()
    done = 0

    def finish(item_id: str, answer: dict, debug: dict) -> None:
        nonlocal done
        with lock:
            answers[item_id] = answer
            done += 1
            case = answer.get("case")
            calls = debug.get("calls")
            print(f"[{done}/{len(items)}] {item_id} {case} calls={calls}", flush=True)
            _write(args.out, items, answers)

    workers = max(1, args.workers)
    if workers == 1:
        for item in items:
            item_id, answer, debug = _one(item, args.budget, flags, args.mode, settings, replay, args.log_dir)
            finish(item_id, answer, debug)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_one, item, args.budget, flags, args.mode, settings, replay, args.log_dir)
                for item in items
            ]
            for future in as_completed(futures):
                item_id, answer, debug = future.result()
                finish(item_id, answer, debug)

    missing = [item["id"] for item in items if item["id"] not in answers]
    if missing:
        print(f"missing answers for {len(missing)} items", file=sys.stderr)
        return 1
    print(f"wrote {len(answers)} answers to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
