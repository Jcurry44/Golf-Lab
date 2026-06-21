from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from app_common import DEFAULT_DB, connect, init_db


COLLECTION_FILES = {
    "players": "players.csv",
    "courses": "courses.csv",
    "events": "events.csv",
    "course_setups": "course_setups.csv",
    "fields": "fields.csv",
    "rounds": "rounds.csv",
    "strokes_gained": "strokes_gained.csv",
    "weather_snapshots": "weather_snapshots.csv",
    "odds_snapshots": "odds_snapshots.csv",
    "model_predictions": "model_predictions.csv",
    "prediction_ledger": "prediction_ledger.csv",
    "source_fetches": "source_fetches.csv",
}


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def number(value: Any) -> float | None:
    text = clean(value)
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def integer(value: Any) -> int | None:
    numeric = number(value)
    return int(numeric) if numeric is not None else None


def normalize_header(value: str) -> str:
    text = clean(value).replace("\ufeff", "")
    out = []
    for index, char in enumerate(text):
        if char.isupper() and index and text[index - 1].islower():
            out.append("_")
        out.append(char.lower() if char.isalnum() else "_")
    return "_".join(part for part in "".join(out).split("_") if part)


def csv_rows(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        reader.fieldnames = [normalize_header(name or "") for name in (reader.fieldnames or [])]
        for row in reader:
            yield {normalize_header(key): clean(value) for key, value in row.items() if key}


def first(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = clean(row.get(key))
        if value:
            return value
    return ""


def setup_db(db_path: Path, reset: bool = False) -> sqlite3.Connection:
    if reset and db_path.exists():
        db_path.unlink()
    conn = connect(db_path)
    init_db(conn)
    return conn


def upsert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    keys = list(values)
    placeholders = ", ".join("?" for _ in keys)
    updates = ", ".join(f"{key}=excluded.{key}" for key in keys)
    conn.execute(
        f"insert into {table} ({', '.join(keys)}) values ({placeholders}) "
        f"on conflict do update set {updates}",
        tuple(values[key] for key in keys),
    )


def select_event(events: list[dict[str, str]], predictions: list[dict[str, str]], forced: str = "") -> dict[str, str] | None:
    if forced:
        match = next((event for event in events if first(event, "id", "event_id") == forced), None)
        if match:
            return match
    prediction_event_ids = {first(row, "event_id") for row in predictions if first(row, "event_id")}
    modeled = [event for event in events if first(event, "id", "event_id") in prediction_event_ids]
    candidates = modeled or events
    return sorted(candidates, key=lambda row: first(row, "end_date", "start_date"), reverse=True)[0] if candidates else None


def player_ids_for_event(rows: dict[str, list[dict[str, str]]], event_id: str, limit: int) -> set[str]:
    ids: list[str] = []
    seen: set[str] = set()

    def push(value: str) -> None:
        player_id = clean(value)
        if player_id and player_id not in seen and len(ids) < limit:
            seen.add(player_id)
            ids.append(player_id)

    for row in sorted(
        [row for row in rows["model_predictions"] if first(row, "event_id") == event_id],
        key=lambda item: integer(first(item, "rank")) or 9999,
    ):
        push(first(row, "player_id"))
    for row in rows["prediction_ledger"]:
        if first(row, "event_id") == event_id:
            push(first(row, "player_id"))
    for row in rows["odds_snapshots"]:
        if first(row, "event_id") == event_id:
            push(first(row, "player_id"))
    for row in rows["fields"]:
        if first(row, "event_id") == event_id:
            push(first(row, "player_id"))
    return set(ids)


def import_players(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], player_ids: set[str]) -> None:
    for row in rows:
        player_id = first(row, "id", "player_id")
        if player_ids and player_id not in player_ids:
            continue
        upsert(conn, "players", {
            "player_id": player_id,
            "player_name": first(row, "name", "player_name") or player_id,
            "country": first(row, "country"),
            "tour": first(row, "tour") or "PGA",
            "handedness": first(row, "handedness"),
            "birth_year": integer(first(row, "birth_year")),
            "turned_pro": integer(first(row, "turned_pro")),
            "source_provider": first(row, "source_provider"),
            "source_url": first(row, "source_url"),
            "source_updated_at": first(row, "source_updated_at"),
        })


def import_courses(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], course_ids: set[str]) -> None:
    for row in rows:
        course_id = first(row, "id", "course_id")
        if course_ids and course_id not in course_ids and first(row, "name", "course_name") not in course_ids:
            continue
        upsert(conn, "courses", {
            "course_id": course_id or first(row, "name", "course_name"),
            "course_name": first(row, "name", "course_name") or course_id,
            "location": first(row, "location"),
            "country": first(row, "country"),
            "par": integer(first(row, "par")),
            "yards": integer(first(row, "yards", "yardage")),
            "architect": first(row, "architect"),
            "source_provider": first(row, "source_provider"),
            "source_url": first(row, "source_url"),
            "source_updated_at": first(row, "source_updated_at"),
        })


