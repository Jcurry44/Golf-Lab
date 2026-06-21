from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app_common import DEFAULT_DB, ROOT, connect, init_db
from golf_lab_import import import_player_skill_snapshots, upsert


PGA_TOUR_STAT_URL = "https://www.pgatour.com/stats/detail/{stat_id}?year={year}"
PGA_TOUR_GRAPHQL_URL = "https://orchestrator.pgatour.com/graphql"
PGA_TOUR_PUBLIC_API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"
NEXT_DATA_PATTERN = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)
STAT_DETAILS_QUERY = """
query StatDetails($tourCode: TourCode!, $statId: String!, $year: Int, $eventQuery: StatDetailEventQuery) {
  statDetails(tourCode: $tourCode, statId: $statId, year: $year, eventQuery: $eventQuery) {
    __typename
    tourCode
    year
    displaySeason
    statId
    statType
    statTitle
    statDescription
    tourAvg
    lastProcessed
    rows {
      ... on StatDetailsPlayer {
        __typename
        playerId
        playerName
        country
        countryFlag
        rank
        rankDiff
        rankChangeTendency
        stats {
          statName
          statValue
          color
          supportingData
          supportingDataColor
        }
      }
      ... on StatDetailTourAvg {
        __typename
        displayName
        value
      }
    }
  }
}
"""


@dataclass(frozen=True)
class StatSpec:
    stat_id: str
    column: str
    percent: bool = False


STAT_SPECS = [
    StatSpec("02675", "sg_total"),
    StatSpec("02674", "sg_t2g"),
    StatSpec("02567", "sg_ott"),
    StatSpec("02568", "sg_app"),
    StatSpec("02569", "sg_arg"),
    StatSpec("02564", "sg_putt"),
    StatSpec("101", "driving_distance"),
    StatSpec("102", "accuracy", percent=True),
    StatSpec("103", "gir", percent=True),
    StatSpec("130", "scrambling", percent=True),
]


STAT_COLUMNS = [spec.column for spec in STAT_SPECS]


def normalize_player_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def numeric_stat_value(value: Any, *, percent: bool = False) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    has_percent = text.endswith("%")
    text = text.replace("%", "")
    try:
        number = float(text)
    except ValueError:
        return None
    return round(number / 100.0, 4) if percent or has_percent else number


def extract_next_data(html: str) -> dict[str, Any]:
    match = NEXT_DATA_PATTERN.search(html)
    if not match:
        raise ValueError("PGA TOUR page did not contain __NEXT_DATA__.")
    return json.loads(match.group(1))


def extract_stat_details(html: str, stat_id: str, year: int) -> dict[str, Any]:
    next_data = extract_next_data(html)
    queries = (
        next_data.get("props", {})
        .get("pageProps", {})
        .get("dehydratedState", {})
        .get("queries", [])
    )
    candidates: list[tuple[int, dict[str, Any]]] = []
    for query in queries:
        query_key = query.get("queryKey") or []
        query_params = query_key[1] if len(query_key) > 1 and isinstance(query_key[1], dict) else {}
        data = query.get("state", {}).get("data")
        if not isinstance(data, dict) or data.get("__typename") != "StatDetails":
            continue
        if str(data.get("statId") or query_params.get("statId") or "") != str(stat_id):
            continue
        if query_params.get("eventQuery") is not None:
            continue
        score = 0
        if query_params.get("year") == year:
            score += 10
        if data.get("year") == year:
            score += 5
        score += min(len(data.get("rows") or []), 500)
        candidates.append((score, data))
    if not candidates:
        raise ValueError(f"No season stat detail found for stat {stat_id} in {year}.")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def primary_row_value(row: dict[str, Any], spec: StatSpec) -> float | None:
    stats = row.get("stats") or []
    if not stats:
        return None
    preferred_names = {"Avg", "%"}
    for stat in stats:
        if stat.get("statName") in preferred_names:
            return numeric_stat_value(stat.get("statValue"), percent=spec.percent)
    return numeric_stat_value(stats[0].get("statValue"), percent=spec.percent)


