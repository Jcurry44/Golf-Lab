from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app_common import connect
from golf_lab_analytics import course_cards, database_summary, model_board, player_card, player_cards, player_filter_profiles, warehouse_health
from golf_lab_import import seed_starter


class GolfLabCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "golf_lab.db"
        seed_starter(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_summary_is_model_ready(self) -> None:
        with connect(self.db, readonly=True) as conn:
            summary = database_summary(conn)
        self.assertEqual(summary["readiness"], "model-ready")
        self.assertGreaterEqual(summary["counts"]["players"], 6)
        self.assertGreater(summary["counts"]["rounds"], 0)

    def test_player_cards_have_plain_english_model_read(self) -> None:
        with connect(self.db, readonly=True) as conn:
            payload = player_cards(conn, limit=3)
        self.assertEqual(len(payload["rows"]), 3)
        self.assertIn("plain_english", payload["rows"][0])
        self.assertEqual(payload["rows"][0]["rank"], 1)

    def test_player_detail_returns_scorecards_and_course_fit(self) -> None:
        with connect(self.db, readonly=True) as conn:
            payload = player_card(conn, "scottie-scheffler", "starter-us-open-2026")
        self.assertEqual(payload["player"]["player_name"], "Scottie Scheffler")
        self.assertGreater(len(payload["rounds"]["rows"]), 0)
        self.assertIsNotNone(payload["model"])

    def test_model_and_course_boards(self) -> None:
        with connect(self.db, readonly=True) as conn:
            model = model_board(conn, limit=10)
            courses = course_cards(conn, limit=10)
            health = warehouse_health(conn)
        self.assertGreater(len(model["rows"]), 0)
        self.assertGreater(len(courses["rows"]), 0)
        self.assertEqual(health["grade"], "premium-ready")

    def test_player_filter_profiles_include_seasons_and_scoring(self) -> None:
        with connect(self.db, readonly=True) as conn:
            payload = player_filter_profiles(conn)
        self.assertGreater(len(payload["seasons"]), 0)
        self.assertGreater(len(payload["rows"]), 0)
        self.assertIn("scoring_average", payload["rows"][0])
        self.assertIn("avg_sg_total", payload["rows"][0])


if __name__ == "__main__":
    unittest.main()