def import_events(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], event_ids: set[str]) -> None:
    for row in rows:
        event_id = first(row, "id", "event_id")
        if event_ids and event_id not in event_ids:
            continue
        upsert(conn, "events", {
            "event_id": event_id,
            "event_name": first(row, "name", "event_name") or event_id,
            "tour": first(row, "tour") or "PGA",
            "season": integer(first(row, "season")),
            "start_date": first(row, "start_date"),
            "end_date": first(row, "end_date"),
            "course_id": first(row, "course_id") or None,
            "course_name": first(row, "course_name"),
            "status": first(row, "status"),
            "source_provider": first(row, "source_provider"),
            "source_url": first(row, "source_url"),
            "source_updated_at": first(row, "source_updated_at"),
        })


def import_course_setups(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], event_ids: set[str], course_ids: set[str]) -> None:
    for row in rows:
        event_id = first(row, "event_id")
        course_id = first(row, "course_id")
        if event_ids and event_id not in event_ids and course_id not in course_ids:
            continue
        upsert(conn, "course_setups", {
            "setup_id": first(row, "id", "setup_id") or f"{event_id}-{course_id}",
            "event_id": event_id,
            "course_id": course_id,
            "course_name": first(row, "course_name"),
            "par": integer(first(row, "par")),
            "yards": integer(first(row, "yards", "yardage")),
            "rough": first(row, "rough"),
            "green_speed": first(row, "green_speed"),
            "fairway_width": first(row, "fairway_width"),
            "difficulty_score": number(first(row, "difficulty_score", "difficulty")),
            "difficulty_bucket": first(row, "difficulty_bucket"),
            "source_provider": first(row, "source_provider"),
            "source_url": first(row, "source_url"),
            "source_updated_at": first(row, "source_updated_at"),
        })


def import_fields(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], event_id: str, player_ids: set[str]) -> None:
    for row in rows:
        if first(row, "event_id") != event_id or first(row, "player_id") not in player_ids:
            continue
        upsert(conn, "fields", {
            "field_id": first(row, "id", "field_id") or f"{event_id}-{first(row, 'player_id')}",
            "event_id": event_id,
            "player_id": first(row, "player_id"),
            "player_name": first(row, "player_name"),
            "status": first(row, "status"),
            "seed": integer(first(row, "seed")),
            "tee_time": first(row, "tee_time"),
            "source_provider": first(row, "source_provider"),
            "source_url": first(row, "source_url"),
            "source_updated_at": first(row, "source_updated_at"),
        })


def import_rounds(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], player_ids: set[str], limit_per_player: int) -> tuple[set[str], set[str], set[str]]:
    by_player: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        player_id = first(row, "player_id")
        if player_id not in player_ids:
            continue
        by_player[player_id].append(row)
    round_ids: set[str] = set()
    event_ids: set[str] = set()
    course_ids: set[str] = set()
    for player_rows in by_player.values():
        for row in sorted(player_rows, key=lambda item: first(item, "date", "round_date", "source_updated_at"), reverse=True)[:limit_per_player]:
            round_id = first(row, "id", "round_id")
            event_id = first(row, "event_id")
            course_id = first(row, "course_id")
            round_ids.add(round_id)
            event_ids.add(event_id)
            if course_id:
                course_ids.add(course_id)
            upsert(conn, "rounds", {
                "round_id": round_id,
                "event_id": event_id,
                "player_id": first(row, "player_id"),
                "course_id": course_id,
                "round_number": integer(first(row, "round_number")),
                "round_date": first(row, "date", "round_date"),
                "score": integer(first(row, "score")),
                "to_par": number(first(row, "to_par")),
                "position": first(row, "position"),
                "source_provider": first(row, "source_provider"),
                "source_url": first(row, "source_url"),
                "source_updated_at": first(row, "source_updated_at"),
            })
    return round_ids, event_ids, course_ids