def player_name_index(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        """
        select p.player_id,
               p.player_name,
               count(r.round_id) as rounds,
               max(e.season) as latest_season
        from players p
        left join rounds r on r.player_id = p.player_id
        left join events e on e.event_id = r.event_id
        group by p.player_id, p.player_name
        """
    ).fetchall()
    candidates: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        key = normalize_player_name(row["player_name"])
        if key:
            candidates.setdefault(key, []).append(row)

    index: dict[str, str] = {}
    for key, matches in candidates.items():
        best = sorted(
            matches,
            key=lambda row: (
                row["latest_season"] or 0,
                row["rounds"] or 0,
                row["player_id"],
            ),
            reverse=True,
        )[0]
        index[key] = best["player_id"]
    return index


def fetch_stat_page(stat_id: str, year: int, cache_dir: Path, refresh: bool = False) -> tuple[str, str, bool]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"stats-detail-{stat_id}-{year}.html"
    url = PGA_TOUR_STAT_URL.format(stat_id=stat_id, year=year)
    if cache_path.exists() and not refresh:
        return cache_path.read_text(encoding="utf-8", errors="ignore"), url, False

    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; GolfLab/1.0; +https://github.com/Jcurry44/Golf-Lab)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Failed to fetch {url}: {exc}") from exc
    cache_path.write_text(html, encoding="utf-8")
    return html, url, True


