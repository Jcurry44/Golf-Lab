from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app_common import DEFAULT_DB, ROOT, connect, one, rows_to_dicts
from golf_lab_analytics import selected_event


RESULTS_DIR = ROOT / "results"
LEGACY_RESULTS_DIR = ROOT / "data" / "results"
ESPN_SCOREBOARD_SOURCE = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard?dates=20260622"


MODEL_VARIANTS: dict[str, dict[str, Any]] = {
    "balanced": {
        "label": "Balanced Lab",
        "description": "Current Golf Lab blend: baseline talent, recent scorecards, major form, tough-course form, and skill lanes.",
        "weights": {
            "skill_total": 116,
            "career_perf": 74,
            "recent_perf": 64,
            "season_perf": 46,
            "major_perf": 29,
            "tough_perf": 24,
            "t2g": 21,
            "approach": 13,
            "off_tee": 8,
            "around_green": 4,
            "putting": 6,
            "distance": 0.85,
            "gir": 150,
        },
        "probability_scale": 260.0,
    },
    "baseline_talent": {
        "label": "Talent Anchor",
        "description": "Leans hardest on long-run skill, tee-to-green quality, and career scoring baseline.",
        "weights": {
            "skill_total": 145,
            "career_perf": 94,
            "recent_perf": 36,
            "season_perf": 34,
            "major_perf": 20,
            "tough_perf": 14,
            "t2g": 26,
            "approach": 10,
            "off_tee": 8,
            "around_green": 3,
            "putting": 4,
            "distance": 0.75,
            "gir": 110,
        },
        "probability_scale": 270.0,
    },
    "recent_form": {
        "label": "Recent Form",
        "description": "Pushes the last 24 pre-event rounds and same-season performance higher in the stack.",
        "weights": {
            "skill_total": 86,
            "career_perf": 50,
            "recent_perf": 116,
            "season_perf": 72,
            "major_perf": 24,
            "tough_perf": 24,
            "t2g": 18,
            "approach": 10,
            "off_tee": 6,
            "around_green": 5,
            "putting": 7,
            "distance": 0.65,
            "gir": 120,
        },
        "probability_scale": 250.0,
    },
    "course_fit": {
        "label": "Course Fit",
        "description": "Weights majors, hard-course scoring, approach profile, and course-proof samples more aggressively.",
        "weights": {
            "skill_total": 82,
            "career_perf": 54,
            "recent_perf": 58,
            "season_perf": 38,
            "major_perf": 58,
            "tough_perf": 52,
            "t2g": 20,
            "approach": 17,
            "off_tee": 8,
            "around_green": 7,
            "putting": 8,
            "distance": 0.95,
            "gir": 190,
        },
        "probability_scale": 255.0,
    },
    "short_game_risk": {
        "label": "Short-Game Risk",
        "description": "Adds more protection for putting, around-the-green play, and volatility on harder setups.",
        "weights": {
            "skill_total": 84,
            "career_perf": 54,
            "recent_perf": 70,
            "season_perf": 45,
            "major_perf": 36,
            "tough_perf": 36,
            "t2g": 16,
            "approach": 10,
            "off_tee": 5,
            "around_green": 15,
            "putting": 20,
            "distance": 0.45,
            "gir": 95,
        },
        "probability_scale": 255.0,
    },
    "championship_blend": {
        "label": "Championship Blend",
        "description": "A harder-event blend that pulls baseline talent down and lifts recent, major, and tough-course evidence.",
        "weights": {
            "skill_total": 96,
            "career_perf": 58,
            "recent_perf": 84,
            "season_perf": 52,
            "major_perf": 46,
            "tough_perf": 40,
            "t2g": 22,
            "approach": 14,
            "off_tee": 8,
            "around_green": 8,
            "putting": 8,
            "distance": 0.78,
            "gir": 160,
        },
        "probability_scale": 255.0,
    },
    "contender_guard": {
        "label": "Contender Guard",
        "description": "Championship Blend plus missed-cut, blow-up, and volatility guardrails to reduce false-elite top-20 names.",
        "weights": {
            "skill_total": 94,
            "career_perf": 56,
            "recent_perf": 82,
            "season_perf": 50,
            "major_perf": 45,
            "tough_perf": 39,
            "t2g": 22,
            "approach": 14,
            "off_tee": 8,
            "around_green": 9,
            "putting": 9,
            "distance": 0.76,
            "gir": 155,
            "missed_cut_rate": 42,
            "recent_missed_cut_rate": 34,
            "blowup_rate": 95,
            "volatility": 7,
            "par_or_better_rate": 42,
            "cut_stability": 16,
        },
        "probability_scale": 255.0,
    },
    "hard_course_guard": {
        "label": "Hard-Course Guard",
        "description": "Short-game and risk-aware blend for difficult scoring setups where avoiding the blow-up matters.",
        "weights": {
            "skill_total": 82,
            "career_perf": 52,
            "recent_perf": 74,
            "season_perf": 46,
            "major_perf": 40,
            "tough_perf": 43,
            "t2g": 17,
            "approach": 12,
            "off_tee": 5,
            "around_green": 17,
            "putting": 19,
            "distance": 0.48,
            "gir": 105,
            "missed_cut_rate": 36,
            "recent_missed_cut_rate": 40,
            "blowup_rate": 120,
            "volatility": 9,
            "par_or_better_rate": 50,
            "cut_stability": 20,
        },
        "probability_scale": 255.0,
    },
    "fit_form_guard": {
        "label": "Fit + Form Guard",
        "description": "Heavier recent-form and course-fit blend with same-course history, tough-course evidence, and light cut-risk protection.",
        "weights": {
            "skill_total": 82,
            "career_perf": 46,
            "recent_perf": 105,
            "season_perf": 62,
            "major_perf": 40,
            "tough_perf": 48,
            "course_history_perf": 46,
            "t2g": 18,
            "approach": 17,
            "off_tee": 7,
            "around_green": 10,
            "putting": 9,
            "distance": 0.74,
            "gir": 175,
            "missed_cut_rate": 22,
            "recent_missed_cut_rate": 24,
            "blowup_rate": 70,
            "volatility": 5,
            "par_or_better_rate": 58,
            "cut_stability": 18,
        },
        "probability_scale": 250.0,
    },
}

