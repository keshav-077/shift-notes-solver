"""Symbolic solver, header, extraction and the no-network pipeline."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import httpx
from openai import APITimeoutError

from wsolver.client import ItemClient, ReplayClient, Settings
from wsolver.extract import (
    VoteBoard,
    canonical,
    decide,
    ground,
    statement_core,
    votes_from_completion,
)
from wsolver.header import parse_header_text, resolve_header, split_notes
from wsolver.pipeline import solve_item
from wsolver.solver import Constraint, analyse, render_answer
from wsolver.types import Flags

ROOT = Path(__file__).resolve().parents[1]

NOTES = """Shift notes, Bay 2. 3 staff on the rota: Ann, Ben, Cal. Blocks run 08:00, 10:00, 12:00, one person per block, and each person works exactly one block. There are 2 stations, one person on each: intake, packing. The people on a station are Ann, Ben; the rest are on no station.

Ann is on the 08:00 block.
Ben is on the block directly after Ann.
Cal comes later in the day than Ben.
Intake is Ann's station.
The kettle in the break room is broken.
"""


def _header():
    header, _ = split_notes(NOTES)
    parsed = parse_header_text(header)
    assert parsed is not None
    return parsed


def _constraint(kind, args, line, source, agreement=1.0):
    return Constraint(kind=kind, args=tuple(args), line=line, source=source, agreement=agreement)


class HeaderTests(unittest.TestCase):
    def test_parses_roster_sentence(self):
        header = _header()
        self.assertEqual(header.staff, ["Ann", "Ben", "Cal"])
        self.assertEqual(header.blocks, ["08:00", "10:00", "12:00"])
        self.assertEqual(header.stations, ["intake", "packing"])
        self.assertEqual(header.holders, ["Ann", "Ben"])

    def test_real_item_header(self):
        items = json.loads((ROOT / "candidate_package" / "items.json").read_text(encoding="utf-8"))
        for item in items:
            header = resolve_header(item["text"], n_staff=item["n_staff"], n_stations=item["n_stations"])
            self.assertIsNotNone(header, item["id"])
            self.assertEqual(len(header.staff), item["n_staff"])
            self.assertEqual(len(header.stations), item["n_stations"])

    def test_roster_fallback_when_sentence_is_prose(self):
        text = "Today's cover is nonstandard and the usual sentence is missing.\n\nAnn starts early.\n"
        header = resolve_header(
            text,
            {
                "staff": ["Ann", "Ben"],
                "blocks": ["08:00", "10:00"],
                "stations": ["intake"],
                "on_station": ["Ann"],
            },
            n_staff=2,
            n_stations=1,
        )
        self.assertIsNotNone(header)
        self.assertEqual(header.source, "model")
        self.assertEqual(header.holders, ["Ann"])


class SolverTests(unittest.TestCase):
    def setUp(self):
        self.header = _header()

    def test_unique(self):
        constraints = [
            _constraint("block_is", ("Ann", "08:00"), 0, "Ann is on the 08:00 block."),
            _constraint("adjacent", ("Ann", "Ben"), 1, "Ben is on the block directly after Ann."),
            _constraint("before", ("Ben", "Cal"), 2, "Cal comes later in the day than Ben."),
            _constraint("station_is", ("Ann", "intake"), 3, "Intake is Ann's station."),
        ]
        answer = render_answer(self.header, constraints)
        self.assertEqual(answer["case"], "unique")
        self.assertEqual(answer["assignment"]["Ann"], {"block": "08:00", "station": "intake"})
        self.assertEqual(answer["assignment"]["Ben"], {"block": "10:00", "station": "packing"})
        self.assertEqual(answer["assignment"]["Cal"], {"block": "12:00"})
        self.assertNotIn("station", answer["assignment"]["Cal"])

    def test_ambiguous_lists_every_solution(self):
        constraints = [_constraint("block_is", ("Ann", "08:00"), 0, "Ann is on the 08:00 block.")]
        answer = render_answer(self.header, constraints)
        self.assertEqual(answer["case"], "ambiguous")
        # Ben/Cal can swap the two later blocks, and the two holders can swap stations.
        self.assertEqual(len(answer["assignments"]), 4)

    def test_minimal_core_drops_an_idle_line(self):
        constraints = [
            _constraint("block_is", ("Ann", "08:00"), 0, "Ann is on 08:00."),
            _constraint("block_is", ("Ann", "10:00"), 1, "Ann is on 10:00."),
            _constraint("block_not", ("Ben", "12:00"), 2, "Ben is not on 12:00."),
        ]
        result = analyse(self.header, constraints)
        self.assertEqual(result.case, "inconsistent")
        cited = {c.line for c in result.chosen_core}
        self.assertEqual(cited, {0, 1})

    def test_station_exclusion_core(self):
        constraints = [
            _constraint("station_not", ("Ann", "intake"), 0, "Ann is not on intake."),
            _constraint("station_not", ("Ann", "packing"), 1, "Ann is not on packing."),
            _constraint("block_is", ("Cal", "12:00"), 2, "Cal is on 12:00."),
        ]
        result = analyse(self.header, constraints)
        self.assertEqual(result.case, "inconsistent")
        self.assertEqual({c.line for c in result.chosen_core}, {0, 1})

    def test_higher_agreement_core_wins_ties_on_size(self):
        # Two different contradictions. The higher-agreement one is cited
        # even though both are minimal.
        constraints = [
            _constraint("block_is", ("Ann", "08:00"), 0, "low a", agreement=0.4),
            _constraint("block_is", ("Ann", "10:00"), 1, "low b", agreement=0.4),
            _constraint("station_not", ("Ben", "intake"), 2, "high a", agreement=1.0),
            _constraint("station_not", ("Ben", "packing"), 3, "high b", agreement=1.0),
        ]
        result = analyse(self.header, constraints)
        self.assertEqual({c.line for c in result.chosen_core}, {2, 3})


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.header = _header()

    def test_station_in_a_person_slot_becomes_a_holder_order(self):
        obj = {"line": 1, "type": "before", "earlier": "packing", "later": "Cal"}
        self.assertEqual(
            canonical(obj, self.header, validate=True),
            ("holder_before", "packing", "Cal"),
        )
        # Without validation the station is not coerced into a holder constraint.
        self.assertIsNone(canonical(obj, self.header, validate=False))

    def test_non_holder_station_is_dropped_only_when_validating(self):
        obj = {"line": 1, "type": "station_is", "person": "Cal", "station": "intake"}
        self.assertIsNone(canonical(obj, self.header, validate=True))
        self.assertEqual(
            canonical(obj, self.header, validate=False),
            ("station_is", "Cal", "intake"),
        )

    def test_hedge_prefix_does_not_change_the_restatement_identity(self):
        a = "Going off the roster, Ann is on the 08:00 block."
        b = "Noted twice in the handover: Ann is on the 08:00 block."
        self.assertEqual(statement_core(a, self.header), statement_core(b, self.header))
        # A different fact does not collapse onto it.
        c = "Going off the roster, Ben is on the 08:00 block."
        self.assertNotEqual(statement_core(a, self.header), statement_core(c, self.header))

    def test_tie_drops_the_line(self):
        board = VoteBoard(1)
        board.add({0: ("block_is", "Ann", "08:00")}, repair=False)
        board.add({0: ("block_is", "Ann", "10:00")}, repair=False)
        constraints, decisions = decide(board, ["Ann is somewhere."], self.header, dedupe=False)
        self.assertEqual(constraints, [])
        self.assertTrue(decisions[0].disagreement)

    def test_repair_does_not_vote_on_lines_it_was_not_shown(self):
        body = ["Ann is on the 08:00 block.", "The kettle is broken."]
        page = json.dumps(
            {
                "constraints": [
                    {"line": 1, "type": "block_is", "person": "Ann", "block": "08:00"},
                    {"line": 2, "type": "none"},
                ]
            }
        )
        repair = json.dumps(
            {"constraints": [{"line": 1, "type": "block_is", "person": "Ann", "block": "10:00"}]}
        )
        board = VoteBoard(2)
        board.add(
            votes_from_completion(page, "stop", self.header, 2, validate=True),
            repair=False,
        )
        board.add(
            votes_from_completion(repair, "stop", self.header, 2, validate=True, expected={0}),
            repair=True,
        )
        _, decisions = decide(board, body, self.header, dedupe=True)
        # Line 0 is a 1-1 tie, so it is dropped. Line 1 was not in the repair
        # and stays a single vote of none, not a second none from the repair.
        self.assertEqual(decisions[1].repair_n, 0)
        self.assertEqual(decisions[1].base_n, 1)
        self.assertEqual(decisions[1].canon, ("none",))

    def test_wrong_person_is_replaced_when_the_line_names_one(self):
        line = "Ayesha is not assigned to calibration."
        # Header for this check needs Ayesha. Use the synthetic header's people
        # by feeding a line that names Ann and a constraint that names Ben.
        line = "Ann is not assigned to intake."
        obj = {"line": 1, "type": "station_not", "person": "Ben", "station": "packing"}
        canon = canonical(obj, self.header, validate=True)
        self.assertEqual(ground(canon, line, self.header), ("station_not", "Ann", "intake"))

    def test_gloss_overrides_a_flipped_holder_label(self):
        obj = {
            "line": 2,
            "gloss": "Cal's block is later than whoever holds packing",
            "type": "holder_order",
            "station": "packing",
            "person": "Cal",
            "person_is": "earlier",
        }
        self.assertEqual(
            canonical(obj, self.header, validate=True),
            ("holder_before", "packing", "Cal"),
        )

    def test_holder_after_means_the_person_is_later(self):
        obj = {"line": 2, "type": "holder_after", "station": "packing", "earlier": "Cal"}
        self.assertEqual(
            canonical(obj, self.header, validate=True),
            ("holder_before", "packing", "Cal"),
        )
        ordered = {
            "line": 2,
            "type": "holder_order",
            "station": "packing",
            "person": "Cal",
            "person_is": "earlier",
        }
        self.assertEqual(
            canonical(ordered, self.header, validate=True),
            ("holder_after", "packing", "Cal"),
        )

    def test_package_has_no_answer_key(self):
        for path in (ROOT / "wsolver").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("visible_key", text, path.name)
            self.assertNotIn("B2-", text, path.name)


def _completion(constraints: list[dict]) -> str:
    return json.dumps({"roster": {}, "constraints": constraints})


class PipelineTests(unittest.TestCase):
    def test_answer_changes_when_the_model_output_changes(self):
        item = {"id": "T-1", "text": NOTES, "n_staff": 3, "n_stations": 2}
        flags = Flags()
        first = _completion(
            [
                {"line": 1, "type": "block_is", "person": "Ann", "block": "08:00"},
                {"line": 2, "type": "adjacent", "earlier": "Ann", "later": "Ben"},
                {"line": 3, "type": "before", "earlier": "Ben", "later": "Cal"},
                {"line": 4, "type": "station_is", "person": "Ann", "station": "intake"},
                {"line": 5, "type": "none"},
            ]
        )
        # A different translation of the same lines. The answer has to follow
        # the model, not the wording, so this one names every block outright.
        # Same entities the lines actually name, but the opposite order on
        # line 2. Grounding must not rewrite that; the answer has to follow it.
        second = _completion(
            [
                {"line": 1, "type": "block_is", "person": "Ann", "block": "08:00"},
                {"line": 2, "type": "adjacent", "earlier": "Ben", "later": "Ann"},
                {"line": 3, "type": "before", "earlier": "Ben", "later": "Cal"},
                {"line": 4, "type": "station_is", "person": "Ann", "station": "intake"},
                {"line": 5, "type": "none"},
            ]
        )
        client_a = ReplayClient("T-1", 1, [(first, "stop")])
        client_b = ReplayClient("T-1", 1, [(second, "stop")])
        answer_a, _ = solve_item(item, "1x", flags, client_a)
        answer_b, _ = solve_item(item, "1x", flags, client_b)
        self.assertEqual(client_a.calls, 1)
        self.assertEqual(client_b.calls, 1)
        self.assertEqual(answer_a["case"], "unique")
        self.assertEqual(answer_a["assignment"]["Ann"]["block"], "08:00")
        self.assertEqual(answer_b["case"], "inconsistent")
        self.assertNotEqual(answer_a, answer_b)

    def test_one_x_is_exactly_one_call_even_with_repair_enabled(self):
        item = {"id": "T-2", "text": NOTES, "n_staff": 3, "n_stations": 2}
        raw = _completion(
            [
                {"line": 1, "type": "block_is", "person": "Ann", "block": "08:00"},
                {"line": 2, "type": "none"},
                {"line": 3, "type": "none"},
                {"line": 4, "type": "none"},
                {"line": 5, "type": "none"},
            ]
        )
        # A second response is available. 1x must not consume it.
        client = ReplayClient("T-2", 1, [(raw, "stop"), (raw, "stop")])
        answer, debug = solve_item(item, "1x", Flags(), client)
        self.assertEqual(client.calls, 1)
        self.assertEqual(debug["calls"], 1)
        self.assertIn(answer["case"], {"unique", "ambiguous", "inconsistent"})

    def test_three_x_stops_at_the_cap_and_uses_a_repair_on_a_tie(self):
        item = {"id": "T-3", "text": NOTES, "n_staff": 3, "n_stations": 2}
        vote_a = _completion(
            [
                {"line": 1, "type": "block_is", "person": "Ann", "block": "08:00"},
                {"line": 2, "type": "adjacent", "earlier": "Ann", "later": "Ben"},
                {"line": 3, "type": "before", "earlier": "Ben", "later": "Cal"},
                {"line": 4, "type": "station_is", "person": "Ann", "station": "intake"},
                {"line": 5, "type": "none"},
            ]
        )
        vote_b = _completion(
            [
                {"line": 1, "type": "block_is", "person": "Ann", "block": "08:00"},
                {"line": 2, "type": "adjacent", "earlier": "Ben", "later": "Ann"},
                {"line": 3, "type": "before", "earlier": "Ben", "later": "Cal"},
                {"line": 4, "type": "station_is", "person": "Ann", "station": "intake"},
                {"line": 5, "type": "none"},
            ]
        )
        repair = _completion(
            [
                {"line": 1, "type": "block_is", "person": "Ann", "block": "08:00"},
            ]
        )
        client = ReplayClient("T-3", 3, [(vote_a, "stop"), (vote_b, "stop"), (repair, "stop")])
        answer, debug = solve_item(item, "3x", Flags(), client)
        self.assertEqual(client.calls, 3)
        self.assertLessEqual(client.calls, 3)
        self.assertEqual(answer["assignment"]["Ann"]["block"], "08:00")
        self.assertEqual(debug["responses"][2]["kind"], "repair")


class TimeoutBudgetTests(unittest.TestCase):
    def test_timeout_at_1x_counts_as_the_one_call_and_is_not_retried(self):
        client = ItemClient(
            "T-timeout",
            1,
            Settings(api_key="test", base_url="http://127.0.0.1:9", model="ibm-granite/granite-4.2-8b"),
        )
        request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
        create = MagicMock(side_effect=APITimeoutError(request))
        client._client.chat.completions.create = create
        with self.assertRaises(APITimeoutError):
            client.complete([{"role": "user", "content": "hi"}], json_mode=True)
        self.assertEqual(client.calls, 1)
        self.assertEqual(create.call_count, 1)


class NaiveVoteTests(unittest.TestCase):
    def test_a_tie_between_cases_is_ambiguous_and_does_not_crash(self):
        from wsolver.naive import _vote

        answer = _vote(
            [
                {"case": "unique", "assignment": {"Ann": {"block": "08:00"}}},
                {"case": "inconsistent", "conflicts": ["a line", "another", "a third"]},
            ]
        )
        self.assertEqual(answer, {"case": "ambiguous", "assignments": []})


if __name__ == "__main__":
    unittest.main()
