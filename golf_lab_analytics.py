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

_COURSE_DIFFICULTY_CACHE: dict[str, dict[str, dict[str, Any]]] = {}


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _market_text(market: Any) -> str:
    return (str(market or "winner").strip().lower() or "winner")


def _market_probability_label(market: Any) -> str:
    labels = {
        "winner": "Win probability",
        "top 10": "Top-10 probability",
        "top 20": "Top-20 probability",
        "make cut": "Make-cut probability",
    }
    text = _market_text(market)
    return labels.get(text, f"{text.title()} probability")


def _market_profile_label(market: Any) -> str:
    labels = {
        "winner": "win",
        "top 10": "top-10",
        "top 20": "top-20",
        "make cut": "make-cut",
    }
    text = _market_text(market)
    return labels.get(text, text.replace(" ", "-"))


def _generated_reason(row: dict[str, Any]) -> str:
    market = _market_text(row.get("market"))
    probability_label = _market_probability_label(market)
    profile_label = _market_profile_label(market)
    saved = (row.get("plain_english") or "").strip()
    if saved:
        if market != "winner":
            saved = saved.replace("Win probability sits", f"{probability_label} sits")
            saved = saved.replace("top-tier win profile", f"top-tier {profile_label} profile")
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
        pieces.append(f"Model sees him as a top-tier {profile_label} profile")
    elif rank is not None and rank <= 20:
        if market == "winner":
            pieces.append("Model keeps him in the live contender tier")
        else:
            pieces.append(f"Model keeps him in the strong {profile_label} tier")
    elif rank is not None:
        if market == "winner":
            pieces.append("Model needs a cleaner path than the market leaders")
        else:
            pieces.append(f"Model needs more separation from the {profile_label} leaders")

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
        pieces.append(f"{probability_label} sits at {probability * 100:.1f}%")
    if edge is not None:
        if edge > 0.02:
            pieces.append("Price is better than the model number")
        elif edge < -0.10:
            pieces.append("Market price is richer than our projection")
        else:
            pieces.append("Price is close to fair value")

    return ". ".join(pieces[:4]) + "." if pieces else "Model read is pending more player and market context."


def _signed_text(value: Any, digits: int = 1) -> str:
    numeric = _num(value)
    if numeric is None:
        return "--"
    return f"{numeric:+.{digits}f}"


def _pct_text(value: Any) -> str:
    numeric = _num(value)
    if numeric is None:
        return "--"
    return f"{numeric * 100:.1f}%"