def import_strokes_gained(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], round_ids: set[str], event_ids: set[str], player_ids: set[str]) -> None:
    for row in rows:
        round_id = first(row, "round_id")
        event_id = first(row, "event_id")
        player_id = first(row, "player_id")
        if round_id not in round_ids and not (event_id in event_ids and player_id in player_ids):
            continue
        linked_round_id = round_id if round_id in round_ids else None
        upsert(conn, "strokes_gained", {
            "sg_id": first(row, "id", "sg_id") or f"{round_id or event_id}-{player_id}-sg",
            "round_id": linked_round_id,
            "event_id": event_id or None,
            "player_id": player_id,
            "period": first(row, "period"),
            "sg_total": number(first(row, "sg_total")),
            "sg_t2g": number(first(row, "sg_t2g")),
            "sg_ott": number(first(row, "sg_ott")),
            "sg_app": number(first(row, "sg_app")),
            "sg_arg": number(first(row, "sg_arg")),
            "sg_putt": number(first(row, "sg_putt")),
            "source_provider": first(row, "source_provider"),
            "source_url": first(row, "source_url"),
            "source_updated_at": first(row, "source_updated_at"),
        })


def import_weather(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], event_ids: set[str]) -> None:
    for row in rows:
        if first(row, "event_id") not in event_ids:
            continue
        upsert(conn, "weather_snapshots", {
            "weather_id": first(row, "id", "weather_id") or f"{first(row, 'event_id')}-{first(row, 'round_number')}-{first(row, 'forecast_at', 'date')}",
            "event_id": first(row, "event_id"),
            "course_id": first(row, "course_id"),
            "round_number": integer(first(row, "round_number")),
            "forecast_at": first(row, "forecast_at", "date", "observed_at"),
            "temperature_f": number(first(row, "temperature_f", "temp_f")),
            "wind_mph": number(first(row, "wind_mph")),
            "wind_direction": first(row, "wind_direction"),
            "precip_probability": number(first(row, "precip_probability", "precip_probability_pct")),
            "condition": first(row, "condition"),
            "source_provider": first(row, "source_provider"),
            "source_url": first(row, "source_url"),
            "source_updated_at": first(row, "source_updated_at"),
        })


def import_odds(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], event_id: str, player_ids: set[str]) -> None:
    for row in rows:
        if first(row, "event_id") != event_id or first(row, "player_id") not in player_ids:
            continue
        upsert(conn, "odds_snapshots", {
            "odds_id": first(row, "id", "odds_id") or f"{event_id}-{first(row, 'player_id')}-{first(row, 'market')}-{first(row, 'book')}-{first(row, 'captured_at')}",
            "event_id": event_id,
            "player_id": first(row, "player_id"),
            "player_name": first(row, "player_name"),
            "market": first(row, "market") or "winner",
            "book": first(row, "book"),
            "odds_american": integer(first(row, "odds_american")),
            "implied_probability": number(first(row, "implied_probability")),
            "captured_at": first(row, "captured_at"),
            "source_provider": first(row, "source_provider"),
            "source_url": first(row, "source_url"),
            "source_updated_at": first(row, "source_updated_at"),
        })


def import_predictions(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], event_id: str, player_ids: set[str]) -> set[str]:
    model_run_ids: set[str] = set()
    for row in rows:
        if first(row, "event_id") != event_id or first(row, "player_id") not in player_ids:
            continue
        model_run_id = first(row, "model_run_id")
        if model_run_id:
            model_run_ids.add(model_run_id)
        upsert(conn, "model_predictions", {
            "prediction_id": first(row, "id", "prediction_id") or f"{model_run_id}-{first(row, 'player_id')}-{first(row, 'market')}",
            "model_run_id": model_run_id,
            "event_id": event_id,
            "player_id": first(row, "player_id"),
            "player_name": first(row, "player_name"),
            "market": first(row, "market") or "winner",
            "rank": integer(first(row, "rank")),
            "probability": number(first(row, "probability")),
            "fair_odds_american": integer(first(row, "fair_odds_american", "fair_odds")),
            "edge_probability": number(first(row, "edge_probability", "edge")),
            "projected_to_par": number(first(row, "projected_to_par")),
            "confidence": first(row, "confidence"),
            "plain_english": first(row, "plain_english", "reasoning", "why"),
            "risk_flags": first(row, "risk_flags", "risk"),
            "created_at": first(row, "created_at"),
            "source_provider": first(row, "source_provider"),
            "source_url": first(row, "source_url"),
            "source_updated_at": first(row, "source_updated_at"),
        })
    return model_run_ids


