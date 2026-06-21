from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app_common import connect
from golf_lab_analytics import course_cards, database_summary, model_board, player_card, player_cards, player_filter_profiles, warehouse_health
from golf_lab_import import seed_starter
from pga_tour_stats_backfill import extract_stat_details, normalize_player_name, numeric_stat_value


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
        self.assertIn("worstCourses", payload)
        self.assertIn("seasons", payload)
        self.assertIn("coverage", payload)
        self.assertIn("difficultySplits", payload)
        self.assertIn("courseDna", payload)
        self.assertIn("majorProfile", payload)
        self.assertIn("recentVsBaseline", payload)
        self.assertIn("gradeExplanations", payload)
        self.assertTrue(payload["coverage"]["hasRoundScorecards"])
        self.assertGreater(len(payload["difficultySplits"]["rows"]), 0)
        self.assertEqual(payload["majorProfile"]["summary"]["rounds"], 4)
        self.assertIn("sg_total", payload["gradeExplanations"])
        self.assertIn("course_dna", payload["gradeExplanations"])
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

    def test_player_filter_profiles_include_stat_only_seasons(self) -> None:
        with connect(self.db) as conn:
            conn.execute(
                """
                insert into players (player_id, player_name, tour)
                values ('stat-only-player', 'Stat Only Player', 'PGA')
                """
            )
            conn.execute(
                """
                insert into strokes_gained (
                  sg_id, player_id, period, sg_total, driving_distance, gir,
                  source_provider, source_url, source_updated_at
                )
                values (
                  'stat-only-player-season-2025', 'stat-only-player', 'season-2025',
                  1.25, 311.4, 0.702, 'test', 'test', '2026-06-21T00:00:00Z'
                )
                """
            )
            conn.commit()

        with connect(self.db, readonly=True) as conn:
            payload = player_filter_profiles(conn)
        profile = next(row for row in payload["rows"] if row["player_id"] == "stat-only-player")
        self.assertEqual(profile["season"], 2025)
        self.assertEqual(profile["rounds"], 0)
        self.assertEqual(profile["driving_distance"], 311.4)
        self.assertEqual(profile["gir"], 0.702)

    def test_pga_tour_stat_parser_handles_season_tables(self) -> None:
        next_data = {
            "props": {
                "pageProps": {
                    "dehydratedState": {
                        "queries": [
                            {
                                "queryKey": ["statDetails", {"tourCode": "R", "statId": "103", "year": 2025, "eventQuery": None}],
                                "state": {
                                    "data": {
                                        "__typename": "StatDetails",
                                        "statId": "103",
                                        "year": 2025,
                                        "statTitle": "Greens in Regulation Percentage",
                                        "rows": [{"playerName": "Ludvig Åberg", "stats": [{"statName": "%", "statValue": "70.12%"}]}],
                                    }
                                },
                            }
                        ]
                    }
                }
            }
        }
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'
        details = extract_stat_details(html, "103", 2025)
        self.assertEqual(details["statTitle"], "Greens in Regulation Percentage")
        self.assertEqual(normalize_player_name("Ludvig Åberg"), "ludvig aberg")
        self.assertEqual(numeric_stat_value("70.12%", percent=True), 0.7012)


if __name__ == "__main__":
    unittest.main()
