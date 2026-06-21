from __future__ import annotations

import sqlite3
from typing import Any

from app_common import one, rows_to_dicts, table_payload


LATEST_MODEL_CTE = """
with latest_prediction as (
  select prediction_id
  from (
    select prediction_id,
           row_number() over (
             partition by event_id, player_id, market
             order by coalesce(created_at, source_updated_at, '') desc,
                      coalesce(rank, 9999),
                      prediction_id desc
           ) as row_number
    from model_predictions
  )
  where row_number = 1
),
latest_model as (
  select mp.*
  from model_predictions mp
  join latest_prediction lp on lp.prediction_id = mp.prediction_id
)
"""


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _generated_reason(row: dict[str, Any]) -> str:
    saved = (row.get("plain_english") or "").strip()
    if saved:
        return saved
    pieces: list[str] = []
    rank = _num(row.get("rank"))
    probability = _num(row.get("probability"))
    if probability is None:
        probability_pct = _num(row.get("probability_pct"))
        probability = probability_pct / 100.0 if probability_pct is not None else None
    avg_sg = _num(row.get("avg_sg_total"))
    avg_to_par = _num(row.get("avg_to_par"))
    edge = _num(row.get("edge_probability"))

    if rank is not None and rank <= 5:
        pieces.append("Model sees him as a top-tier win profile")
    elif rank is not None and rank <= 20:
        pieces.append("Model keeps him in the live contender tier")
    elif rank is not None:
        pieces.append("Model needs a cleaner path than the market leaders")

    if avg_sg is not None:
        if avg_sg >= 1.0:
            pieces.append(f"Recent scorecards show a strong {avg_sg:+.2f} strokes-gained baseline")
        elif avg_sg >= 0:
            pieces.append(f"Recent scorecards are positive at {avg_sg:+.2f} strokes gained")
        else:
            pieces.append(f"Recent scorecards trail the field at {avg_sg:+.2f} strokes gained")
    elif avg_to_par is not None:
        pieces.append(f"Recent rounds average {avg_to_par:+.2f} to par")

    if probability is not None:
        pieces.append(f"Win probability sits at {probability * 100:.1f}%")
    if edge is not None:
        if edge > 0.02:
            pieces.append("Price is better than the model number")
        elif edge < -0.10:
            pieces.append("Market price is richer than our projection")
        else:
            pieces.append("Price is close to fair value")

    return ". ".join(pieces[:4]) + "." if pieces else "Model read is pending more player and market context."