def import_ledger(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], event_id: str, player_ids: set[str]) -> None:
    for row in rows:
        if first(row, "event_id") != event_id or first(row, "player_id") not in player_ids:
            continue
        upsert(conn, "prediction_ledger", {
            "ledger_id": first(row, "id", "ledger_id") or f"{first(row, 'model_run_id')}-{first(row, 'player_id')}-{first(row, 'market')}",
            "model_run_id": first(row, "model_run_id"),
            "event_id": event_id,
            "player_id": first(row, "player_id"),
            "market": first(row, "market") or "winner",
            "rank": integer(first(row, "rank")),
            "probability": number(first(row, "probability")),
            "odds_american": integer(first(row, "odds_american")),
            "outcome_status": first(row, "outcome_status"),
            "result_label": first(row, "result_label"),
            "created_at": first(row, "created_at"),
            "source_provider": first(row, "source_provider"),
            "source_url": first(row, "source_url"),
            "source_updated_at": first(row, "source_updated_at"),
        })


def import_sources(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], event_ids: set[str], model_run_ids: set[str]) -> None:
    for row in rows:
        if first(row, "event_id") not in event_ids and first(row, "model_run_id") not in model_run_ids:
            continue
        upsert(conn, "source_fetches", {
            "fetch_id": first(row, "id", "fetch_id") or f"{first(row, 'provider')}-{first(row, 'endpoint')}-{first(row, 'fetched_at')}",
            "provider": first(row, "provider", "source_provider"),
            "endpoint": first(row, "endpoint"),
            "event_id": first(row, "event_id"),
            "model_run_id": first(row, "model_run_id"),
            "fetched_at": first(row, "fetched_at", "source_updated_at"),
            "status": first(row, "status") or "ok",
            "row_count": integer(first(row, "row_count")),
            "source_url": first(row, "source_url"),
            "notes": first(row, "notes"),
        })