DEFAULT_AUDIT_LOOKBACK_DAYS = 730


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 2) -> float | None:
    numeric = _num(value)
    return round(numeric, digits) if numeric is not None else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _slugify_name(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def _score_to_par(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("\u2212", "-")
    if not text or text.lower() in {"e", "even"}:
        return 0.0
    text = text.replace("+", "")
    try:
        return float(text)
    except ValueError:
        return None


def _signed(value: Any, digits: int = 1) -> str:
    numeric = _num(value)
    if numeric is None:
        return "--"
    return f"{numeric:+.{digits}f}"


def _pct(value: Any) -> str:
    numeric = _num(value)
    if numeric is None:
        return "--"
    return f"{numeric * 100:.1f}%"


def _major_clause(alias: str = "e") -> str:
    return f"""
    (
      lower({alias}.event_name) like '%u.s. open%'
      or lower({alias}.event_name) like '%us open%'
      or lower({alias}.event_name) like '%masters%'
      or lower({alias}.event_name) like '%pga championship%'
      or lower({alias}.event_name) like '%open championship%'
      or lower({alias}.event_name) = 'the open'
      or lower({alias}.event_name) like '%the open%'
    )
    """


def trim_espn_scoreboard(payload: dict[str, Any], local_event_id: str, source_url: str = ESPN_SCOREBOARD_SOURCE) -> dict[str, Any]:
    event = None
    for candidate in payload.get("events", []):
        if local_event_id.endswith(str(candidate.get("id") or "")):
            event = candidate
            break
    if event is None and payload.get("events"):
        event = payload["events"][0]
    if event is None:
        raise ValueError("No ESPN event found in scoreboard payload.")

    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    rows: list[dict[str, Any]] = []
    for competitor in competitors:
        athlete = competitor.get("athlete") or {}
        player_name = athlete.get("displayName") or athlete.get("fullName") or competitor.get("id") or ""
        total_to_par = _score_to_par(competitor.get("score"))
        rounds: list[dict[str, Any]] = []
        for line in competitor.get("linescores") or []:
            period = line.get("period")
            if not isinstance(period, int) or period < 1 or period > 4:
                continue
            rounds.append({
                "round_number": period,
                "score": int(line["value"]) if _num(line.get("value")) is not None else None,
                "to_par": _score_to_par(line.get("displayValue")),
                "label": line.get("displayValue"),
            })
        rounds.sort(key=lambda row: row["round_number"])
        rows.append({
            "player_name": player_name,
            "player_id_guess": _slugify_name(player_name),
            "country": ((athlete.get("flag") or {}).get("alt") or "").strip() or None,
            "espn_order": competitor.get("order"),
            "total_to_par": total_to_par,
            "total_label": competitor.get("score"),
            "made_cut": len(rounds) >= 4,
            "rounds": rounds,
        })

    _assign_actual_ranks(rows)
    return {
        "source_provider": "ESPN public scoreboard",
        "source_url": source_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "event": {
            "local_event_id": local_event_id,
            "espn_event_id": event.get("id"),
            "event_name": event.get("name"),
            "status": ((event.get("status") or {}).get("type") or {}).get("description"),
            "completed": ((event.get("status") or {}).get("type") or {}).get("completed"),
            "start_date": competition.get("startDate") or event.get("date"),
            "end_date": competition.get("endDate"),
        },
        "rows": rows,
    }


def _assign_actual_ranks(rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda row: (
        0 if row.get("made_cut") else 1,
        row.get("total_to_par") if row.get("total_to_par") is not None else 999,
        row.get("espn_order") or 9999,
        row.get("player_name") or "",
    ))
    for row in rows:
        row["actual_rank"] = None
        row["finish_label"] = "MC" if row.get("total_to_par") is not None else "WD"

    made_cut_rows = [row for row in rows if row.get("made_cut") and row.get("total_to_par") is not None]
    score_counts: dict[float, int] = {}
    for row in made_cut_rows:
        score = float(row["total_to_par"])
        score_counts[score] = score_counts.get(score, 0) + 1

    last_score: float | None = None
    current_rank = 0
    for index, row in enumerate(made_cut_rows, start=1):
        score = float(row["total_to_par"])
        if last_score is None or float(score) != last_score:
            current_rank = index
            last_score = score
        row["actual_rank"] = current_rank
        tied = score_counts.get(score, 0) > 1
        row["finish_label"] = f"T{current_rank}" if tied else str(current_rank)


def write_trimmed_espn_result(raw_path: Path, out_path: Path, event_id: str) -> dict[str, Any]:
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    result = trim_espn_scoreboard(payload, event_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _cached_result_path(event_id: str) -> Path:
    return RESULTS_DIR / f"{event_id}.json"


def _load_cached_results(event_id: str) -> dict[str, Any] | None:
    for path in (_cached_result_path(event_id), LEGACY_RESULTS_DIR / f"{event_id}.json"):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def _db_actual_results(conn: sqlite3.Connection, event: dict[str, Any]) -> dict[str, Any]:
    rows = rows_to_dicts(
        conn.execute(
            """
            select p.player_name,
                   p.player_id as player_id_guess,
                   p.country,
                   count(r.round_id) as round_count,
                   sum(r.to_par) as total_to_par,
                   sum(case when r.score between 55 and 95 then 1 else 0 end) as scoring_rounds
            from rounds r
            join players p on p.player_id = r.player_id
            where r.event_id = ?
              and r.to_par is not null
            group by p.player_id, p.player_name, p.country
            order by sum(r.to_par), p.player_name
            """,
            (event["event_id"],),
        ).fetchall()
    )
    for row in rows:
        row["made_cut"] = (row.get("round_count") or 0) >= 4
        row["rounds"] = []
    _assign_actual_ranks(rows)
    return {
        "source_provider": "Golf Lab scorecard warehouse",
        "source_url": event.get("source_url"),
        "fetched_at": None,
        "event": {
            "local_event_id": event.get("event_id"),
            "event_name": event.get("event_name"),
            "status": event.get("status"),
            "completed": str(event.get("status") or "").lower() in {"final", "complete", "completed"},
        },
        "rows": rows,
    }


def _field_rows(conn: sqlite3.Connection, event: dict[str, Any]) -> list[dict[str, Any]]:
    rows = rows_to_dicts(
        conn.execute(
            """
            select f.player_id,
                   coalesce(f.player_name, p.player_name) as player_name,
                   p.country,
                   f.seed
            from fields f
            left join players p on p.player_id = f.player_id
            where f.event_id = ?
            order by coalesce(f.seed, 9999), coalesce(f.player_name, p.player_name)
            """,
            (event["event_id"],),
        ).fetchall()
    )
    if rows:
        return rows
    return rows_to_dicts(
        conn.execute(
            """
            select distinct r.player_id, p.player_name, p.country, null as seed
            from rounds r
            join players p on p.player_id = r.player_id
            where r.event_id = ?
            order by p.player_name
            """,
            (event["event_id"],),
        ).fetchall()
    )


def _profile_maps(conn: sqlite3.Connection, event: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    event_id = event["event_id"]
    start_date = event.get("start_date") or "9999-12-31"
    season = int(event.get("season") or 9999)
    course_id = event.get("course_id") or ""
    major_clause = _major_clause("e")
    maps: dict[str, dict[str, dict[str, Any]]] = {}

    queries = {
        "career": (
            """
            select r.player_id,
                   count(r.round_id) as rounds,
                   sum(case when r.score between 55 and 95 then 1 else 0 end) as scoring_rounds,
                   avg(case when r.score between 55 and 95 then r.score end) as scoring_average,
                   avg(r.to_par) as avg_to_par,
                   avg(sg.sg_total) as avg_sg_total,
                   avg(sg.sg_t2g) as sg_t2g,
                   avg(sg.sg_ott) as sg_ott,
                   avg(sg.sg_app) as sg_app,
                   avg(sg.sg_arg) as sg_arg,
                   avg(sg.sg_putt) as sg_putt,
                   max(r.round_date) as last_round
            from rounds r
            left join strokes_gained sg on sg.round_id = r.round_id
            where r.event_id != ?
              and r.round_date < ?
              and r.to_par is not null
            group by r.player_id
            """,
            (event_id, start_date),
        ),
        "season": (
            """
            select r.player_id,
                   count(r.round_id) as rounds,
                   avg(r.to_par) as avg_to_par,
                   avg(sg.sg_total) as avg_sg_total,
                   avg(sg.sg_t2g) as sg_t2g,
                   avg(sg.sg_app) as sg_app
            from rounds r
            join events e on e.event_id = r.event_id
            left join strokes_gained sg on sg.round_id = r.round_id
            where r.event_id != ?
              and r.round_date < ?
              and e.season = ?
              and r.to_par is not null
            group by r.player_id
            """,
            (event_id, start_date, season),
        ),
        "major": (
            f"""
            select r.player_id,
                   count(r.round_id) as rounds,
                   count(distinct e.event_id) as events,
                   avg(r.to_par) as avg_to_par,
                   avg(sg.sg_total) as avg_sg_total
            from rounds r
            join events e on e.event_id = r.event_id
            left join strokes_gained sg on sg.round_id = r.round_id
            where r.event_id != ?
              and r.round_date < ?
              and r.to_par is not null
              and {major_clause}
            group by r.player_id
            """,
            (event_id, start_date),
        ),
        "tough": (
            """
            select r.player_id,
                   count(r.round_id) as rounds,
                   avg(r.to_par) as avg_to_par,
                   avg(sg.sg_total) as avg_sg_total
            from rounds r
            join course_difficulty cd on cd.course_id = r.course_id
            left join strokes_gained sg on sg.round_id = r.round_id
            where r.event_id != ?
              and r.round_date < ?
              and r.to_par is not null
              and cd.difficulty_bucket in ('brutal', 'tough')
            group by r.player_id
            """,
            (event_id, start_date),
        ),
        "course_history": (
            """
            select r.player_id,
                   count(r.round_id) as rounds,
                   avg(r.to_par) as avg_to_par,
                   avg(sg.sg_total) as avg_sg_total
            from rounds r
            left join strokes_gained sg on sg.round_id = r.round_id
            where r.event_id != ?
              and r.round_date < ?
              and r.course_id = ?
              and r.to_par is not null
            group by r.player_id
            """,
            (event_id, start_date, course_id),
        ),
        "recent": (
            """
            with eligible as (
              select r.player_id,
                     r.round_date,
                     r.round_number,
                     r.to_par,
                     sg.sg_total,
                     row_number() over (
                       partition by r.player_id
                       order by r.round_date desc, coalesce(r.round_number, 0) desc, r.round_id desc
                     ) as recent_rank
              from rounds r
              left join strokes_gained sg on sg.round_id = r.round_id
              where r.event_id != ?
                and r.round_date < ?
                and r.to_par is not null
            )
            select player_id,
                   count(*) as rounds,
                   avg(to_par) as avg_to_par,
                   avg(sg_total) as avg_sg_total
            from eligible
            where recent_rank <= 24
            group by player_id
            """,
            (event_id, start_date),
        ),
        "skill": (
            """
            with season_skill as (
              select player_id,
                     cast(substr(period, 8) as integer) as season,
                     sg_total,
                     sg_t2g,
                     sg_ott,
                     sg_app,
                     sg_arg,
                     sg_putt,
                     driving_distance,
                     accuracy,
                     gir,
                     scrambling
              from strokes_gained
              where round_id is null
                and period like 'season-%'
                and cast(substr(period, 8) as integer) < ?
            )
            select player_id,
                   count(*) as seasons,
                   avg(sg_total) as sg_total,
                   avg(sg_t2g) as sg_t2g,
                   avg(sg_ott) as sg_ott,
                   avg(sg_app) as sg_app,
                   avg(sg_arg) as sg_arg,
                   avg(sg_putt) as sg_putt,
                   avg(driving_distance) as driving_distance,
                   avg(accuracy) as accuracy,
                   avg(gir) as gir,
                   avg(scrambling) as scrambling,
                   max(season) as last_skill_season
            from season_skill
            group by player_id
            """,
            (season,),
        ),
        "risk": (
            """
            with event_quality as (
              select e.event_id,
                     e.start_date,
                     count(distinct r.player_id) as result_players,
                     count(distinct case when r.round_number = 4 and r.to_par is not null then r.player_id end) as r4_players
              from events e
              join rounds r on r.event_id = e.event_id
              where e.start_date < ?
                and e.event_id != ?
              group by e.event_id, e.start_date
            ),
            player_event_base as (
              select r.player_id,
                     e.event_id,
                     e.start_date,
                     count(r.round_id) as rounds,
                     sum(case when r.to_par >= 4 then 1 else 0 end) as blowup_rounds,
                     sum(case when r.to_par <= 0 then 1 else 0 end) as par_or_better_rounds,
                     avg(r.to_par) as avg_to_par,
                     avg(r.to_par * r.to_par) - avg(r.to_par) * avg(r.to_par) as to_par_variance,
                     case
                       when eq.result_players >= 90
                        and cast(eq.r4_players as real) / nullif(eq.result_players, 0) <= 0.88
                       then 1 else 0
                     end as cut_event
              from rounds r
              join events e on e.event_id = r.event_id
              join event_quality eq on eq.event_id = e.event_id
              where r.event_id != ?
                and e.start_date < ?
                and r.to_par is not null
              group by r.player_id, e.event_id, e.start_date, eq.result_players, eq.r4_players
            ),
            ranked as (
              select *,
                     case when cut_event = 1 and rounds < 4 then 1 else 0 end as missed_cut,
                     row_number() over (
                       partition by player_id
                       order by start_date desc, event_id desc
                     ) as event_rank
              from player_event_base
            )
            select player_id,
                   count(*) as events,
                   sum(cut_event) as cut_events,
                   sum(missed_cut) as missed_cuts,
                   cast(sum(missed_cut) as real) / nullif(sum(cut_event), 0) as missed_cut_rate,
                   cast(sum(case when event_rank <= 12 then missed_cut else 0 end) as real)
                     / nullif(sum(case when event_rank <= 12 then cut_event else 0 end), 0) as recent_missed_cut_rate,
                   cast(sum(blowup_rounds) as real) / nullif(sum(rounds), 0) as blowup_rate,
                   cast(sum(par_or_better_rounds) as real) / nullif(sum(rounds), 0) as par_or_better_rate,
                   avg(to_par_variance) as to_par_volatility,
                   avg(case when event_rank <= 8 then avg_to_par end) as recent_event_avg_to_par
            from ranked
            group by player_id
            """,
            (start_date, event_id, event_id, start_date),
        ),
    }
    for key, (sql, params) in queries.items():
        maps[key] = {row["player_id"]: row for row in rows_to_dicts(conn.execute(sql, params).fetchall())}
    return maps


def _performance(avg_sg: Any, avg_to_par: Any) -> float:
    sg = _num(avg_sg)
    if sg is not None:
        return sg
    to_par = _num(avg_to_par)
    if to_par is not None:
        return _clamp(-0.32 * to_par, -2.5, 2.5)
    return 0.0


def _variant_config(variant_key: str | None = None) -> dict[str, Any]:
    return MODEL_VARIANTS.get(variant_key or "balanced") or MODEL_VARIANTS["balanced"]


def model_variants() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": value["label"],
            "description": value["description"],
        }
        for key, value in MODEL_VARIANTS.items()
    ]


def _model_score(row: dict[str, Any], profiles: dict[str, dict[str, dict[str, Any]]], variant_key: str | None = None) -> tuple[float, dict[str, Any]]:
    player_id = row["player_id"]
    career = profiles["career"].get(player_id, {})
    season = profiles["season"].get(player_id, {})
    recent = profiles["recent"].get(player_id, {})
    major = profiles["major"].get(player_id, {})
    tough = profiles["tough"].get(player_id, {})
    course_history = profiles["course_history"].get(player_id, {})
    skill = profiles["skill"].get(player_id, {})
    risk = profiles["risk"].get(player_id, {})

    career_perf = _performance(career.get("avg_sg_total"), career.get("avg_to_par"))
    season_perf = _performance(season.get("avg_sg_total"), season.get("avg_to_par"))
    recent_perf = _performance(recent.get("avg_sg_total"), recent.get("avg_to_par"))
    major_perf = _performance(major.get("avg_sg_total"), major.get("avg_to_par"))
    tough_perf = _performance(tough.get("avg_sg_total"), tough.get("avg_to_par"))
    course_history_perf = _performance(course_history.get("avg_sg_total"), course_history.get("avg_to_par"))
    skill_total = _num(skill.get("sg_total")) or 0.0
    t2g = _num(skill.get("sg_t2g")) or _num(career.get("sg_t2g")) or 0.0
    approach = _num(skill.get("sg_app")) or _num(career.get("sg_app")) or 0.0
    off_tee = _num(skill.get("sg_ott")) or _num(career.get("sg_ott")) or 0.0
    around_green = _num(skill.get("sg_arg")) or _num(career.get("sg_arg")) or 0.0
    putting = _num(skill.get("sg_putt")) or _num(career.get("sg_putt")) or 0.0
    distance = _num(skill.get("driving_distance"))
    gir = _num(skill.get("gir"))
    missed_cut_rate = _num(risk.get("missed_cut_rate"))
    recent_missed_cut_rate = _num(risk.get("recent_missed_cut_rate"))
    blowup_rate = _num(risk.get("blowup_rate"))
    par_or_better_rate = _num(risk.get("par_or_better_rate"))
    volatility = _num(risk.get("to_par_volatility"))
    recent_event_avg_to_par = _num(risk.get("recent_event_avg_to_par"))
    weights = _variant_config(variant_key)["weights"]

    score = 1000.0
    score += skill_total * weights["skill_total"]
    score += career_perf * weights["career_perf"]
    score += recent_perf * weights["recent_perf"]
    score += season_perf * weights["season_perf"]
    score += major_perf * weights["major_perf"]
    score += tough_perf * weights["tough_perf"]
    score += course_history_perf * weights.get("course_history_perf", 0)
    score += t2g * weights["t2g"]
    score += approach * weights["approach"]
    score += off_tee * weights["off_tee"]
    score += around_green * weights["around_green"]
    score += putting * weights["putting"]
    if distance is not None:
        score += _clamp((distance - 292) * weights["distance"], -18, 28)
    if gir is not None:
        score += _clamp((gir - 0.655) * weights["gir"], -18, 24)
    if missed_cut_rate is not None:
        score -= missed_cut_rate * weights.get("missed_cut_rate", 0)
    if recent_missed_cut_rate is not None:
        score -= recent_missed_cut_rate * weights.get("recent_missed_cut_rate", 0)
    elif missed_cut_rate is not None:
        score -= missed_cut_rate * weights.get("recent_missed_cut_rate", 0) * 0.45
    if blowup_rate is not None:
        score -= max(0.0, blowup_rate - 0.065) * weights.get("blowup_rate", 0)
    if volatility is not None:
        score -= max(0.0, volatility - 5.35) * weights.get("volatility", 0)
    if par_or_better_rate is not None:
        score += max(0.0, par_or_better_rate - 0.68) * weights.get("par_or_better_rate", 0)
    if recent_missed_cut_rate is not None:
        score += max(0.0, 0.12 - recent_missed_cut_rate) * weights.get("cut_stability", 0)

    career_rounds = _num(career.get("rounds")) or 0
    recent_rounds = _num(recent.get("rounds")) or 0
    major_rounds = _num(major.get("rounds")) or 0
    tough_rounds = _num(tough.get("rounds")) or 0
    course_history_rounds = _num(course_history.get("rounds")) or 0
    skill_seasons = _num(skill.get("seasons")) or 0
    score += min(career_rounds, 220) * 0.08
    score += min(recent_rounds, 24) * 0.45
    score += min(major_rounds, 60) * 0.18
    score += min(tough_rounds, 80) * 0.10
    score += min(course_history_rounds, 24) * (0.18 if weights.get("course_history_perf", 0) else 0)
    score += min(skill_seasons, 3) * 6
    if career_rounds < 12:
        score -= 42
    if recent_rounds < 8:
        score -= 20
    if not skill:
        score -= 28

    features = {
        "career_rounds": int(career_rounds),
        "recent_rounds": int(recent_rounds),
        "major_rounds": int(major_rounds),
        "tough_rounds": int(tough_rounds),
        "course_history_rounds": int(course_history_rounds),
        "skill_seasons": int(skill_seasons),
        "career_sg": _round(career_perf, 2),
        "recent_sg": _round(recent_perf, 2),
        "season_sg": _round(season_perf, 2),
        "major_sg": _round(major_perf, 2),
        "tough_sg": _round(tough_perf, 2),
        "course_history_sg": _round(course_history_perf, 2),
        "prior_skill_sg": _round(skill_total, 2),
        "sg_t2g": _round(t2g, 2),
        "sg_app": _round(approach, 2),
        "sg_ott": _round(off_tee, 2),
        "sg_arg": _round(around_green, 2),
        "sg_putt": _round(putting, 2),
        "driving_distance": _round(distance, 1),
        "gir": _round(gir, 4),
        "missed_cut_rate": _round(missed_cut_rate, 3),
        "recent_missed_cut_rate": _round(recent_missed_cut_rate, 3),
        "blowup_rate": _round(blowup_rate, 3),
        "par_or_better_rate": _round(par_or_better_rate, 3),
        "to_par_volatility": _round(volatility, 2),
        "recent_event_avg_to_par": _round(recent_event_avg_to_par, 2),
        "scoring_average": _round(career.get("scoring_average"), 2),
        "last_round": career.get("last_round"),
    }
    return score, features


def _profile_reason(row: dict[str, Any]) -> str:
    features = row["features"]
    pieces: list[str] = []
    if _num(features.get("prior_skill_sg")) is not None and abs(features.get("prior_skill_sg") or 0) >= 0.8:
        pieces.append(f"prior-season SG was {_signed(features['prior_skill_sg'])}")
    if _num(features.get("recent_sg")) is not None and abs(features.get("recent_sg") or 0) >= 0.7:
        pieces.append(f"recent pre-event form was {_signed(features['recent_sg'])}")
    if _num(features.get("major_sg")) is not None and (features.get("major_rounds") or 0) >= 12:
        pieces.append(f"major sample carried {_signed(features['major_sg'])} SG over {features['major_rounds']} rounds")
    if _num(features.get("tough_sg")) is not None and (features.get("tough_rounds") or 0) >= 12:
        pieces.append(f"tough-course sample was {_signed(features['tough_sg'])}")
    if _num(features.get("course_history_sg")) is not None and (features.get("course_history_rounds") or 0) >= 4:
        pieces.append(f"same-course history was {_signed(features['course_history_sg'])} over {features['course_history_rounds']} rounds")
    if _num(features.get("recent_missed_cut_rate")) is not None and (features.get("recent_missed_cut_rate") or 0) >= 0.25:
        pieces.append(f"recent cut-risk was elevated at {features['recent_missed_cut_rate'] * 100:.0f}%")
    elif _num(features.get("recent_missed_cut_rate")) is not None and (features.get("recent_missed_cut_rate") or 0) <= 0.05:
        pieces.append("recent cut-risk was clean")
    if _num(features.get("sg_t2g")) is not None and abs(features.get("sg_t2g") or 0) >= 0.8:
        pieces.append(f"tee-to-green profile sat at {_signed(features['sg_t2g'])}")
    if _num(features.get("par_or_better_rate")) is not None and (features.get("par_or_better_rate") or 0) >= 0.78:
        pieces.append(f"par-or-better rate was {features['par_or_better_rate'] * 100:.0f}%")
    if _num(features.get("driving_distance")) is not None and (features.get("driving_distance") or 0) >= 305:
        pieces.append(f"distance checked in at {features['driving_distance']:.1f} yards")
    if not pieces:
        pieces.append("projection leaned on scorecard baseline and available field context")
    return "Reconstructed model liked him because " + "; ".join(pieces[:3]) + "."


def _result_reason(row: dict[str, Any]) -> str:
    projected = row.get("projected_rank")
    actual = row.get("actual_rank")
    finish = row.get("finish_label") or "--"
    name = row.get("player_name") or "Player"
    if finish == "MC":
        if projected and projected <= 20:
            return f"Miss: projected inside the top 20, but missed the cut; the pre-event baseline overrated the week."
        return "Missed cut, which kept the profile out of the final leaderboard conversation."
    if actual == 1:
        if projected and projected <= 20:
            return f"Worked: {name} was inside the pre-event contender pool and converted it into the win."
        return f"Missed the winner: {name} finished first, but the pre-event profile did not grade as a core win candidate."
    if projected and projected <= 10 and actual and actual <= 10:
        return f"Worked: projected top 10 and finished {finish}; the profile translated into the event."
    if projected and projected <= 20 and actual and actual <= 20:
        return f"Worked directionally: projected top 20 and finished {finish}, so the contender signal held."
    if projected and projected <= 10 and (actual is None or actual > 30):
        return f"Miss: projected as a core play, but finished {finish}; the pre-event baseline overrated the week."
    if projected and ((actual and actual <= 5 and projected > 25) or (actual and actual <= 10 and projected > 35)):
        return f"Surprise: finished {finish} from outside the model core, a result the pre-event board did not price aggressively."
    if actual is None:
        return "No settled finish was available in the result source."
    return f"Finished {finish} versus a projected rank of #{projected}; close enough for context but not a headline call."


def _finish_sort(row: dict[str, Any]) -> tuple[int, int]:
    actual = row.get("actual_rank")
    return (actual if actual is not None else 999, row.get("projected_rank") or 999)


def _project_event_rows(
    event: dict[str, Any],
    field: list[dict[str, Any]],
    profiles: dict[str, dict[str, dict[str, Any]]],
    result_payload: dict[str, Any],
    variant_key: str | None = None,
) -> list[dict[str, Any]]:
    result_rows = result_payload.get("rows") or []
    actual_by_id = {row.get("player_id_guess"): row for row in result_rows if row.get("player_id_guess")}
    actual_by_name = {_slugify_name(row.get("player_name") or ""): row for row in result_rows}

    rows: list[dict[str, Any]] = []
    for field_row in field:
        score, features = _model_score(field_row, profiles, variant_key)
        player_id = field_row["player_id"]
        actual = actual_by_id.get(player_id) or actual_by_name.get(_slugify_name(field_row.get("player_name") or ""))
        rows.append({
            "player_id": player_id,
            "player_name": field_row.get("player_name") or player_id,
            "country": field_row.get("country"),
            "model_score": round(score, 2),
            "features": features,
            "actual_rank": actual.get("actual_rank") if actual else None,
            "finish_label": actual.get("finish_label") if actual else None,
            "actual_to_par": actual.get("total_to_par") if actual else None,
            "actual_to_par_label": actual.get("total_label") if actual else None,
            "made_cut": actual.get("made_cut") if actual else None,
        })
    rows.sort(key=lambda row: (-row["model_score"], row["player_name"]))

    max_score = max((row["model_score"] for row in rows), default=0)
    probability_scale = float(_variant_config(variant_key).get("probability_scale") or 260.0)
    weights = [math.exp((row["model_score"] - max_score) / probability_scale) for row in rows]
    total_weight = sum(weights) or 1.0
    mean_score = statistics.mean([row["model_score"] for row in rows]) if rows else 0
    std_score = statistics.pstdev([row["model_score"] for row in rows]) if len(rows) > 1 else 1
    std_score = std_score or 1
    for index, row in enumerate(rows, start=1):
        row["projected_rank"] = index
        row["pre_event_win_probability"] = round(weights[index - 1] / total_weight, 4)
        z_score = (row["model_score"] - mean_score) / std_score
        row["projected_to_par"] = round(3.1 - (z_score * 2.5), 1)
        row["profile_reason"] = _profile_reason(row)
        row["result_reason"] = _result_reason(row)
    return rows


def _review_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    winner = next((row for row in rows if row.get("actual_rank") == 1), None)
    projected_top10 = rows[:10]
    projected_top20 = rows[:20]
    top10_hits = [row for row in projected_top10 if row.get("actual_rank") is not None and row["actual_rank"] <= 10 and row.get("made_cut")]
    top20_hits = [row for row in projected_top20 if row.get("actual_rank") is not None and row["actual_rank"] <= 20 and row.get("made_cut")]
    rank_errors = [
        abs((row.get("actual_rank") or 999) - row["projected_rank"])
        for row in projected_top20
        if row.get("actual_rank") is not None and row.get("made_cut")
    ]
    best_calls = sorted(
        [row for row in rows if row["projected_rank"] <= 25 and row.get("actual_rank") is not None and row["actual_rank"] <= 20],
        key=lambda row: ((row.get("actual_rank") or 99), row["projected_rank"]),
    )[:8]
    misses = sorted(
        [row for row in rows if row["projected_rank"] <= 20 and (row.get("actual_rank") is None or row.get("actual_rank", 999) > 35 or not row.get("made_cut"))],
        key=lambda row: row["projected_rank"],
    )[:8]
    surprises = sorted(
        [row for row in rows if row.get("actual_rank") is not None and row["actual_rank"] <= 10 and row["projected_rank"] > 25],
        key=_finish_sort,
    )[:8]
    actual_top10 = sorted(
        [row for row in rows if row.get("actual_rank") is not None and row["actual_rank"] <= 10],
        key=_finish_sort,
    )[:12]
    made_cut_misses_top20 = [row for row in projected_top20 if row.get("finish_label") == "MC" or not row.get("made_cut")]
    actual_top10_captured_top20 = [
        row for row in rows
        if row.get("actual_rank") is not None and row["actual_rank"] <= 10 and row["projected_rank"] <= 20
    ]
    return {
        "summary": {
            "field_size": len(rows),
            "winner": winner.get("player_name") if winner else None,
            "winner_projected_rank": winner.get("projected_rank") if winner else None,
            "winner_probability": winner.get("pre_event_win_probability") if winner else None,
            "winner_to_par": winner.get("actual_to_par") if winner else None,
            "top10_hits": len(top10_hits),
            "top20_hits": len(top20_hits),
            "top10_hit_rate": round(len(top10_hits) / 10, 3) if projected_top10 else None,
            "top20_hit_rate": round(len(top20_hits) / 20, 3) if projected_top20 else None,
            "actual_top10_captured_top20": len(actual_top10_captured_top20),
            "missed_cut_top20": len(made_cut_misses_top20),
            "avg_projected_top20_rank_error": round(sum(rank_errors) / len(rank_errors), 1) if rank_errors else None,
        },
        "boards": {
            "projected_top10": projected_top10,
            "best_calls": best_calls,
            "misses": misses,
            "surprises": surprises,
            "actual_top10": actual_top10,
        },
    }


def _review_payload(
    conn: sqlite3.Connection,
    event: dict[str, Any],
    result_payload: dict[str, Any],
    rows: list[dict[str, Any]],
    variant_key: str | None = None,
) -> dict[str, Any]:
    variant_key = variant_key or "balanced"
    review = _review_summary(rows)
    review["summary"]["saved_model_min_created_at"] = _prediction_run_boundary(conn, event["event_id"], "min")
    review["summary"]["saved_model_max_created_at"] = _prediction_run_boundary(conn, event["event_id"], "max")

    return {
        "event": event,
        "variant": {
            "key": variant_key,
            "label": _variant_config(variant_key)["label"],
            "description": _variant_config(variant_key)["description"],
        },
        "variants": model_variants(),
        "mode": "reconstructed_pre_tournament",
        "archived_snapshot": False,
        "as_of": event.get("start_date"),
        "caveat": (
            "No archived pre-tournament model run was saved. This review reconstructs the board using only scorecards "
            f"and prior-season stat snapshots available before {event.get('start_date') or 'the event start'}."
        ),
        "source": {
            "provider": result_payload.get("source_provider"),
            "url": result_payload.get("source_url"),
            "fetched_at": result_payload.get("fetched_at"),
        },
        "summary": review["summary"],
        "boards": review["boards"],
        "rows": rows,
    }


def prediction_review(conn: sqlite3.Connection, event_id: str | None = None, variant_key: str | None = None) -> dict[str, Any]:
    event = selected_event(conn, event_id)
    if not event:
        return {"event": None, "rows": [], "summary": {}, "boards": {}, "variants": model_variants()}
    result_payload = _load_cached_results(event["event_id"]) or _db_actual_results(conn, event)
    field = _field_rows(conn, event)
    profiles = _profile_maps(conn, event)
    rows = _project_event_rows(event, field, profiles, result_payload, variant_key)
    return _review_payload(conn, event, result_payload, rows, variant_key)


def _parse_iso_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _default_audit_window(conn: sqlite3.Connection, event_id: str | None = None) -> tuple[str, str]:
    event = selected_event(conn, event_id)
    until_date = _parse_iso_date((event or {}).get("start_date")) or datetime(2026, 6, 18)
    since_date = until_date - timedelta(days=DEFAULT_AUDIT_LOOKBACK_DAYS)
    return since_date.date().isoformat(), until_date.date().isoformat()


def _is_major_name(event_name: str) -> bool:
    text = event_name.lower()
    return (
        "u.s. open" in text
        or "us open" in text
        or "masters" in text
        or "pga championship" in text
        or "open championship" in text
        or text == "the open"
        or "the open" in text
    )


def _is_premium_name(event_name: str) -> bool:
    text = event_name.lower()
    premium_terms = [
        "players championship",
        "memorial",
        "arnold palmer",
        "genesis invitational",
        "pebble beach",
        "rbc heritage",
        "truist championship",
        "travelers championship",
        "fedex st. jude",
        "cadillac championship",
        "tour championship",
        "playoff",
    ]
    return any(term in text for term in premium_terms)


def _event_tags(row: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    event_name = row.get("event_name") or ""
    if _is_major_name(event_name):
        tags.append("major")
    if _is_premium_name(event_name):
        tags.append("premium")
    avg_to_par = _num(row.get("event_avg_to_par"))
    cut_rate = _num(row.get("cut_rate"))
    if avg_to_par is not None and avg_to_par >= 0:
        tags.append("hard")
    if cut_rate is not None and cut_rate <= 0.58:
        tags.append("cut-pressure")
    if not tags:
        tags.append("standard")
    if any(tag in tags for tag in ("major", "premium", "hard", "cut-pressure")):
        tags.append("similar")
    return tags


def _candidate_events(
    conn: sqlite3.Connection,
    since: str,
    until: str,
    limit: int = 40,
    focus: str = "similar",
    min_result_players: int = 60,
    min_scoring_rounds: int = 220,
    min_r4_players: int = 45,
) -> list[dict[str, Any]]:
    rows = rows_to_dicts(
        conn.execute(
            """
            with event_quality as (
              select e.*,
                     c.location,
                     c.par,
                     c.yards,
                     count(distinct f.player_id) as field_players,
                     count(distinct r.player_id) as result_players,
                     count(r.round_id) as rounds,
                     sum(case when r.score between 55 and 95 then 1 else 0 end) as scoring_rounds,
                     sum(case when r.to_par is not null then 1 else 0 end) as to_par_rows,
                     count(distinct case when r.round_number = 4 and r.to_par is not null then r.player_id end) as r4_players,
                     round(avg(r.to_par), 2) as event_avg_to_par,
                     round(avg(case when r.score between 55 and 95 then r.score end), 2) as event_scoring_average
              from events e
              left join courses c on c.course_id = e.course_id
              left join fields f on f.event_id = e.event_id
              left join rounds r on r.event_id = e.event_id
              where e.start_date >= ?
                and e.start_date < ?
                and coalesce(e.status, '') not like '%In Progress%'
                and lower(e.event_name) not like '%q-school%'
              group by e.event_id
            )
            select *,
                   cast(r4_players as real) / nullif(result_players, 0) as cut_rate
            from event_quality
            where result_players >= ?
              and scoring_rounds >= ?
              and to_par_rows >= ?
              and r4_players >= ?
            order by start_date desc
            limit ?
            """,
            (since, until, min_result_players, min_scoring_rounds, min_scoring_rounds, min_r4_players, limit * 3),
        ).fetchall()
    )
    tagged: list[dict[str, Any]] = []
    for row in rows:
        row["tags"] = _event_tags(row)
        row["field_size"] = max(row.get("field_players") or 0, row.get("result_players") or 0)
        if focus and focus != "all" and focus not in row["tags"]:
            continue
        tagged.append(row)
        if len(tagged) >= limit:
            break
    return tagged


def _single_event_variant_score(summary: dict[str, Any]) -> float:
    winner_rank = summary.get("winner_projected_rank") or 999
    winner_bonus = 0.0
    if winner_rank <= 10:
        winner_bonus = 8.0
    elif winner_rank <= 20:
        winner_bonus = 5.0
    elif winner_rank <= 35:
        winner_bonus = 2.0
    rank_error = _num(summary.get("avg_projected_top20_rank_error")) or 35.0
    score = 0.0
    score += (summary.get("top10_hits") or 0) * 3.0
    score += (summary.get("top20_hits") or 0) * 2.0
    score += (summary.get("actual_top10_captured_top20") or 0) * 1.4
    score += winner_bonus
    score -= (summary.get("missed_cut_top20") or 0) * 1.7
    score -= rank_error * 0.08
    return round(score, 2)


def _aggregate_variant_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_variant.setdefault(row["variant_key"], []).append(row)
    aggregates: list[dict[str, Any]] = []
    for variant_key, variant_rows in by_variant.items():
        events = len(variant_rows)
        top10_hits = sum(row["top10_hits"] for row in variant_rows)
        top20_hits = sum(row["top20_hits"] for row in variant_rows)
        winner_top10 = sum(1 for row in variant_rows if row["winner_projected_rank"] and row["winner_projected_rank"] <= 10)
        winner_top20 = sum(1 for row in variant_rows if row["winner_projected_rank"] and row["winner_projected_rank"] <= 20)
        missed_cut_top20 = sum(row["missed_cut_top20"] for row in variant_rows)
        rank_errors = [row["avg_projected_top20_rank_error"] for row in variant_rows if row["avg_projected_top20_rank_error"] is not None]
        model_score = sum(row["variant_score"] for row in variant_rows) / events if events else 0
        config = _variant_config(variant_key)
        aggregates.append({
            "variant_key": variant_key,
            "label": config["label"],
            "description": config["description"],
            "events": events,
            "model_score": round(model_score, 2),
            "top10_hit_rate": round(top10_hits / (events * 10), 3) if events else None,
            "top20_hit_rate": round(top20_hits / (events * 20), 3) if events else None,
            "avg_top10_hits": round(top10_hits / events, 2) if events else None,
            "avg_top20_hits": round(top20_hits / events, 2) if events else None,
            "winner_top10_rate": round(winner_top10 / events, 3) if events else None,
            "winner_top20_rate": round(winner_top20 / events, 3) if events else None,
            "missed_cut_top20_per_event": round(missed_cut_top20 / events, 2) if events else None,
            "avg_projected_top20_rank_error": round(sum(rank_errors) / len(rank_errors), 1) if rank_errors else None,
        })
    aggregates.sort(key=lambda row: (
        row["model_score"],
        row.get("top20_hit_rate") or 0,
        -(row.get("missed_cut_top20_per_event") or 99),
    ), reverse=True)
    return aggregates


def _group_aggregates(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for group in ("all", "similar", "major", "premium", "hard", "cut-pressure"):
        scoped = [row for row in rows if group == "all" or group in row.get("tags", [])]
        if scoped:
            groups[group] = _aggregate_variant_results(scoped)
    return groups


def _model_lessons(aggregates: list[dict[str, Any]], groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    if not aggregates:
        return []
    leader = aggregates[0]
    balanced = next((row for row in aggregates if row["variant_key"] == "balanced"), None)
    lessons: list[dict[str, str]] = [{
        "title": "Best current weighting",
        "body": f"{leader['label']} graded best across the walk-forward sample at {leader['avg_top20_hits']} projected top-20 hits per event.",
    }]
    if balanced and leader["variant_key"] != "balanced":
        top20_delta = (leader.get("avg_top20_hits") or 0) - (balanced.get("avg_top20_hits") or 0)
        cut_delta = (balanced.get("missed_cut_top20_per_event") or 0) - (leader.get("missed_cut_top20_per_event") or 0)
        leak_word = "reduced" if cut_delta >= 0 else "increased"
        lessons.append({
            "title": "Weighting change signal",
            "body": (
                f"Compared with Balanced Lab, {leader['label']} gained {top20_delta:+.2f} top-20 hits per event "
                f"and {leak_word} missed-cut leakage by {abs(cut_delta):.2f} projected top-20 players per event."
            ),
        })
    near_leaders = [
        row for row in aggregates
        if (row.get("avg_top20_hits") or 0) >= (leader.get("avg_top20_hits") or 0) - 0.15
    ]
    guard = min(near_leaders, key=lambda row: row.get("missed_cut_top20_per_event") or 99) if near_leaders else None
    if guard and guard["variant_key"] != leader["variant_key"]:
        leak_delta = (leader.get("missed_cut_top20_per_event") or 0) - (guard.get("missed_cut_top20_per_event") or 0)
        lessons.append({
            "title": "False-elite guardrail",
            "body": (
                f"{guard['label']} stayed within 0.15 top-20 hits of the leader while reducing projected top-20 missed-cut "
                f"leakage by {leak_delta:.2f} players per event."
            ),
        })
    fit_form = next((row for row in aggregates if row["variant_key"] == "fit_form_guard"), None)
    if fit_form and balanced:
        top20_delta = (fit_form.get("avg_top20_hits") or 0) - (balanced.get("avg_top20_hits") or 0)
        cut_delta = (balanced.get("missed_cut_top20_per_event") or 0) - (fit_form.get("missed_cut_top20_per_event") or 0)
        lessons.append({
            "title": "Fit + form test",
            "body": (
                f"Heavier recent-form and same-course weighting gained {top20_delta:+.2f} top-20 hits per event "
                f"and reduced missed-cut leakage by {cut_delta:.2f}; useful, but not the best overall blend."
            ),
        })
    major_leader = (groups.get("major") or [None])[0]
    if major_leader:
        lessons.append({
            "title": "Major setup read",
            "body": f"For majors, {major_leader['label']} is the leading blend, which tells us whether championship history and hard-course samples deserve more weight.",
        })
    hard_leader = (groups.get("hard") or [None])[0]
    if hard_leader:
        lessons.append({
            "title": "Hard-course read",
            "body": f"On harder-scoring events, {hard_leader['label']} led the board; that is the clearest place to test course-fit and short-game upgrades.",
        })
    return lessons[:5]


def historical_prediction_audit(
    conn: sqlite3.Connection,
    since: str | None = None,
    until: str | None = None,
    limit: int = 32,
    focus: str = "similar",
    min_result_players: int = 60,
    min_scoring_rounds: int = 220,
    min_r4_players: int = 45,
) -> dict[str, Any]:
    default_since, default_until = _default_audit_window(conn)
    since = since or default_since
    until = until or default_until
    events = _candidate_events(
        conn,
        since,
        until,
        limit=limit,
        focus=focus,
        min_result_players=min_result_players,
        min_scoring_rounds=min_scoring_rounds,
        min_r4_players=min_r4_players,
    )
    variant_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []

    for event in events:
        result_payload = _load_cached_results(event["event_id"]) or _db_actual_results(conn, event)
        field = _field_rows(conn, event)
        profiles = _profile_maps(conn, event)
        per_event_variants: list[dict[str, Any]] = []
        for variant_key in MODEL_VARIANTS:
            rows = _project_event_rows(event, field, profiles, result_payload, variant_key)
            summary = _review_summary(rows)["summary"]
            variant_score = _single_event_variant_score(summary)
            variant_result = {
                "event_id": event["event_id"],
                "event_name": event["event_name"],
                "start_date": event.get("start_date"),
                "tags": event.get("tags", []),
                "variant_key": variant_key,
                "variant_label": _variant_config(variant_key)["label"],
                "variant_score": variant_score,
                "winner": summary.get("winner"),
                "winner_projected_rank": summary.get("winner_projected_rank"),
                "winner_probability": summary.get("winner_probability"),
                "top10_hits": summary.get("top10_hits") or 0,
                "top20_hits": summary.get("top20_hits") or 0,
                "top10_hit_rate": summary.get("top10_hit_rate"),
                "top20_hit_rate": summary.get("top20_hit_rate"),
                "actual_top10_captured_top20": summary.get("actual_top10_captured_top20") or 0,
                "missed_cut_top20": summary.get("missed_cut_top20") or 0,
                "avg_projected_top20_rank_error": summary.get("avg_projected_top20_rank_error"),
            }
            variant_rows.append(variant_result)
            per_event_variants.append(variant_result)
        per_event_variants.sort(key=lambda row: row["variant_score"], reverse=True)
        balanced = next((row for row in per_event_variants if row["variant_key"] == "balanced"), per_event_variants[0])
        best = per_event_variants[0]
        event_rows.append({
            "event_id": event["event_id"],
            "event_name": event["event_name"],
            "start_date": event.get("start_date"),
            "course_name": event.get("course_name"),
            "field_size": event.get("field_size"),
            "event_avg_to_par": event.get("event_avg_to_par"),
            "tags": event.get("tags", []),
            "winner": balanced.get("winner"),
            "balanced_winner_projected_rank": balanced.get("winner_projected_rank"),
            "balanced_top20_hits": balanced.get("top20_hits"),
            "balanced_missed_cut_top20": balanced.get("missed_cut_top20"),
            "best_variant_key": best["variant_key"],
            "best_variant_label": best["variant_label"],
            "best_variant_score": best["variant_score"],
            "best_top20_hits": best["top20_hits"],
            "best_winner_projected_rank": best["winner_projected_rank"],
        })

    aggregates = _aggregate_variant_results(variant_rows)
    groups = _group_aggregates(variant_rows)
    return {
        "mode": "walk_forward_reconstructed",
        "focus": focus,
        "since": since,
        "until": until,
        "event_count": len(events),
        "available_events": len(events),
        "variants": model_variants(),
        "variant_summary": aggregates,
        "groups": groups,
        "lessons": _model_lessons(aggregates, groups),
        "events": event_rows,
        "caveat": (
            "Every event is reconstructed walk-forward: the model can use only scorecards and public stat snapshots "
            "dated before that tournament start. This is designed for weighting research, not as an archived betting ledger."
        ),
    }


def _prediction_run_boundary(conn: sqlite3.Connection, event_id: str, boundary: str) -> str | None:
    func = "min" if boundary == "min" else "max"
    row = one(conn, f"select {func}(created_at) as created_at from model_predictions where event_id = ?", (event_id,))
    return row.get("created_at") if row else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Golf Lab result caches and reconstructed prediction reviews.")
    parser.add_argument("--raw-espn", type=Path, help="Raw ESPN scoreboard JSON to trim.")
    parser.add_argument("--event-id", default="2026-u-s-open-401811952")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--review", action="store_true", help="Print reconstructed review summary.")
    parser.add_argument("--audit", action="store_true", help="Print historical walk-forward variant summary.")
    parser.add_argument("--audit-limit", type=int, default=40)
    parser.add_argument("--focus", default="similar")
    args = parser.parse_args()

    if args.raw_espn:
        out = args.out or _cached_result_path(args.event_id)
        result = write_trimmed_espn_result(args.raw_espn, out, args.event_id)
        print(json.dumps({"rows": len(result["rows"]), "out": str(out)}, indent=2))

    if args.review:
        with connect(args.db, readonly=True) as conn:
            review = prediction_review(conn, args.event_id)
        print(json.dumps(review["summary"], indent=2, default=str))

    if args.audit:
        with connect(args.db, readonly=True) as conn:
            audit = historical_prediction_audit(conn, limit=args.audit_limit, focus=args.focus)
        print(json.dumps({
            "event_count": audit["event_count"],
            "since": audit["since"],
            "until": audit["until"],
            "leader": audit["variant_summary"][0] if audit["variant_summary"] else None,
            "variants": audit["variant_summary"],
            "lessons": audit["lessons"],
        }, indent=2, default=str))


if __name__ == "__main__":
    main()