def _enrich_reasoning(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        row["plain_english"] = _generated_reason(row)
    return rows


def database_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = {
        table: conn.execute(f"select count(*) from {table}").fetchone()[0]
        for table in [
            "players",
            "events",
            "courses",
            "fields",
            "rounds",
            "strokes_gained",
            "weather_snapshots",
            "odds_snapshots",
            "model_predictions",
            "source_fetches",
        ]
    }
    event = one(
        conn,
        """
        select event_id, event_name, start_date, end_date, course_name, status
        from events
        order by coalesce(end_date, start_date) desc
        limit 1
        """,
    )
    latest_fetch = one(
        conn,
        """
        select provider, endpoint, fetched_at, status, row_count
        from source_fetches
        order by fetched_at desc
        limit 1
        """,
    )
    return {
        "counts": counts,
        "selectedEvent": event,
        "latestFetch": latest_fetch,
        "readiness": readiness_label(counts),
    }


def readiness_label(counts: dict[str, int]) -> str:
    if counts.get("players") and counts.get("rounds") and counts.get("model_predictions"):
        return "model-ready"
    if counts.get("players") and counts.get("rounds"):
        return "analysis-ready"
    if counts.get("players") or counts.get("events"):
        return "partial"
    return "empty"


def event_board(conn: sqlite3.Connection, event_id: str | None = None) -> dict[str, Any]:
    event = selected_event(conn, event_id)
    if not event:
        return {"event": None, "field": [], "weather": [], "model": []}
    event_id = event["event_id"]
    field = rows_to_dicts(
        conn.execute(
            f"""
            {LATEST_MODEL_CTE}
            select f.player_id, p.player_name, f.status, f.seed,
                   rf.rounds, rf.avg_to_par, rf.avg_sg_total,
                   mp.rank, mp.probability, mp.confidence
            from fields f
            join players p on p.player_id = f.player_id
            left join player_recent_form rf on rf.player_id = f.player_id
            left join latest_model mp
              on mp.event_id = f.event_id and mp.player_id = f.player_id and mp.market = 'winner'
            where f.event_id = ?
            order by coalesce(mp.rank, 9999), coalesce(f.seed, 9999), p.player_name
            limit 80
            """,
            (event_id,),
        ).fetchall()
    )
    weather = rows_to_dicts(
        conn.execute(
            """
            select round_number, forecast_at, temperature_f, wind_mph,
                   precip_probability, condition
            from weather_snapshots
            where event_id = ?
            order by round_number, forecast_at
            limit 20
            """,
            (event_id,),
        ).fetchall()
    )
    model = model_board(conn, event_id, limit=20)["rows"]
    return {"event": event, "field": field, "weather": weather, "model": model}


def selected_event(conn: sqlite3.Connection, event_id: str | None = None) -> dict[str, Any] | None:
    if event_id:
        found = one(
            conn,
            """
            select e.*, c.location, c.par, c.yards
            from events e
            left join courses c on c.course_id = e.course_id
            where e.event_id = ?
            """,
            (event_id,),
        )
        if found:
            return found
    return one(
        conn,
        """
        select e.*, c.location, c.par, c.yards
        from events e
        left join courses c on c.course_id = e.course_id
        where exists (
          select 1 from model_predictions mp where mp.event_id = e.event_id
        )
        order by coalesce(e.end_date, e.start_date) desc
        limit 1
        """,
    ) or one(
        conn,
        """
        select e.*, c.location, c.par, c.yards
        from events e
        left join courses c on c.course_id = e.course_id
        order by coalesce(e.end_date, e.start_date) desc
        limit 1
        """,
    )


def player_cards(conn: sqlite3.Connection, event_id: str | None = None, limit: int = 24) -> dict[str, Any]:
    event = selected_event(conn, event_id)
    modeled_event_id = event["event_id"] if event else ""
    rows = conn.execute(
        f"""
        {LATEST_MODEL_CTE}
        select
          p.player_id,
          p.player_name,
          p.country,
          rf.rounds,
          rf.avg_to_par,
          rf.avg_sg_total,
          ps.driving_distance,
          ps.accuracy,
          ps.gir,
          ps.scrambling,
          mp.rank,
          mp.probability,
          mp.edge_probability,
          mp.projected_to_par,
          coalesce(
            mp.confidence,
            case
              when coalesce(rf.rounds, 0) >= 20 then 'Profile'
              when coalesce(rf.rounds, 0) > 0 then 'Thin data'
              else 'Watch'
            end
          ) as confidence,
          mp.plain_english,
          mp.risk_flags,
          case when mp.player_id is null then 0 else 1 end as modeled
        from players p
        left join player_recent_form rf on rf.player_id = p.player_id
        left join player_skill_snapshots ps on ps.player_id = p.player_id
        left join latest_model mp
          on mp.player_id = p.player_id
         and mp.market = 'winner'
         and (? = '' or mp.event_id = ?)
        where coalesce(rf.rounds, 0) > 0
           or ps.player_id is not null
           or mp.player_id is not null
        order by
          case when mp.rank is null then 1 else 0 end,
          coalesce(mp.rank, 9999),
          coalesce(rf.last_round, '') desc,
          coalesce(rf.avg_sg_total, -999) desc,
          p.player_name
        limit ?
        """,
        (modeled_event_id, modeled_event_id, limit),
    ).fetchall()
    return {"event": event, "rows": _enrich_reasoning(rows_to_dicts(rows))}


def player_card(conn: sqlite3.Connection, player_id: str, event_id: str | None = None) -> dict[str, Any]:
    player = one(
        conn,
        """
        with recent as (
          select r.player_id,
                 count(r.round_id) as rounds,
                 round(avg(r.to_par), 2) as avg_to_par,
                 round(avg(sg.sg_total), 2) as avg_sg_total,
                 min(r.round_date) as first_round,
                 max(r.round_date) as last_round
          from rounds r
          left join strokes_gained sg on sg.round_id = r.round_id
          where r.player_id = ?
          group by r.player_id
        )
        select p.*, rf.rounds, rf.avg_to_par, rf.avg_sg_total, rf.first_round, rf.last_round,
               ps.sg_t2g, ps.sg_ott, ps.sg_app, ps.sg_arg, ps.sg_putt,
               ps.driving_distance, ps.accuracy, ps.gir, ps.scrambling
        from players p
        left join recent rf on rf.player_id = p.player_id
        left join player_skill_snapshots ps on ps.player_id = p.player_id
        where p.player_id = ?
        """,
        (player_id, player_id),
    )
    if not player:
        raise ValueError("Unknown player")
    rounds = table_payload(
        conn,
        """
        select r.round_date, e.event_name, coalesce(c.course_name, r.course_id) as course,
               r.round_number, r.score, r.to_par, sg.sg_total, sg.sg_t2g, sg.sg_putt
        from rounds r
        left join events e on e.event_id = r.event_id
        left join courses c on c.course_id = r.course_id
        left join strokes_gained sg on sg.round_id = r.round_id
        where r.player_id = ?
        order by r.round_date desc, r.round_number desc
        limit 24
        """,
        (player_id,),
    )
    best_courses = table_payload(
        conn,
        """
        select coalesce(c.course_name, r.course_id) as course, count(*) as rounds,
               round(avg(r.to_par), 2) as avg_to_par, round(avg(sg.sg_total), 2) as avg_sg
        from rounds r
        left join courses c on c.course_id = r.course_id
        left join strokes_gained sg on sg.round_id = r.round_id
        where r.player_id = ?
        group by coalesce(c.course_name, r.course_id)
        having count(*) >= 2
        order by avg_to_par asc
        limit 6
        """,
        (player_id,),
    )
    model = one(
        conn,
        f"""
        {LATEST_MODEL_CTE}
        select event_id, market, rank, probability, fair_odds_american,
               edge_probability, projected_to_par, confidence, plain_english, risk_flags
        from latest_model
        where player_id = ?
          and (? = '' or event_id = ?)
        order by created_at desc, rank
        limit 1
        """,
        (player_id, event_id or "", event_id or ""),
    )
    if model:
        model["avg_sg_total"] = player.get("avg_sg_total")
        model["avg_to_par"] = player.get("avg_to_par")
        model["plain_english"] = _generated_reason(model)
    return {"player": player, "rounds": rounds, "bestCourses": best_courses, "model": model}


def course_cards(conn: sqlite3.Connection, limit: int = 18) -> dict[str, Any]:
    return table_payload(
        conn,
        """
        select cd.course_id, cd.course_name, cd.location, cd.rounds,
               cd.avg_to_par, cd.avg_sg_total, cd.difficulty_bucket,
               c.par, c.yards
        from course_difficulty cd
        join courses c on c.course_id = cd.course_id
        where cd.rounds > 0
        order by cd.avg_to_par desc, cd.rounds desc
        limit ?
        """,
        (limit,),
    )


def course_card(conn: sqlite3.Connection, course_id: str) -> dict[str, Any]:
    course = one(
        conn,
        """
        select c.*, cd.rounds, cd.avg_to_par, cd.avg_sg_total, cd.difficulty_bucket
        from courses c
        left join course_difficulty cd on cd.course_id = c.course_id
        where c.course_id = ?
        """,
        (course_id,),
    )
    if not course:
        raise ValueError("Unknown course")
    fits = table_payload(
        conn,
        """
        select p.player_id, p.player_name, count(*) as rounds,
               round(avg(r.to_par), 2) as avg_to_par,
               round(avg(sg.sg_total), 2) as avg_sg
        from rounds r
        join players p on p.player_id = r.player_id
        left join strokes_gained sg on sg.round_id = r.round_id
        where r.course_id = ?
        group by p.player_id, p.player_name
        having count(*) >= 2
        order by avg_to_par asc, avg_sg desc
        limit 12
        """,
        (course_id,),
    )
    setups = table_payload(
        conn,
        """
        select e.event_name, e.season, cs.par, cs.yards, cs.difficulty_score,
               cs.difficulty_bucket, cs.rough, cs.green_speed
        from course_setups cs
        left join events e on e.event_id = cs.event_id
        where cs.course_id = ?
        order by e.season desc, e.start_date desc
        limit 10
        """,
        (course_id,),
    )
    return {"course": course, "fits": fits, "setups": setups}


def model_board(conn: sqlite3.Connection, event_id: str | None = None, limit: int = 30) -> dict[str, Any]:
    event = selected_event(conn, event_id)
    if not event:
        return {"event": None, "rows": []}
    rows = conn.execute(
        f"""
        {LATEST_MODEL_CTE},
        latest_odds as (
          select event_id, player_id, market, book, odds_american, captured_at
          from (
            select event_id, player_id, market, book, odds_american, captured_at,
                   row_number() over (
                     partition by event_id, player_id, market
                     order by coalesce(captured_at, '') desc, odds_american desc
                   ) as row_number
            from odds_snapshots
          )
          where row_number = 1
        )
        select mp.rank, mp.player_id, p.player_name, mp.market,
               round(100.0 * mp.probability, 1) as probability_pct,
               mp.fair_odds_american, mp.edge_probability,
               mp.projected_to_par, mp.confidence, mp.plain_english, mp.risk_flags,
               rf.rounds, rf.avg_to_par, rf.avg_sg_total,
               best.book as best_book, best.odds_american as best_odds
        from latest_model mp
        join players p on p.player_id = mp.player_id
        left join player_recent_form rf on rf.player_id = p.player_id
        left join latest_odds best on best.event_id = mp.event_id
             and best.player_id = mp.player_id
             and best.market = mp.market
        where mp.event_id = ?
          and mp.market = 'winner'
        order by coalesce(mp.rank, 9999), p.player_name
        limit ?
        """,
        (event["event_id"], limit),
    ).fetchall()
    return {"event": event, "rows": _enrich_reasoning(rows_to_dicts(rows))}


def warehouse_health(conn: sqlite3.Connection) -> dict[str, Any]:
    summary = database_summary(conn)
    source_rows = rows_to_dicts(
        conn.execute(
            """
            select provider, endpoint, status, fetched_at, row_count
            from source_fetches
            order by fetched_at desc
            limit 20
            """
        ).fetchall()
    )
    blockers: list[str] = []
    counts = summary["counts"]
    if counts["players"] == 0:
        blockers.append("No players loaded")
    if counts["rounds"] == 0:
        blockers.append("No scorecards loaded")
    if counts["model_predictions"] == 0:
        blockers.append("No model predictions saved")
    if counts["source_fetches"] == 0:
        blockers.append("No source proof rows")
    return {
        "summary": summary,
        "sources": source_rows,
        "blockers": blockers,
        "grade": "premium-ready" if not blockers else ("analysis-ready" if counts["rounds"] else "setup"),
    }