def fetch_stat_details_api(stat_id: str, year: int, cache_dir: Path, refresh: bool = False) -> tuple[dict[str, Any], str, bool]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"stat-details-{stat_id}-{year}.json"
    source_url = PGA_TOUR_STAT_URL.format(stat_id=stat_id, year=year)
    if cache_path.exists() and not refresh:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        details = payload["data"]["statDetails"]
        if details.get("year") != year:
            raise ValueError(f"Cached stat {stat_id} returned {details.get('year')} instead of {year}.")
        return details, source_url, False

    request_body = json.dumps(
        {
            "operationName": "StatDetails",
            "variables": {"tourCode": "R", "statId": stat_id, "year": year, "eventQuery": None},
            "query": STAT_DETAILS_QUERY,
        }
    ).encode("utf-8")
    request = Request(
        PGA_TOUR_GRAPHQL_URL,
        data=request_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": PGA_TOUR_PUBLIC_API_KEY,
            "User-Agent": "Mozilla/5.0 (compatible; GolfLab/1.0; +https://github.com/Jcurry44/Golf-Lab)",
            "Origin": "https://www.pgatour.com",
            "Referer": source_url,
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Failed to fetch PGA TOUR stat {stat_id} for {year}: {exc}") from exc
    if payload.get("errors"):
        raise RuntimeError(f"PGA TOUR GraphQL returned errors for stat {stat_id} {year}: {payload['errors']}")
    details = payload.get("data", {}).get("statDetails")
    if not isinstance(details, dict):
        raise RuntimeError(f"PGA TOUR GraphQL returned no statDetails for stat {stat_id} {year}.")
    if details.get("year") != year:
        raise ValueError(f"PGA TOUR stat {stat_id} returned {details.get('year')} instead of {year}.")
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return details, source_url, True


def collect_profiles(
    conn: sqlite3.Connection,
    years: list[int],
    cache_dir: Path,
    refresh: bool = False,
    pause_seconds: float = 0.25,
) -> tuple[dict[tuple[int, str], dict[str, Any]], list[dict[str, Any]]]:
    name_index = player_name_index(conn)
    profiles: dict[tuple[int, str], dict[str, Any]] = {}
    fetches: list[dict[str, Any]] = []

    for year in years:
        for spec in STAT_SPECS:
            details, source_url, downloaded = fetch_stat_details_api(spec.stat_id, year, cache_dir, refresh=refresh)
            rows = details.get("rows") or []
            matched = 0
            unmatched: list[str] = []
            for row in rows:
                player_name = str(row.get("playerName") or "").strip()
                player_id = name_index.get(normalize_player_name(player_name))
                if not player_id:
                    if len(unmatched) < 12:
                        unmatched.append(player_name)
                    continue
                value = primary_row_value(row, spec)
                if value is None:
                    continue
                key = (year, player_id)
                profile = profiles.setdefault(
                    key,
                    {
                        "season": year,
                        "player_id": player_id,
                        "player_name": player_name,
                        "source_url": f"https://www.pgatour.com/stats?year={year}",
                    },
                )
                profile[spec.column] = value
                matched += 1
            fetches.append(
                {
                    "stat_id": spec.stat_id,
                    "stat_title": details.get("statTitle"),
                    "year": year,
                    "rows": len(rows),
                    "matched": matched,
                    "downloaded": downloaded,
                    "source_url": source_url,
                    "unmatched": unmatched,
                }
            )
            if downloaded and pause_seconds > 0:
                time.sleep(pause_seconds)
    return profiles, fetches


def upsert_profiles(conn: sqlite3.Connection, profiles: dict[tuple[int, str], dict[str, Any]], fetched_at: str) -> int:
    count = 0
    for (_, player_id), profile in sorted(profiles.items()):
        season = profile["season"]
        values = {
            "sg_id": f"pgatour-stats-season-{season}-{player_id}",
            "round_id": None,
            "event_id": None,
            "player_id": player_id,
            "period": f"season-{season}",
            "source_provider": "PGA TOUR public stats",
            "source_url": profile["source_url"],
            "source_updated_at": fetched_at,
        }
        for column in STAT_COLUMNS:
            values[column] = profile.get(column)
        upsert(conn, "strokes_gained", values)
        count += 1
    return count


def record_fetches(conn: sqlite3.Connection, fetches: list[dict[str, Any]], fetched_at: str) -> None:
    for fetch in fetches:
        upsert(
            conn,
            "source_fetches",
            {
                "fetch_id": f"pgatour-stat-{fetch['stat_id']}-{fetch['year']}",
                "provider": "PGA TOUR public stats",
                "endpoint": f"/stats/detail/{fetch['stat_id']}?year={fetch['year']}",
                "event_id": None,
                "model_run_id": None,
                "fetched_at": fetched_at,
                "status": "ok" if fetch["matched"] else "no-player-matches",
                "row_count": fetch["matched"],
                "source_url": fetch["source_url"],
                "notes": json.dumps(
                    {
                        "statTitle": fetch["stat_title"],
                        "sourceRows": fetch["rows"],
                        "downloaded": fetch["downloaded"],
                        "unmatchedSample": fetch["unmatched"],
                    },
                    ensure_ascii=False,
                ),
            },
        )


def backfill_recent_pga_stats(
    db_path: Path = DEFAULT_DB,
    years: list[int] | None = None,
    cache_dir: Path = ROOT / "data" / "raw" / "pgatour",
    refresh: bool = False,
) -> dict[str, Any]:
    if years is None:
        years = [2023, 2024, 2025, 2026]
    years = sorted(set(years))
    fetched_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    with connect(db_path) as conn:
        init_db(conn)
        profiles, fetches = collect_profiles(conn, years, cache_dir, refresh=refresh)
        profile_count = upsert_profiles(conn, profiles, fetched_at)
        record_fetches(conn, fetches, fetched_at)
        import_player_skill_snapshots(conn)
        conn.commit()
    return {
        "years": years,
        "statPages": len(fetches),
        "playerSeasonProfiles": profile_count,
        "matchedStatCells": sum(fetch["matched"] for fetch in fetches),
        "sourceRows": sum(fetch["rows"] for fetch in fetches),
    }


def parse_years(values: list[str]) -> list[int]:
    years: set[int] = set()
    for value in values:
        if "-" in value:
            start, end = value.split("-", 1)
            years.update(range(int(start), int(end) + 1))
        else:
            years.add(int(value))
    return sorted(years)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill recent PGA TOUR public season stats into Golf Lab.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--years", nargs="+", default=["2023-2026"], help="Years or ranges, e.g. 2023 2024 or 2023-2026.")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "raw" / "pgatour")
    parser.add_argument("--refresh", action="store_true", help="Download pages even when cached HTML exists.")
    args = parser.parse_args()
    result = backfill_recent_pga_stats(args.db, parse_years(args.years), args.cache_dir, args.refresh)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
