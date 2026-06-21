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
        self.assertIn("sg_t2g", payload["rows"][0])
        self.assertIn("scoring_rounds", payload["rows"][0])
        self.assertIn("scoring_average", payload["rows"][0])
        self.assertIn("tough_rounds", payload["rows"][0])
        self.assertIn("major_rounds", payload["rows"][0])

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
        self.assertIn("scoring_rounds", payload["player"])
        self.assertIn("hasTrustedScoring", payload["coverage"])
        self.assertGreater(len(payload["difficultySplits"]["rows"]), 0)
        self.assertEqual(payload["majorProfile"]["summary"]["rounds"], 4)
        self.assertIn("sg_total", payload["gradeExplanations"])
        self.assertIn("course_dna", payload["gradeExplanations"])
        self.assertIsNotNone(payload["model"])

    def test_player_detail_headline_model_uses_winner_market(self) -> None:
        with connect(self.db) as conn:
            conn.execute(
                """
                insert into model_predictions (
                  prediction_id, model_run_id, event_id, player_id, player_name,
                  market, rank, probability, fair_odds_american, edge_probability,
                  projected_to_par, confidence, plain_english, created_at,
                  source_provider, source_url, source_updated_at
                )
                values (
                  'starter-model-scottie-scheffler-cut', 'starter-model-run',
                  'starter-us-open-2026', 'scottie-scheffler', 'Scottie Scheffler',
                  'make cut', 1, 0.915, -1076, 0.01, null, 'High',
                  'Model sees him as a top-tier win profile. Win probability sits at 91.5%.',
                  '2026-06-22T00:00:00Z', 'test', 'test', '2026-06-22T00:00:00Z'
                )
                """
            )
            conn.commit()

        with connect(self.db, readonly=True) as conn:
            payload = player_card(conn, "scottie-scheffler", "starter-us-open-2026")

        self.assertEqual(payload["model"]["market"], "winner")
        self.assertLess(payload["model"]["probability"], 0.5)

    def test_model_and_course_boards(self) -> None:
        with connect(self.db, readonly=True) as conn:
            model = model_board(conn, limit=10)
            courses = course_cards(conn, limit=10)
            health = warehouse_health(conn)
        self.assertGreater(len(model["rows"]), 0)
        self.assertIn("tier", model["rows"][0])
        self.assertIn("tier_reason", model["rows"][0])
        self.assertGreater(len(courses["rows"]), 0)
        self.assertEqual(health["grade"], "premium-ready")
        self.assertGreater(len(health["coverage"]), 0)
        self.assertGreater(len(health["statQuality"]), 0)
        self.assertIn("contract", health["statQuality"][0])
        self.assertGreater(len(health["automation"]), 0)

    def test_player_filter_profiles_include_seasons_and_scoring(self) -> None:
        with connect(self.db, readonly=True) as conn:
            payload = player_filter_profiles(conn)
        self.assertGreater(len(payload["seasons"]), 0)
        self.assertGreater(len(payload["rows"]), 0)
        self.assertIn("scoring_average", payload["rows"][0])
        self.assertIn("scoring_rounds", payload["rows"][0])
        self.assertIn("avg_sg_total", payload["rows"][0])
        self.assertIn("tough_rounds", payload["rows"][0])
        self.assertIn("major_rounds", payload["rows"][0])

    def test_modified_stableford_scores_do_not_count_as_scoring_average(self) -> None:
        stableford_scores = [2, 7, 12, 4, 9, 14, 1, 8, 11, 6, 13, 5, 10, 3, 15, 0, 16, 17, 18, 19]
        with connect(self.db) as conn:
            conn.execute(
                """
                insert into players (player_id, player_name, country, tour)
                values ('stableford-player', 'Stableford Player', 'USA', 'PGA')
                """
            )
            conn.execute(
                """
                insert into courses (course_id, course_name, par)
                values ('stableford-course', 'Tahoe Mountain Club', 72)
                """
            )
            conn.execute(
                """
                insert into events (event_id, event_name, tour, season, course_id, status)
                values ('stableford-event', 'Barracuda Championship', 'PGA', 2025, 'stableford-course', 'complete')
                """
            )
            conn.executemany(
                """
                insert into rounds (
                  round_id, event_id, player_id, course_id, round_number, round_date,
                  score, source_provider, source_url, source_updated_at
                )
                values (?, 'stableford-event', 'stableford-player', 'stableford-course', ?, ?, ?, 'test', 'test', '2026-06-21T00:00:00Z')
                """,
                [
                    (f"stableford-round-{index}", (index % 4) + 1, f"2025-07-{index:02d}", score)
                    for index, score in enumerate(stableford_scores, start=1)
                ],
            )
            conn.commit()

        with connect(self.db, readonly=True) as conn:
            filters = player_filter_profiles(conn)
            cards = player_cards(conn, limit=5000)

        filter_row = next(row for row in filters["rows"] if row["player_id"] == "stableford-player")
        card_row = next(row for row in cards["rows"] if row["player_id"] == "stableford-player")
        self.assertEqual(filter_row["rounds"], 20)
        self.assertEqual(filter_row["scoring_rounds"], 0)
        self.assertIsNone(filter_row["scoring_average"])
        self.assertEqual(card_row["scoring_rounds"], 0)
        self.assertIsNone(card_row["scoring_average"])

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