def _avg_metric(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [_num(row.get(field)) for row in rows]
    numeric = [value for value in values if value is not None]
    return sum(numeric) / len(numeric) if numeric else None


def _weighted_avg_metric(rows: list[dict[str, Any]], field: str, weight_field: str = "rounds") -> float | None:
    numerator = 0.0
    denominator = 0.0
    for row in rows:
        value = _num(row.get(field))
        weight = _num(row.get(weight_field)) or 0
        if value is None or weight <= 0:
            continue
        numerator += value * weight
        denominator += weight
    return numerator / denominator if denominator else None


def _course_difficulty_lookup(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    db_file = conn.execute("pragma database_list").fetchone()["file"]
    cache_key = str(db_file or id(conn))
    if cache_key not in _COURSE_DIFFICULTY_CACHE:
        _COURSE_DIFFICULTY_CACHE[cache_key] = {
            row["course_id"]: dict(row)
            for row in conn.execute(
                """
                select course_id, course_name, rounds, avg_to_par, avg_sg_total, difficulty_bucket
                from course_difficulty
                """
            ).fetchall()
        }
    return _COURSE_DIFFICULTY_CACHE[cache_key]


def _player_difficulty_splits(conn: sqlite3.Connection, player_id: str) -> dict[str, Any]:
    lookup = _course_difficulty_lookup(conn)
    rounds = rows_to_dicts(
        conn.execute(
            """
            select r.course_id, r.to_par, sg.sg_total
            from rounds r
            left join strokes_gained sg on sg.round_id = r.round_id
            where r.player_id = ?
              and r.to_par is not null
            """,
            (player_id,),
        ).fetchall()
    )
    bucket_order = {"brutal": 1, "tough": 2, "balanced": 3, "gettable": 4}
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rounds:
        bucket = (lookup.get(row.get("course_id") or "", {}) or {}).get("difficulty_bucket") or "balanced"
        groups.setdefault(bucket, []).append(row)

    split_rows: list[dict[str, Any]] = []
    for bucket, bucket_rows in groups.items():
        to_par_values = [_num(row.get("to_par")) for row in bucket_rows]
        sg_values = [_num(row.get("sg_total")) for row in bucket_rows]
        to_par_numeric = [value for value in to_par_values if value is not None]
        sg_numeric = [value for value in sg_values if value is not None]
        par_or_better = sum(1 for value in to_par_numeric if value <= 0)
        split_rows.append({
            "bucket": bucket,
            "rounds": len(bucket_rows),
            "avg_to_par": round(sum(to_par_numeric) / len(to_par_numeric), 2) if to_par_numeric else None,
            "avg_sg": round(sum(sg_numeric) / len(sg_numeric), 2) if sg_numeric else None,
            "best_to_par": round(min(to_par_numeric), 2) if to_par_numeric else None,
            "worst_to_par": round(max(to_par_numeric), 2) if to_par_numeric else None,
            "par_or_better_rounds": par_or_better,
            "par_or_better_rate": round(par_or_better / len(to_par_numeric), 3) if to_par_numeric else None,
        })

    split_rows.sort(key=lambda row: bucket_order.get(row["bucket"], 5))
    return {
        "columns": [
            "bucket",
            "rounds",
            "avg_to_par",
            "avg_sg",
            "best_to_par",
            "worst_to_par",
            "par_or_better_rounds",
            "par_or_better_rate",
        ],
        "rows": split_rows,
    }


def _rich_profile_rows(seasons: dict[str, Any], limit: int = 4) -> list[dict[str, Any]]:
    rows = [
        row for row in seasons.get("rows", [])
        if any(row.get(field) is not None for field in [
            "avg_sg_total",
            "sg_t2g",
            "driving_distance",
            "accuracy",
            "gir",
            "scrambling",
            "scoring_average",
        ])
    ]
    return sorted(rows, key=lambda row: int(row.get("season") or 0), reverse=True)[:limit]


def _grade_band(value: Any, good: float, watch: float, lower_is_better: bool = False) -> str:
    numeric = _num(value)
    if numeric is None:
        return "Coverage watch"
    if lower_is_better:
        if numeric <= good:
            return "Plus"
        if numeric <= watch:
            return "Playable"
        return "Pressure point"
    if numeric >= good:
        return "Plus"
    if numeric >= watch:
        return "Playable"
    return "Pressure point"


def _course_dna(difficulty_splits: dict[str, Any], recent_vs_baseline: dict[str, Any] | None) -> dict[str, Any]:
    rows = difficulty_splits.get("rows", [])
    tough_rows = [row for row in rows if row.get("bucket") in {"brutal", "tough"}]
    gettable = next((row for row in rows if row.get("bucket") == "gettable"), None)
    tough_rounds = sum(int(row.get("rounds") or 0) for row in tough_rows)
    tough_to_par = _weighted_avg_metric(tough_rows, "avg_to_par")
    tough_sg = _weighted_avg_metric(tough_rows, "avg_sg")
    baseline_to_par = _num((recent_vs_baseline or {}).get("baseline_to_par"))
    gettable_to_par = _num(gettable.get("avg_to_par")) if gettable else None

    if tough_rounds:
        if tough_to_par is not None and baseline_to_par is not None and tough_to_par <= baseline_to_par:
            headline = "Tough-course profile travels"
        elif tough_to_par is not None:
            headline = "Tough-course sample is the stress test"
        else:
            headline = "Tough-course sample loaded"
        body = (
            f"On brutal/tough courses, imported scorecards average {_signed_text(tough_to_par)} to par "
            f"over {tough_rounds:,} rounds with {_signed_text(tough_sg)} SG."
        )
    else:
        headline = "Tough-course read needs more starts"
        body = "The warehouse has not loaded enough brutal/tough-course scorecards to grade this lane honestly."

    if gettable:
        body += (
            f" Gettable-course rounds average {_signed_text(gettable_to_par)} to par "
            f"across {int(gettable.get('rounds') or 0):,} rounds."
        )

    return {
        "headline": headline,
        "body": body,
        "toughRounds": tough_rounds,
        "toughAvgToPar": tough_to_par,
        "toughAvgSg": tough_sg,
        "gettableRounds": int(gettable.get("rounds") or 0) if gettable else 0,
        "gettableAvgToPar": gettable_to_par,
    }


def _major_summary(major_profile: dict[str, Any]) -> dict[str, Any]:
    rows = major_profile.get("rows", [])
    rounds = sum(int(row.get("rounds") or 0) for row in rows)
    events = sum(int(row.get("events") or 0) for row in rows)
    avg_to_par = _weighted_avg_metric(rows, "avg_to_par")
    avg_sg = _weighted_avg_metric(rows, "avg_sg")
    if rounds:
        headline = "Major sample loaded"
        body = f"Majors average {_signed_text(avg_to_par)} to par over {rounds:,} rounds across {events:,} event entries."
    else:
        headline = "Major profile pending"
        body = "No loaded major-championship scorecards are tied to this player yet."
    return {
        "headline": headline,
        "body": body,
        "rounds": rounds,
        "events": events,
        "avg_to_par": avg_to_par,
        "avg_sg": avg_sg,
    }


def _grade_explanations(
    player: dict[str, Any],
    seasons: dict[str, Any],
    recent_vs_baseline: dict[str, Any] | None,
    difficulty_splits: dict[str, Any],
) -> dict[str, dict[str, str]]:
    rich_rows = _rich_profile_rows(seasons)
    season_label = "latest rich public seasons"
    if rich_rows:
        years = [int(row["season"]) for row in rich_rows if row.get("season") is not None]
        if years:
            season_label = f"{min(years)}-{max(years)} rich public seasons" if min(years) != max(years) else f"{max(years)} rich public season"

    def metric(field: str, fallback_field: str | None = None) -> float | None:
        return _avg_metric(rich_rows, field) if rich_rows else _num(player.get(fallback_field or field))

    recent_sg = _num((recent_vs_baseline or {}).get("recent_sg"))
    baseline_sg = _num((recent_vs_baseline or {}).get("baseline_sg"))
    recent_rounds = int((recent_vs_baseline or {}).get("recent_rounds") or 0)
    all_rounds = int((recent_vs_baseline or {}).get("baseline_rounds") or player.get("rounds") or 0)
    tough = _course_dna(difficulty_splits, recent_vs_baseline)

    explanations = {
        "sg_total": {
            "label": "SG Total",
            "headline": _grade_band(metric("avg_sg_total"), 1.0, 0.0),
            "body": (
                f"Overall grade blends {season_label} with scorecard form. The rich profile sits at "
                f"{_signed_text(metric('avg_sg_total'))} SG total; recent imported rounds are "
                f"{_signed_text(recent_sg)} SG vs {_signed_text(baseline_sg)} across the full sample."
            ),
            "source": f"{len(rich_rows)} rich seasons, {all_rounds:,} imported scorecards",
        },
        "sg_t2g": {
            "label": "Tee to Green",
            "headline": _grade_band(metric("sg_t2g"), 0.8, 0.0),
            "body": f"Tee-to-green is the cleanest week-to-week skill base. The loaded rich profile averages {_signed_text(metric('sg_t2g'))} SG T2G.",
            "source": season_label,
        },
        "sg_ott": {
            "label": "Off Tee",
            "headline": _grade_band(metric("sg_ott"), 0.35, 0.0),
            "body": f"Off-tee value grades driver pressure and positional advantage. Current rich profile average: {_signed_text(metric('sg_ott'))} SG OTT.",
            "source": season_label,
        },
        "sg_app": {
            "label": "Approach",
            "headline": _grade_band(metric("sg_app"), 0.45, 0.0),
            "body": f"Approach is the model's preferred ball-striking signal for difficult courses. Loaded average: {_signed_text(metric('sg_app'))} SG APP.",
            "source": season_label,
        },
        "sg_arg": {
            "label": "Around Green",
            "headline": _grade_band(metric("sg_arg"), 0.15, -0.05),
            "body": f"Around-the-green grade captures survival when GIR drops. Loaded average: {_signed_text(metric('sg_arg'))} SG ARG.",
            "source": season_label,
        },
        "sg_putt": {
            "label": "Putting",
            "headline": _grade_band(metric("sg_putt"), 0.25, -0.05),
            "body": f"Putting is treated as volatile, so it explains upside and risk more than baseline talent. Loaded average: {_signed_text(metric('sg_putt'))} SG putting.",
            "source": season_label,
        },
        "driving_distance": {
            "label": "Distance",
            "headline": _grade_band(metric("driving_distance"), 305, 292),
            "body": f"Distance grades raw scoring ceiling on longer setups. Public profile average: {metric('driving_distance'):.1f} yards." if metric("driving_distance") is not None else "Distance is not loaded for this player yet.",
            "source": season_label,
        },
        "accuracy": {
            "label": "Fairways",
            "headline": _grade_band(metric("accuracy"), 0.65, 0.58),
            "body": f"Accuracy shows whether distance comes with enough control. Public profile average: {_pct_text(metric('accuracy'))} fairways hit.",
            "source": season_label,
        },
        "gir": {
            "label": "GIR",
            "headline": _grade_band(metric("gir"), 0.68, 0.62),
            "body": f"GIR is the plain-English iron-control read. Public profile average: {_pct_text(metric('gir'))} greens in regulation.",
            "source": season_label,
        },
        "scrambling": {
            "label": "Scramble",
            "headline": _grade_band(metric("scrambling"), 0.62, 0.56),
            "body": f"Scrambling matters most when the course is firm or the approach profile misses. Public profile average: {_pct_text(metric('scrambling'))}.",
            "source": season_label,
        },
        "scoring_average": {
            "label": "Scoring",
            "headline": _grade_band(metric("scoring_average"), 70.0, 71.5, lower_is_better=True),
            "body": f"Scoring average is raw output, not adjusted for course strength. Loaded profile average: {_signed_text(metric('avg_to_par'))} to par.",
            "source": f"{all_rounds:,} imported scorecards",
        },
        "scorecards": {
            "label": "Scorecards",
            "headline": "Sample strength" if all_rounds >= 120 else "Building sample",
            "body": (
                f"The card is powered by {all_rounds:,} imported scorecards. The last {recent_rounds:,} rounds are compared "
                f"against the full sample so form changes do not get mistaken for career skill."
            ),
            "source": "Golf Lab warehouse",
        },
        "course_dna": {
            "label": "Course DNA",
            "headline": tough["headline"],
            "body": tough["body"],
            "source": "Course difficulty buckets from loaded rounds",
        },
    }
    return explanations


def _model_tier(row: dict[str, Any]) -> str:
    rank = _num(row.get("rank"))
    probability_pct = _num(row.get("probability_pct"))
    if rank is not None and rank <= 8:
        return "Win Core"
    if rank is not None and rank <= 24:
        return "Contender Pool"
    if probability_pct is not None and probability_pct >= 1.0:
        return "Longshot With Signal"
    return "Volatility Watch"


def _model_tier_reason(row: dict[str, Any]) -> str:
    tier = row.get("tier") or _model_tier(row)
    rank = _num(row.get("rank"))
    projected = _num(row.get("projected_to_par"))
    avg_sg = _num(row.get("avg_sg_total"))
    edge = _num(row.get("edge_probability"))
    pieces = []
    if rank is not None:
        pieces.append(f"rank #{int(rank)}")
    if projected is not None:
        pieces.append(f"{projected:+.1f} projected to par")
    if avg_sg is not None:
        pieces.append(f"{avg_sg:+.1f} profile SG")
    if edge is not None and edge > 0:
        pieces.append("positive market edge")
    if not pieces:
        return f"{tier} because the model has enough saved signal to keep him on the board."
    return f"{tier}: " + ", ".join(pieces[:4]) + "."


def _enrich_reasoning(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        row["plain_english"] = _generated_reason(row)
        if "probability_pct" in row:
            row["tier"] = _model_tier(row)
            row["tier_reason"] = _model_tier_reason(row)
    return rows


def _stat_quality_checks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    def count(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0] or 0)

    scoring_total = count("select count(*) from rounds where score is not null")
    scoring_valid = count("select count(*) from rounds where score between 55 and 95")
    scoring_quarantined = scoring_total - scoring_valid
    scoring_profiles = count(
        """
        select count(*) from (
          select player_id
          from rounds
          group by player_id
          having sum(case when score between 55 and 95 then 1 else 0 end) >= 20
        )
        """
    )

    sg_total = count("select count(*) from strokes_gained where sg_total is not null")
    sg_invalid = count("select count(*) from strokes_gained where sg_total is not null and (sg_total < -20 or sg_total > 20)")

    distance_total = count("select count(*) from strokes_gained where driving_distance is not null")
    distance_invalid = count(
        "select count(*) from strokes_gained where driving_distance is not null and (driving_distance < 240 or driving_distance > 380)"
    )

    accuracy_total = count("select count(*) from strokes_gained where accuracy is not null")
    accuracy_invalid = count("select count(*) from strokes_gained where accuracy is not null and (accuracy < 0 or accuracy > 1)")
    gir_total = count("select count(*) from strokes_gained where gir is not null")
    gir_invalid = count("select count(*) from strokes_gained where gir is not null and (gir < 0 or gir > 1)")
    scrambling_total = count("select count(*) from strokes_gained where scrambling is not null")
    scrambling_invalid = count("select count(*) from strokes_gained where scrambling is not null and (scrambling < 0 or scrambling > 1)")

    major_profiles = count(
        """
        select count(*) from (
          select r.player_id
          from rounds r
          join events e on e.event_id = r.event_id
          where r.to_par is not null
            and (
              lower(e.event_name) like '%u.s. open%'
              or lower(e.event_name) like '%us open%'
              or lower(e.event_name) like '%masters%'
              or lower(e.event_name) like '%pga championship%'
              or lower(e.event_name) like '%open championship%'
              or lower(e.event_name) = 'the open'
              or lower(e.event_name) like '%the open%'
            )
          group by r.player_id
          having count(*) >= 8
        )
        """
    )
    tough_profiles = count(
        """
        select count(*) from (
          select r.player_id
          from rounds r
          join course_difficulty cd on cd.course_id = r.course_id
          where r.to_par is not null
            and cd.difficulty_bucket in ('brutal', 'tough')
          group by r.player_id
          having count(*) >= 8
        )
        """
    )

    skill_invalid = accuracy_invalid + gir_invalid + scrambling_invalid
    checks = [
        {
            "label": "Scoring average",
            "status": "good" if scoring_profiles >= 100 or (scoring_profiles and scoring_quarantined >= 0) else "watch",
            "value": scoring_profiles,
            "note": f"{scoring_profiles:,} qualified profiles; {scoring_quarantined:,} non-stroke-play rows quarantined",
            "contract": "Requires 20 scores from 55-95 before appearing on scoring boards.",
        },
        {
            "label": "Strokes gained",
            "status": "good" if sg_total and sg_invalid <= 5 else "bad" if sg_invalid else "watch",
            "value": sg_total,
            "note": f"{sg_total:,} rows; {sg_invalid:,} outside -20 to +20",
            "contract": "SG rows are checked for impossible outliers before they power model boards.",
        },
        {
            "label": "Driving distance",
            "status": "good" if distance_total and distance_invalid == 0 else "bad" if distance_invalid else "watch",
            "value": distance_total,
            "note": f"{distance_total:,} rows; {distance_invalid:,} outside 240-380 yards",
            "contract": "Distance leaderboards require a plausible PGA driving range.",
        },
        {
            "label": "Fairways / GIR / scrambling",
            "status": "good" if (accuracy_total or gir_total or scrambling_total) and skill_invalid == 0 else "bad" if skill_invalid else "watch",
            "value": accuracy_total + gir_total + scrambling_total,
            "note": f"{skill_invalid:,} percentage rows outside 0-100%",
            "contract": "Public percentage stats are stored as decimals from 0 to 1.",
        },
        {
            "label": "Major boards",
            "status": "good" if major_profiles >= 25 else "watch",
            "value": major_profiles,
            "note": f"{major_profiles:,} players with 8+ major rounds",
            "contract": "Major rankings require current/recent profiles and 8+ major rounds.",
        },
        {
            "label": "Tough-course boards",
            "status": "good" if tough_profiles >= 25 else "watch",
            "value": tough_profiles,
            "note": f"{tough_profiles:,} players with 8+ brutal/tough rounds",
            "contract": "Tough-course rankings require a real difficulty-bucket sample.",
        },
    ]
    return checks


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
        {LATEST_MODEL_CTE},
        career_rounds as (
          select r.player_id,
                 count(r.round_id) as rounds,
                 sum(case when r.score between 55 and 95 then 1 else 0 end) as scoring_rounds,
                 round(avg(case when r.score between 55 and 95 then r.score end), 2) as scoring_average,
                 round(avg(r.to_par), 2) as avg_to_par,
                 round(avg(sg.sg_total), 2) as round_sg_total,
                 max(r.round_date) as last_round
          from rounds r
          left join strokes_gained sg on sg.round_id = r.round_id
          group by r.player_id
        ),
        season_skill as (
          select player_id,
                 round(avg(sg_total), 2) as season_sg_total,
                 round(avg(sg_t2g), 2) as sg_t2g,
                 round(avg(sg_ott), 2) as sg_ott,
                 round(avg(sg_app), 2) as sg_app,
                 round(avg(sg_arg), 2) as sg_arg,
                 round(avg(sg_putt), 2) as sg_putt,
                 round(avg(driving_distance), 1) as driving_distance,
                 round(avg(accuracy), 4) as accuracy,
                 round(avg(gir), 4) as gir,
                 round(avg(scrambling), 4) as scrambling
          from strokes_gained
          where round_id is null
            and period like 'season-%'
          group by player_id
        ),
        difficulty_profile as (
          select r.player_id,
                 sum(case when cd.difficulty_bucket in ('brutal', 'tough') then 1 else 0 end) as tough_rounds,
                 round(avg(case when cd.difficulty_bucket in ('brutal', 'tough') then r.to_par end), 2) as tough_avg_to_par,
                 round(avg(case when cd.difficulty_bucket in ('brutal', 'tough') then sg.sg_total end), 2) as tough_avg_sg,
                 sum(case when cd.difficulty_bucket = 'gettable' then 1 else 0 end) as gettable_rounds,
                 round(avg(case when cd.difficulty_bucket = 'gettable' then r.to_par end), 2) as gettable_avg_to_par,
                 round(avg(case when cd.difficulty_bucket = 'gettable' then sg.sg_total end), 2) as gettable_avg_sg
          from rounds r
          left join course_difficulty cd on cd.course_id = r.course_id
          left join strokes_gained sg on sg.round_id = r.round_id
          where r.to_par is not null
          group by r.player_id
        ),
        major_profile as (
          select r.player_id,
                 count(r.round_id) as major_rounds,
                 count(distinct e.event_id) as major_events,
                 round(avg(r.to_par), 2) as major_avg_to_par,
                 round(avg(sg.sg_total), 2) as major_avg_sg
          from rounds r
          join events e on e.event_id = r.event_id
          left join strokes_gained sg on sg.round_id = r.round_id
          where r.to_par is not null
            and (
              lower(e.event_name) like '%u.s. open%'
              or lower(e.event_name) like '%us open%'
              or lower(e.event_name) like '%masters%'
              or lower(e.event_name) like '%pga championship%'
              or lower(e.event_name) like '%open championship%'
              or lower(e.event_name) = 'the open'
              or lower(e.event_name) like '%the open%'
            )
          group by r.player_id
        )
        select
          p.player_id,
          p.player_name,
          p.country,
          coalesce(cr.rounds, rf.rounds, 0) as rounds,
          coalesce(cr.scoring_rounds, 0) as scoring_rounds,
          cr.scoring_average,
          coalesce(cr.avg_to_par, rf.avg_to_par) as avg_to_par,
          coalesce(ss.season_sg_total, cr.round_sg_total, rf.avg_sg_total, ps.sg_total) as avg_sg_total,
          coalesce(ss.sg_t2g, ps.sg_t2g) as sg_t2g,
          coalesce(ss.sg_ott, ps.sg_ott) as sg_ott,
          coalesce(ss.sg_app, ps.sg_app) as sg_app,
          coalesce(ss.sg_arg, ps.sg_arg) as sg_arg,
          coalesce(ss.sg_putt, ps.sg_putt) as sg_putt,
          coalesce(ss.driving_distance, ps.driving_distance) as driving_distance,
          coalesce(ss.accuracy, ps.accuracy) as accuracy,
          coalesce(ss.gir, ps.gir) as gir,
          coalesce(ss.scrambling, ps.scrambling) as scrambling,
          coalesce(dp.tough_rounds, 0) as tough_rounds,
          dp.tough_avg_to_par,
          dp.tough_avg_sg,
          coalesce(dp.gettable_rounds, 0) as gettable_rounds,
          dp.gettable_avg_to_par,
          dp.gettable_avg_sg,
          coalesce(mj.major_rounds, 0) as major_rounds,
          coalesce(mj.major_events, 0) as major_events,
          mj.major_avg_to_par,
          mj.major_avg_sg,
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
        left join career_rounds cr on cr.player_id = p.player_id
        left join season_skill ss on ss.player_id = p.player_id
        left join difficulty_profile dp on dp.player_id = p.player_id
        left join major_profile mj on mj.player_id = p.player_id
        left join player_skill_snapshots ps on ps.player_id = p.player_id
        left join latest_model mp
          on mp.player_id = p.player_id
         and mp.market = 'winner'
         and (? = '' or mp.event_id = ?)
        where coalesce(cr.rounds, rf.rounds, 0) > 0
           or ps.player_id is not null
           or ss.player_id is not null
           or mp.player_id is not null
        order by
          case when mp.rank is null then 1 else 0 end,
          coalesce(mp.rank, 9999),
          coalesce(cr.last_round, rf.last_round, '') desc,
          coalesce(ss.season_sg_total, cr.round_sg_total, rf.avg_sg_total, -999) desc,
          p.player_name
        limit ?
        """,
        (modeled_event_id, modeled_event_id, limit),
    ).fetchall()
    return {"event": event, "rows": _enrich_reasoning(rows_to_dicts(rows))}


def player_filter_profiles(conn: sqlite3.Connection) -> dict[str, Any]:
    seasons = rows_to_dicts(
        conn.execute(
            """
            with round_counts as (
              select e.season,
                     count(r.round_id) as rounds
              from rounds r
              join events e on e.event_id = r.event_id
              where e.season is not null
              group by e.season
            ),
            season_players as (
              select e.season,
                     r.player_id
              from rounds r
              join events e on e.event_id = r.event_id
              where e.season is not null
              union
              select cast(substr(period, 8) as integer) as season,
                     player_id
              from strokes_gained
              where round_id is null
                and period like 'season-%'
            )
            select sp.season,
                   coalesce(rc.rounds, 0) as rounds,
                   count(distinct sp.player_id) as players
            from season_players sp
            left join round_counts rc on rc.season = sp.season
            where sp.season is not null
            group by sp.season, rc.rounds
            order by sp.season desc
            """
        ).fetchall()
    )
    rows = rows_to_dicts(
        conn.execute(
            """
            with round_profile as (
              select r.player_id,
                     e.season,
                     count(r.round_id) as rounds,
                     sum(case when r.score between 55 and 95 then 1 else 0 end) as scoring_rounds,
                     round(avg(case when r.score between 55 and 95 then r.score end), 2) as scoring_average,
                     round(avg(r.to_par), 2) as avg_to_par,
                     round(avg(sg.sg_total), 2) as avg_sg_total,
                     round(avg(sg.sg_t2g), 2) as sg_t2g,
                     round(avg(sg.sg_ott), 2) as sg_ott,
                     round(avg(sg.sg_app), 2) as sg_app,
                     round(avg(sg.sg_arg), 2) as sg_arg,
                     round(avg(sg.sg_putt), 2) as sg_putt,
                     max(r.round_date) as last_round
              from rounds r
              join events e on e.event_id = r.event_id
              left join strokes_gained sg on sg.round_id = r.round_id
              where e.season is not null
              group by r.player_id, e.season
            ),
            season_skill as (
              select player_id,
                     cast(substr(period, 8) as integer) as season,
                     sg_total as season_sg_total,
                     sg_t2g as season_sg_t2g,
                     sg_ott as season_sg_ott,
                     sg_app as season_sg_app,
                     sg_arg as season_sg_arg,
                     sg_putt as season_sg_putt,
                     driving_distance,
                     accuracy,
                     gir,
                     scrambling
              from strokes_gained
              where round_id is null
                and period like 'season-%'
            ),
            difficulty_profile as (
              select r.player_id,
                     e.season,
                     sum(case when cd.difficulty_bucket in ('brutal', 'tough') then 1 else 0 end) as tough_rounds,
                     round(avg(case when cd.difficulty_bucket in ('brutal', 'tough') then r.to_par end), 2) as tough_avg_to_par,
                     round(avg(case when cd.difficulty_bucket in ('brutal', 'tough') then sg.sg_total end), 2) as tough_avg_sg,
                     sum(case when cd.difficulty_bucket = 'gettable' then 1 else 0 end) as gettable_rounds,
                     round(avg(case when cd.difficulty_bucket = 'gettable' then r.to_par end), 2) as gettable_avg_to_par,
                     round(avg(case when cd.difficulty_bucket = 'gettable' then sg.sg_total end), 2) as gettable_avg_sg
              from rounds r
              join events e on e.event_id = r.event_id
              left join course_difficulty cd on cd.course_id = r.course_id
              left join strokes_gained sg on sg.round_id = r.round_id
              where e.season is not null
                and r.to_par is not null
              group by r.player_id, e.season
            ),
            major_profile as (
              select r.player_id,
                     e.season,
                     count(r.round_id) as major_rounds,
                     count(distinct e.event_id) as major_events,
                     round(avg(r.to_par), 2) as major_avg_to_par,
                     round(avg(sg.sg_total), 2) as major_avg_sg
              from rounds r
              join events e on e.event_id = r.event_id
              left join strokes_gained sg on sg.round_id = r.round_id
              where e.season is not null
                and r.to_par is not null
                and (
                  lower(e.event_name) like '%u.s. open%'
                  or lower(e.event_name) like '%us open%'
                  or lower(e.event_name) like '%masters%'
                  or lower(e.event_name) like '%pga championship%'
                  or lower(e.event_name) like '%open championship%'
                  or lower(e.event_name) = 'the open'
                  or lower(e.event_name) like '%the open%'
                )
              group by r.player_id, e.season
            ),
            player_seasons as (
              select player_id, season from round_profile
              union
              select player_id, season from season_skill
            )
            select p.player_id,
                   p.player_name,
                   ps.season,
                   coalesce(rp.rounds, 0) as rounds,
                   coalesce(rp.scoring_rounds, 0) as scoring_rounds,
                   rp.scoring_average,
                   rp.avg_to_par,
                   coalesce(ss.season_sg_total, rp.avg_sg_total) as avg_sg_total,
                   coalesce(ss.season_sg_t2g, rp.sg_t2g) as sg_t2g,
                   coalesce(ss.season_sg_ott, rp.sg_ott) as sg_ott,
                   coalesce(ss.season_sg_app, rp.sg_app) as sg_app,
                   coalesce(ss.season_sg_arg, rp.sg_arg) as sg_arg,
                   coalesce(ss.season_sg_putt, rp.sg_putt) as sg_putt,
                   ss.driving_distance,
                   ss.accuracy,
                   ss.gir,
                   ss.scrambling,
                   coalesce(dp.tough_rounds, 0) as tough_rounds,
                   dp.tough_avg_to_par,
                   dp.tough_avg_sg,
                   coalesce(dp.gettable_rounds, 0) as gettable_rounds,
                   dp.gettable_avg_to_par,
                   dp.gettable_avg_sg,
                   coalesce(mp.major_rounds, 0) as major_rounds,
                   coalesce(mp.major_events, 0) as major_events,
                   mp.major_avg_to_par,
                   mp.major_avg_sg,
                   rp.last_round
            from player_seasons ps
            join players p on p.player_id = ps.player_id
            left join round_profile rp
              on rp.player_id = ps.player_id
             and rp.season = ps.season
            left join season_skill ss
              on ss.player_id = ps.player_id
             and ss.season = ps.season
            left join difficulty_profile dp
              on dp.player_id = ps.player_id
             and dp.season = ps.season
            left join major_profile mp
              on mp.player_id = ps.player_id
             and mp.season = ps.season
            order by ps.season desc,
                     coalesce(ss.season_sg_total, rp.avg_sg_total, -999) desc,
                     p.player_name
            """
        ).fetchall()
    )
    return {"seasons": seasons, "rows": rows}


def player_card(conn: sqlite3.Connection, player_id: str, event_id: str | None = None) -> dict[str, Any]:
    player = one(
        conn,
        """
        with recent as (
          select r.player_id,
                 count(r.round_id) as rounds,
                 sum(case when r.score between 55 and 95 then 1 else 0 end) as scoring_rounds,
                 round(avg(case when r.score between 55 and 95 then r.score end), 2) as scoring_average,
                 round(avg(r.to_par), 2) as avg_to_par,
                 round(avg(sg.sg_total), 2) as avg_sg_total,
                 min(r.round_date) as first_round,
                 max(r.round_date) as last_round
          from rounds r
          left join strokes_gained sg on sg.round_id = r.round_id
          where r.player_id = ?
          group by r.player_id
        )
        select p.*, rf.rounds, rf.scoring_rounds, rf.scoring_average,
               rf.avg_to_par, rf.avg_sg_total, rf.first_round, rf.last_round,
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
    worst_courses = table_payload(
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
        order by avg_to_par desc, avg_sg asc
        limit 6
        """,
        (player_id,),
    )
    seasons = table_payload(
        conn,
        """
        with round_profile as (
          select e.season,
                 count(r.round_id) as rounds,
                 sum(case when r.score between 55 and 95 then 1 else 0 end) as scoring_rounds,
                 round(avg(case when r.score between 55 and 95 then r.score end), 2) as scoring_average,
                 round(avg(r.to_par), 2) as avg_to_par,
                 round(avg(sg.sg_total), 2) as avg_sg_total,
                 round(avg(sg.sg_t2g), 2) as sg_t2g,
                 round(avg(sg.sg_ott), 2) as sg_ott,
                 round(avg(sg.sg_app), 2) as sg_app,
                 round(avg(sg.sg_arg), 2) as sg_arg,
                 round(avg(sg.sg_putt), 2) as sg_putt,
                 max(r.round_date) as last_round
          from rounds r
          join events e on e.event_id = r.event_id
          left join strokes_gained sg on sg.round_id = r.round_id
          where r.player_id = ?
            and e.season is not null
          group by e.season
        ),
        season_skill as (
          select cast(substr(period, 8) as integer) as season,
                 sg_total as season_sg_total,
                 sg_t2g as season_sg_t2g,
                 sg_ott as season_sg_ott,
                 sg_app as season_sg_app,
                 sg_arg as season_sg_arg,
                 sg_putt as season_sg_putt,
                 driving_distance,
                 accuracy,
                 gir,
                 scrambling
          from strokes_gained
          where player_id = ?
            and round_id is null
            and period like 'season-%'
        ),
        player_seasons as (
          select season from round_profile
          union
          select season from season_skill
        )
        select ps.season,
               coalesce(rp.rounds, 0) as rounds,
               coalesce(rp.scoring_rounds, 0) as scoring_rounds,
               rp.scoring_average,
               rp.avg_to_par,
               coalesce(ss.season_sg_total, rp.avg_sg_total) as avg_sg_total,
               coalesce(ss.season_sg_t2g, rp.sg_t2g) as sg_t2g,
               coalesce(ss.season_sg_ott, rp.sg_ott) as sg_ott,
               coalesce(ss.season_sg_app, rp.sg_app) as sg_app,
               coalesce(ss.season_sg_arg, rp.sg_arg) as sg_arg,
               coalesce(ss.season_sg_putt, rp.sg_putt) as sg_putt,
               ss.driving_distance,
               ss.accuracy,
               ss.gir,
               ss.scrambling,
               rp.last_round
        from player_seasons ps
        left join round_profile rp on rp.season = ps.season
        left join season_skill ss on ss.season = ps.season
        order by ps.season desc
        limit 12
        """,
        (player_id, player_id),
    )
    difficulty_splits = _player_difficulty_splits(conn, player_id)
    recent_vs_baseline = one(
        conn,
        """
        with player_rounds as (
          select r.round_id, r.round_date, r.round_number, r.to_par, sg.sg_total,
                 row_number() over (
                   order by r.round_date desc, coalesce(r.round_number, 0) desc, r.round_id desc
                 ) as row_number
          from rounds r
          left join strokes_gained sg on sg.round_id = r.round_id
          where r.player_id = ?
            and r.to_par is not null
        )
        select count(*) as baseline_rounds,
               round(avg(to_par), 2) as baseline_to_par,
               round(avg(sg_total), 2) as baseline_sg,
               sum(case when row_number <= 20 then 1 else 0 end) as recent_rounds,
               round(avg(case when row_number <= 20 then to_par end), 2) as recent_to_par,
               round(avg(case when row_number <= 20 then sg_total end), 2) as recent_sg
        from player_rounds
        """,
        (player_id,),
    )
    if recent_vs_baseline:
        sg_delta = None
        to_par_delta = None
        if _num(recent_vs_baseline.get("recent_sg")) is not None and _num(recent_vs_baseline.get("baseline_sg")) is not None:
            sg_delta = round(_num(recent_vs_baseline["recent_sg"]) - _num(recent_vs_baseline["baseline_sg"]), 2)
        if _num(recent_vs_baseline.get("recent_to_par")) is not None and _num(recent_vs_baseline.get("baseline_to_par")) is not None:
            to_par_delta = round(_num(recent_vs_baseline["recent_to_par"]) - _num(recent_vs_baseline["baseline_to_par"]), 2)
        recent_vs_baseline["sg_delta"] = sg_delta
        recent_vs_baseline["to_par_delta"] = to_par_delta
        if sg_delta is not None:
            if sg_delta >= 0.25:
                recent_vs_baseline["trend_label"] = "Form is better than baseline"
            elif sg_delta <= -0.25:
                recent_vs_baseline["trend_label"] = "Recent form trails baseline"
            else:
                recent_vs_baseline["trend_label"] = "Recent form is near baseline"
        elif to_par_delta is not None:
            if to_par_delta <= -0.35:
                recent_vs_baseline["trend_label"] = "Scoring trend is improving"
            elif to_par_delta >= 0.35:
                recent_vs_baseline["trend_label"] = "Scoring trend is cooling"
            else:
                recent_vs_baseline["trend_label"] = "Scoring trend is stable"
        else:
            recent_vs_baseline["trend_label"] = "Trend pending more scorecards"
    major_profile = table_payload(
        conn,
        """
        with major_rounds as (
          select case
                   when lower(e.event_name) like '%u.s. open%' or lower(e.event_name) like '%us open%' then 'U.S. Open'
                   when lower(e.event_name) like '%masters%' then 'Masters'
                   when lower(e.event_name) like '%pga championship%' then 'PGA Championship'
                   when lower(e.event_name) like '%open championship%' or lower(e.event_name) = 'the open' or lower(e.event_name) like '%the open%' then 'The Open'
                 end as major,
                 e.event_id,
                 e.season,
                 r.to_par,
                 sg.sg_total
          from rounds r
          join events e on e.event_id = r.event_id
          left join strokes_gained sg on sg.round_id = r.round_id
          where r.player_id = ?
            and r.to_par is not null
        )
        select major,
               count(distinct event_id) as events,
               count(*) as rounds,
               max(season) as latest_season,
               round(avg(to_par), 2) as avg_to_par,
               round(avg(sg_total), 2) as avg_sg,
               round(min(to_par), 2) as best_to_par,
               round(max(to_par), 2) as worst_to_par
        from major_rounds
        where major is not null
        group by major
        order by case major
          when 'Masters' then 1
          when 'PGA Championship' then 2
          when 'U.S. Open' then 3
          when 'The Open' then 4
          else 5 end
        """,
        (player_id,),
    )
    major_profile["summary"] = _major_summary(major_profile)
    course_dna = _course_dna(difficulty_splits, recent_vs_baseline)
    grade_explanations = _grade_explanations(player, seasons, recent_vs_baseline, difficulty_splits)
    model = one(
        conn,
        f"""
        {LATEST_MODEL_CTE}
        select event_id, market, rank, probability, fair_odds_american,
               edge_probability, projected_to_par, confidence, plain_english, risk_flags
        from latest_model
        where player_id = ?
          and market = 'winner'
          and (? = '' or event_id = ?)
        order by created_at desc, coalesce(rank, 9999)
        limit 1
        """,
        (player_id, event_id or "", event_id or ""),
    )
    if model:
        model["avg_sg_total"] = player.get("avg_sg_total")
        model["avg_to_par"] = player.get("avg_to_par")
        model["plain_english"] = _generated_reason(model)
    coverage = {
        "hasRoundScorecards": bool(player.get("rounds")),
        "hasStrokesGained": player.get("avg_sg_total") is not None,
        "hasDrivingDistance": player.get("driving_distance") is not None,
        "hasAccuracy": player.get("accuracy") is not None,
        "hasGir": player.get("gir") is not None,
        "hasScrambling": player.get("scrambling") is not None,
        "hasTrustedScoring": (player.get("scoring_rounds") or 0) >= 20 and player.get("scoring_average") is not None,
        "seasonProfiles": len(seasons["rows"]),
    }
    return {
        "player": player,
        "rounds": rounds,
        "bestCourses": best_courses,
        "worstCourses": worst_courses,
        "seasons": seasons,
        "difficultySplits": difficulty_splits,
        "courseDna": course_dna,
        "majorProfile": major_profile,
        "recentVsBaseline": recent_vs_baseline,
        "gradeExplanations": grade_explanations,
        "model": model,
        "coverage": coverage,
    }


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


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _course_archetype(conn: sqlite3.Connection, course_id: str) -> dict[str, Any]:
    rows = rows_to_dicts(
        conn.execute(
            """
            with course_player as (
              select r.player_id,
                     count(r.round_id) as rounds,
                     avg(r.to_par) as avg_to_par
              from rounds r
              where r.course_id = ?
                and r.to_par is not null
              group by r.player_id
              having count(r.round_id) >= 2
            ),
            skill as (
              select player_id,
                     avg(sg_t2g) as sg_t2g,
                     avg(sg_ott) as sg_ott,
                     avg(sg_app) as sg_app,
                     avg(sg_arg) as sg_arg,
                     avg(sg_putt) as sg_putt,
                     avg(driving_distance) as driving_distance,
                     avg(accuracy) as accuracy,
                     avg(gir) as gir,
                     avg(scrambling) as scrambling
              from strokes_gained
              where round_id is null
                and period like 'season-%'
              group by player_id
            )
            select cp.player_id,
                   p.player_name,
                   cp.rounds,
                   cp.avg_to_par,
                   skill.sg_t2g,
                   skill.sg_ott,
                   skill.sg_app,
                   skill.sg_arg,
                   skill.sg_putt,
                   skill.driving_distance,
                   skill.accuracy,
                   skill.gir,
                   skill.scrambling
            from course_player cp
            join players p on p.player_id = cp.player_id
            left join skill on skill.player_id = cp.player_id
            """,
            (course_id,),
        ).fetchall()
    )
    metric_defs = [
        ("driving_distance", "Distance", "big hitters", "yards"),
        ("accuracy", "Accuracy", "accurate drivers", "fairways"),
        ("gir", "GIR", "green finders", "GIR"),
        ("sg_app", "Approach", "iron players", "approach"),
        ("sg_arg", "Around green", "short-game players", "around the green"),
        ("sg_putt", "Putting", "putters", "putting"),
        ("scrambling", "Scrambling", "scramblers", "scrambling"),
    ]
    traits: list[dict[str, Any]] = []
    for key, label, player_label, lane in metric_defs:
        metric_rows = [row for row in rows if _num(row.get(key)) is not None and _num(row.get("avg_to_par")) is not None]
        if len(metric_rows) < 24:
            continue
        metric_rows.sort(key=lambda row: _num(row.get(key)) or 0)
        size = max(6, len(metric_rows) // 4)
        bottom = metric_rows[:size]
        top = metric_rows[-size:]
        top_avg = _avg([_num(row.get("avg_to_par")) for row in top if _num(row.get("avg_to_par")) is not None])
        bottom_avg = _avg([_num(row.get("avg_to_par")) for row in bottom if _num(row.get("avg_to_par")) is not None])
        if top_avg is None or bottom_avg is None:
            continue
        edge = bottom_avg - top_avg
        top_names = sorted(top, key=lambda row: _num(row.get("avg_to_par")) if _num(row.get("avg_to_par")) is not None else 99)[:3]
        traits.append({
            "key": key,
            "label": label,
            "lane": lane,
            "player_label": player_label,
            "sample_players": len(metric_rows),
            "top_quartile_avg_to_par": round(top_avg, 2),
            "bottom_quartile_avg_to_par": round(bottom_avg, 2),
            "edge_to_par": round(edge, 2),
            "edge_label": "rewards" if edge >= 0.15 else ("resists" if edge <= -0.15 else "neutral"),
            "top_names": [row["player_name"] for row in top_names],
        })
    traits.sort(key=lambda row: (row["edge_to_par"], abs(row["edge_to_par"])), reverse=True)
    positive = [row for row in traits if row["edge_to_par"] >= 0.15]
    if positive:
        primary = positive[0]
        summary = (
            f"This course has most rewarded {primary['player_label']}: top-quartile {primary['lane']} profiles "
            f"beat the bottom quartile by {primary['edge_to_par']:.2f} strokes per round in the loaded sample."
        )
    elif traits:
        primary = traits[0]
        summary = (
            f"No single stat lane dominates yet. The strongest loaded split is {primary['label']} at "
            f"{primary['edge_to_par']:+.2f} strokes per round."
        )
    else:
        summary = "Course archetype needs more repeat-player rounds joined to public skill profiles."
    return {
        "sample_players": len(rows),
        "summary": summary,
        "primary_traits": positive[:3],
        "traits": traits,
    }


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
    return {"course": course, "fits": fits, "setups": setups, "archetype": _course_archetype(conn, course_id)}


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
    quality = one(
        conn,
        """
        with rich_players as (
          select player_id,
                 count(distinct cast(substr(period, 8) as integer)) as seasons
          from strokes_gained
          where round_id is null
            and period like 'season-%'
          group by player_id
        ),
        major_rounds as (
          select count(r.round_id) as rounds,
                 count(distinct r.player_id) as players
          from rounds r
          join events e on e.event_id = r.event_id
          where lower(e.event_name) like '%u.s. open%'
             or lower(e.event_name) like '%us open%'
             or lower(e.event_name) like '%masters%'
             or lower(e.event_name) like '%pga championship%'
             or lower(e.event_name) like '%open championship%'
             or lower(e.event_name) = 'the open'
             or lower(e.event_name) like '%the open%'
        ),
        tough_rounds as (
          select count(r.round_id) as rounds,
                 count(distinct r.player_id) as players
          from rounds r
          join course_difficulty cd on cd.course_id = r.course_id
          where cd.difficulty_bucket in ('brutal', 'tough')
        )
        select
          (select count(distinct player_id) from rounds) as players_with_scorecards,
          (select count(*) from rich_players where seasons >= 3) as players_with_three_rich_seasons,
          (select players from major_rounds) as players_with_major_rounds,
          (select rounds from major_rounds) as major_rounds,
          (select players from tough_rounds) as players_with_tough_rounds,
          (select rounds from tough_rounds) as tough_rounds,
          (select count(*) from course_difficulty where rounds >= 12) as courses_with_samples,
          (select count(distinct player_id) from model_predictions) as players_with_model_predictions,
          (select count(*) from source_fetches where status = 'ok') as ok_source_fetches
        """
    ) or {}
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
    coverage = [
        {
            "label": "Player scorecards",
            "value": quality.get("players_with_scorecards"),
            "status": "good" if (quality.get("players_with_scorecards") or 0) >= 250 else "watch",
            "note": f"{counts['rounds']:,} imported rounds",
        },
        {
            "label": "3-year rich profiles",
            "value": quality.get("players_with_three_rich_seasons"),
            "status": "good" if (quality.get("players_with_three_rich_seasons") or 0) >= 100 else "watch",
            "note": "SG, distance, accuracy, GIR, scrambling",
        },
        {
            "label": "Tough-course DNA",
            "value": quality.get("players_with_tough_rounds"),
            "status": "good" if (quality.get("tough_rounds") or 0) >= 1000 else "watch",
            "note": f"{(quality.get('tough_rounds') or 0):,} brutal/tough rounds",
        },
        {
            "label": "Major profile",
            "value": quality.get("players_with_major_rounds"),
            "status": "good" if (quality.get("major_rounds") or 0) >= 1000 else "watch",
            "note": f"{(quality.get('major_rounds') or 0):,} major rounds",
        },
        {
            "label": "Course samples",
            "value": quality.get("courses_with_samples"),
            "status": "good" if (quality.get("courses_with_samples") or 0) >= 50 else "watch",
            "note": "courses with 12+ rounds",
        },
        {
            "label": "Model predictions",
            "value": quality.get("players_with_model_predictions"),
            "status": "good" if counts["model_predictions"] else "watch",
            "note": f"{counts['model_predictions']:,} saved predictions",
        },
    ]
    automation = [
        {
            "label": "Static Pages export",
            "status": "ready",
            "note": "python export_static.py writes the phone-ready docs snapshot",
        },
        {
            "label": "Public stat refresh",
            "status": "ready",
            "note": "python pga_tour_stats_backfill.py --years 2023-2026 updates rich profiles",
        },
        {
            "label": "Warehouse refresh",
            "status": "local source required",
            "note": "scorecards rebuild from the local PGA public-history warehouse",
        },
    ]
    return {
        "summary": summary,
        "sources": source_rows,
        "blockers": blockers,
        "quality": quality,
        "coverage": coverage,
        "statQuality": _stat_quality_checks(conn),
        "automation": automation,
        "grade": "premium-ready" if not blockers else ("analysis-ready" if counts["rounds"] else "setup"),
    }
