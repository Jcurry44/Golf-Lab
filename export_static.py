from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app_common import DEFAULT_DB, ROOT, WEB_ROOT, connect
from golf_lab_analytics import (
    course_card,
    course_cards,
    database_summary,
    event_board,
    model_board,
    player_card,
    player_cards,
    player_filter_profiles,
    warehouse_health,
)
from golf_lab_backtest import historical_prediction_audit, prediction_review


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


DETAIL_DIRS = (Path("api") / "players", Path("api") / "courses")


def copy_web(out_dir: Path, preserve_detail_dirs: bool = True) -> None:
    preserved_root: Path | None = None
    if preserve_detail_dirs and out_dir.exists():
        preserved_root = Path(tempfile.mkdtemp(prefix="golf-lab-static-"))
        for rel_path in DETAIL_DIRS:
            source = out_dir / rel_path
            if source.exists():
                shutil.copytree(source, preserved_root / rel_path)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(WEB_ROOT, out_dir)
    if preserved_root:
        for rel_path in DETAIL_DIRS:
            source = preserved_root / rel_path
            if source.exists():
                shutil.copytree(source, out_dir / rel_path, dirs_exist_ok=True)
        shutil.rmtree(preserved_root, ignore_errors=True)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")


def export_static(
    db_path: Path = DEFAULT_DB,
    out_dir: Path = ROOT / "docs",
    refresh_details: bool = False,
    refresh_course_details: bool = False,
    refresh_audit: bool = False,
    audit_limit: int = 40,
) -> dict[str, int]:
    audit_path = out_dir / "api" / "historical-audit.json"
    cached_audit = None
    if not refresh_audit and audit_path.exists():
        cached_audit = json.loads(audit_path.read_text(encoding="utf-8"))

    copy_web(out_dir, preserve_detail_dirs=not refresh_details)
    with connect(db_path, readonly=True) as conn:
        summary = database_summary(conn)
        event_id = (summary.get("selectedEvent") or {}).get("event_id") or ""
        players = player_cards(conn, event_id, limit=5000)
        courses = course_cards(conn, limit=250)
        player_details_written = 0
        course_details_written = 0

        write_json(out_dir / "api" / "summary.json", summary)
        write_json(out_dir / "api" / "event.json", event_board(conn, event_id))
        write_json(out_dir / "api" / "player-cards.json", players)
        write_json(out_dir / "api" / "player-filters.json", player_filter_profiles(conn))
        write_json(out_dir / "api" / "course-cards.json", courses)
        write_json(out_dir / "api" / "model-board.json", model_board(conn, event_id, limit=250))
        write_json(out_dir / "api" / "prediction-review.json", prediction_review(conn, event_id))
        if cached_audit is not None:
            write_json(out_dir / "api" / "historical-audit.json", cached_audit)
        else:
            write_json(out_dir / "api" / "historical-audit.json", historical_prediction_audit(conn, limit=audit_limit, focus="similar"))
        write_json(out_dir / "api" / "warehouse-health.json", warehouse_health(conn))

        for row in players["rows"]:
            player_id = row["player_id"]
            path = out_dir / "api" / "players" / f"{player_id}.json"
            if refresh_details or not path.exists():
                write_json(path, player_card(conn, player_id, event_id))
                player_details_written += 1

        for row in courses["rows"]:
            course_id = row["course_id"]
            path = out_dir / "api" / "courses" / f"{course_id}.json"
            if refresh_details or refresh_course_details or not path.exists():
                write_json(path, course_card(conn, course_id))
                course_details_written += 1

    return {
        "players": len(players["rows"]),
        "courses": len(courses["rows"]),
        "player_details_written": player_details_written,
        "course_details_written": course_details_written,
        "audit_events": audit_limit,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Golf Lab as a static GitHub Pages build.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=ROOT / "docs")
    parser.add_argument("--refresh-details", action="store_true", help="Regenerate every static player/course detail file.")
    parser.add_argument("--refresh-course-details", action="store_true", help="Regenerate every static course detail file.")
    parser.add_argument("--refresh-audit", action="store_true", help="Recompute the expensive historical walk-forward audit.")
    parser.add_argument("--audit-limit", type=int, default=40, help="Recent similar events to include in the historical audit payload.")
    args = parser.parse_args()
    result = export_static(
        args.db,
        args.out,
        refresh_details=args.refresh_details,
        refresh_course_details=args.refresh_course_details,
        refresh_audit=args.refresh_audit,
        audit_limit=args.audit_limit,
    )
    print(result)


if __name__ == "__main__":
    main()