def import_player_skill_snapshots(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        select p.player_id, p.player_name,
               count(r.round_id) as rounds,
               avg(sg.sg_total) as sg_total,
               avg(sg.sg_t2g) as sg_t2g,
               avg(sg.sg_ott) as sg_ott,
               avg(sg.sg_app) as sg_app,
               avg(sg.sg_arg) as sg_arg,
               avg(sg.sg_putt) as sg_putt
        from players p
        left join rounds r on r.player_id = p.player_id
        left join strokes_gained sg on sg.round_id = r.round_id
        group by p.player_id, p.player_name
        """
    ).fetchall()
    for row in rows:
        upsert(conn, "player_skill_snapshots", {
            "snapshot_id": f"{row['player_id']}-derived-current",
            "player_id": row["player_id"],
            "season": None,
            "sg_total": row["sg_total"],
            "sg_t2g": row["sg_t2g"],
            "sg_ott": row["sg_ott"],
            "sg_app": row["sg_app"],
            "sg_arg": row["sg_arg"],
            "sg_putt": row["sg_putt"],
            "driving_distance": None,
            "accuracy": None,
            "gir": None,
            "scrambling": None,
            "source_provider": "Golf Lab derived scoring model",
            "source_url": "derived-from-rounds",
            "source_updated_at": None,
        })


def import_from_warehouse(input_dir: Path, db_path: Path, event_id: str = "", player_limit: int = 80, rounds_per_player: int = 40) -> dict[str, Any]:
    rows = {
        "events": list(csv_rows(input_dir / COLLECTION_FILES["events"])),
        "model_predictions": list(csv_rows(input_dir / COLLECTION_FILES["model_predictions"])),
        "prediction_ledger": list(csv_rows(input_dir / COLLECTION_FILES["prediction_ledger"])),
        "fields": list(csv_rows(input_dir / COLLECTION_FILES["fields"])),
        "odds_snapshots": list(csv_rows(input_dir / COLLECTION_FILES["odds_snapshots"])),
    }
    event = select_event(rows["events"], rows["model_predictions"], event_id)
    if not event:
        raise SystemExit("No event rows found in warehouse.")
    selected_event_id = first(event, "id", "event_id")
    player_ids = player_ids_for_event(rows, selected_event_id, player_limit)

    conn = setup_db(db_path, reset=True)
    try:
        import_players(conn, csv_rows(input_dir / COLLECTION_FILES["players"]), player_ids)
        import_courses(conn, csv_rows(input_dir / COLLECTION_FILES["courses"]), set())
        import_events(conn, rows["events"], set())
        conn.commit()
        import_fields(conn, rows["fields"], selected_event_id, player_ids)
        model_run_ids = import_predictions(conn, rows["model_predictions"], selected_event_id, player_ids)
        import_ledger(conn, rows["prediction_ledger"], selected_event_id, player_ids)
        import_odds(conn, rows["odds_snapshots"], selected_event_id, player_ids)
        round_ids, round_event_ids, course_ids = import_rounds(conn, csv_rows(input_dir / COLLECTION_FILES["rounds"]), player_ids, rounds_per_player)
        event_ids = {selected_event_id, *round_event_ids}
        import_course_setups(conn, csv_rows(input_dir / COLLECTION_FILES["course_setups"]), event_ids, course_ids)
        import_strokes_gained(conn, csv_rows(input_dir / COLLECTION_FILES["strokes_gained"]), round_ids, event_ids, player_ids)
        import_weather(conn, csv_rows(input_dir / COLLECTION_FILES["weather_snapshots"]), event_ids)
        import_sources(conn, csv_rows(input_dir / COLLECTION_FILES["source_fetches"]), event_ids, model_run_ids)
        import_player_skill_snapshots(conn)
        conn.commit()
        counts = {
            table: conn.execute(f"select count(*) from {table}").fetchone()[0]
            for table in ["players", "events", "courses", "rounds", "strokes_gained", "model_predictions", "odds_snapshots"]
        }
        return {"eventId": selected_event_id, "playerIds": len(player_ids), "counts": counts}
    finally:
        conn.close()


STARTER_PLAYERS = [
    ("scottie-scheffler", "Scottie Scheffler", "USA", 1, 0.185, "Elite baseline. Best blend of tee-to-green strength, tough-course scoring, and recent reliability."),
    ("xander-schauffele", "Xander Schauffele", "USA", 2, 0.118, "Major-proof profile with low volatility and strong approach floor."),
    ("rory-mcilroy", "Rory McIlroy", "Northern Ireland", 3, 0.102, "Ceiling remains high when driver advantage matters, but putting variance is the risk."),
    ("collin-morikawa", "Collin Morikawa", "USA", 4, 0.082, "Approach-heavy fit. Needs neutral putting to convert the ball-striking edge."),
    ("ludvig-aberg", "Ludvig Åberg", "Sweden", 5, 0.071, "Premium long-game profile with less major-history certainty."),
    ("bryson-dechambeau", "Bryson DeChambeau", "USA", 6, 0.067, "Distance creates unique scoring paths on hard setups, with accuracy as the swing factor."),
]


def seed_starter(db_path: Path) -> dict[str, Any]:
    conn = setup_db(db_path, reset=True)
    try:
        upsert(conn, "courses", {
            "course_id": "oakmont-country-club",
            "course_name": "Oakmont Country Club",
            "location": "Oakmont, PA",
            "country": "USA",
            "par": 70,
            "yards": 7255,
            "architect": "Henry Fownes",
            "source_provider": "starter seed",
            "source_url": "local-starter",
            "source_updated_at": "2026-06-21T00:00:00Z",
        })
        upsert(conn, "events", {
            "event_id": "starter-us-open-2026",
            "event_name": "U.S. Open",
            "tour": "PGA",
            "season": 2026,
            "start_date": "2026-06-18",
            "end_date": "2026-06-21",
            "course_id": "oakmont-country-club",
            "course_name": "Oakmont Country Club",
            "status": "starter",
            "source_provider": "starter seed",
            "source_url": "local-starter",
            "source_updated_at": "2026-06-21T00:00:00Z",
        })
        for player_id, name, country, rank, probability, why in STARTER_PLAYERS:
            if player_id == "ludvig-aberg":
                name = "Ludvig Aberg"
            upsert(conn, "players", {
                "player_id": player_id,
                "player_name": name,
                "country": country,
                "tour": "PGA",
                "handedness": "",
                "birth_year": None,
                "turned_pro": None,
                "source_provider": "starter seed",
                "source_url": "local-starter",
                "source_updated_at": "2026-06-21T00:00:00Z",
            })
            upsert(conn, "fields", {
                "field_id": f"starter-us-open-2026-{player_id}",
                "event_id": "starter-us-open-2026",
                "player_id": player_id,
                "player_name": name,
                "status": "active",
                "seed": rank,
                "tee_time": "",
                "source_provider": "starter seed",
                "source_url": "local-starter",
                "source_updated_at": "2026-06-21T00:00:00Z",
            })
            for round_number in range(1, 5):
                to_par = (rank - 3) * 0.35 + (round_number - 2) * 0.25
                round_id = f"{player_id}-starter-r{round_number}"
                upsert(conn, "rounds", {
                    "round_id": round_id,
                    "event_id": "starter-us-open-2026",
                    "player_id": player_id,
                    "course_id": "oakmont-country-club",
                    "round_number": round_number,
                    "round_date": f"2026-06-{17 + round_number}",
                    "score": round(70 + to_par),
                    "to_par": to_par,
                    "position": "",
                    "source_provider": "starter seed",
                    "source_url": "local-starter",
                    "source_updated_at": "2026-06-21T00:00:00Z",
                })
                upsert(conn, "strokes_gained", {
                    "sg_id": f"{round_id}-sg",
                    "round_id": round_id,
                    "event_id": "starter-us-open-2026",
                    "player_id": player_id,
                    "period": f"round-{round_number}",
                    "sg_total": -to_par,
                    "sg_t2g": -to_par * 0.8,
                    "sg_ott": -to_par * 0.25,
                    "sg_app": -to_par * 0.35,
                    "sg_arg": -to_par * 0.1,
                    "sg_putt": -to_par * 0.2,
                    "source_provider": "starter seed",
                    "source_url": "local-starter",
                    "source_updated_at": "2026-06-21T00:00:00Z",
                })
            upsert(conn, "model_predictions", {
                "prediction_id": f"starter-model-{player_id}-winner",
                "model_run_id": "starter-model-run",
                "event_id": "starter-us-open-2026",
                "player_id": player_id,
                "player_name": name,
                "market": "winner",
                "rank": rank,
                "probability": probability,
                "fair_odds_american": round((100 / probability) - 100) if probability > 0 else None,
                "edge_probability": 0.012 if rank <= 3 else -0.004,
                "projected_to_par": (rank - 4) * 0.7,
                "confidence": "High" if rank <= 3 else "Medium",
                "plain_english": why,
                "risk_flags": "Putting volatility" if rank in (3, 4) else "Price sensitivity",
                "created_at": "2026-06-21T00:00:00Z",
                "source_provider": "starter model",
                "source_url": "local-starter",
                "source_updated_at": "2026-06-21T00:00:00Z",
            })
        for round_number, wind in [(1, 9), (2, 14), (3, 18), (4, 11)]:
            upsert(conn, "weather_snapshots", {
                "weather_id": f"starter-weather-r{round_number}",
                "event_id": "starter-us-open-2026",
                "course_id": "oakmont-country-club",
                "round_number": round_number,
                "forecast_at": f"2026-06-{17 + round_number}T12:00:00Z",
                "temperature_f": 76,
                "wind_mph": wind,
                "wind_direction": "W",
                "precip_probability": 0.15,
                "condition": "Firm, championship test",
                "source_provider": "starter seed",
                "source_url": "local-starter",
                "source_updated_at": "2026-06-21T00:00:00Z",
            })
        upsert(conn, "source_fetches", {
            "fetch_id": "starter-seed",
            "provider": "starter seed",
            "endpoint": "local",
            "event_id": "starter-us-open-2026",
            "model_run_id": "starter-model-run",
            "fetched_at": "2026-06-21T00:00:00Z",
            "status": "ok",
            "row_count": 6,
            "source_url": "local-starter",
            "notes": "Development seed. Replace with warehouse import for real data.",
        })
        import_player_skill_snapshots(conn)
        conn.commit()
        return {"eventId": "starter-us-open-2026", "players": len(STARTER_PLAYERS)}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Golf Lab SQLite warehouse.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--seed-starter", action="store_true", help="Create a small local starter database.")
    parser.add_argument("--from-warehouse", type=Path, help="Import from a Golf Lab CSV warehouse folder.")
    parser.add_argument("--event-id", default="")
    parser.add_argument("--player-limit", type=int, default=80)
    parser.add_argument("--rounds-per-player", type=int, default=40)
    args = parser.parse_args()

    if args.seed_starter:
        result = seed_starter(args.db)
    elif args.from_warehouse:
        result = import_from_warehouse(args.from_warehouse, args.db, args.event_id, args.player_limit, args.rounds_per_player)
    else:
        parser.error("Choose --seed-starter or --from-warehouse.")
        return
    print(result)


if __name__ == "__main__":
    main()
