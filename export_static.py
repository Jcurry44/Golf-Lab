from __future__ import annotations

import argparse
import json
import shutil
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
    warehouse_health,
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def copy_web(out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(WEB_ROOT, out_dir)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")


def export_static(db_path: Path = DEFAULT_DB, out_dir: Path = ROOT / "docs") -> dict[str, int]:
    copy_web(out_dir)
    with connect(db_path, readonly=True) as conn:
        summary = database_summary(conn)
        event_id = (summary.get("selectedEvent") or {}).get("event_id") or ""
        players = player_cards(conn, event_id, limit=100)
        courses = course_cards(conn, limit=250)

        write_json(out_dir / "api" / "summary.json", summary)
        write_json(out_dir / "api" / "event.json", event_board(conn, event_id))
        write_json(out_dir / "api" / "player-cards.json", players)
        write_json(out_dir / "api" / "course-cards.json", courses)
        write_json(out_dir / "api" / "model-board.json", model_board(conn, event_id, limit=100))
        write_json(out_dir / "api" / "warehouse-health.json", warehouse_health(conn))

        for row in players["rows"]:
            player_id = row["player_id"]
            write_json(out_dir / "api" / "players" / f"{player_id}.json", player_card(conn, player_id, event_id))

        for row in courses["rows"]:
            course_id = row["course_id"]
            write_json(out_dir / "api" / "courses" / f"{course_id}.json", course_card(conn, course_id))

    return {
        "players": len(players["rows"]),
        "courses": len(courses["rows"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Golf Lab as a static GitHub Pages build.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=ROOT / "docs")
    args = parser.parse_args()
    result = export_static(args.db, args.out)
    print(result)


if __name__ == "__main__":
    main()
