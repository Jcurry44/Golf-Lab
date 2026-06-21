# Golf Lab

Blue Line-style professional golf intelligence: a local SQLite warehouse, API
server, and premium dashboard for player cards, course difficulty, model
projections, odds context, and prediction review.

This is intentionally separate from Fairway Ledger. Fairway Ledger stays the
phone-first personal scorecard app. Golf Lab owns the pro database and model
work, using the same architecture pattern that makes Blue Line reliable:

- source refresh/import scripts write to SQLite
- analytics are precomputed or queried server-side
- the UI fetches small JSON payloads from `/api/...`
- no manual browser imports
- no giant database bundle shipped to the phone

## Quick Start

From `Golf Lab`:

```powershell
python golf_lab_import.py --seed-starter
python app.py --port 8787
```

Open:

```text
http://127.0.0.1:8787/
```

To build from the local public PGA warehouse created during research:

```powershell
python golf_lab_import.py --from-warehouse "..\Golf Stats Tracker\data\golf-lab\pga-public-history-2002-2026" --rounds-per-player 40
python pga_tour_stats_backfill.py --years 2023-2026
python app.py --port 8787
```

By default the warehouse import loads the full available player universe. Use
`--event-field-only` only for a tiny development build.

`pga_tour_stats_backfill.py` enriches recent seasons from public PGA TOUR stat
tables: SG Total, tee-to-green, off-the-tee, approach, around-the-green,
putting, driving distance, driving accuracy, GIR, and scrambling. The pages are
cached under `data/raw/pgatour/`, and the player-season rows flow into the same
player filters and scorecard cards as the rest of the app.

The generated SQLite database lives at `data/golf_lab.db` and is ignored by git.

## Mobile / GitHub Pages

Golf Lab can publish a static snapshot to GitHub Pages. The local Python API is
still the source of truth; the static build exports the same dashboard payloads
into `docs/api/*.json` so the phone URL works without a running server.

```powershell
python export_static.py
```

Commit and push the repo to GitHub. The included Pages workflow deploys the
`docs` folder from `main`.

## Current Shape

Implemented first:

- SQLite schema for players, events, courses, scorecards, strokes gained,
  weather, odds, model predictions, prediction ledger, and source proofs.
- Warehouse importer that can build a focused app database from the existing
  source-backed PGA CSV warehouse.
- API endpoints for summary, event board, player cards, course cards, model
  board, warehouse health, and individual player/course drilldowns.
- Premium dashboard shell with low-scroll cards and plain-English model
  reasoning.

Next build lane:

- Scheduled refresh orchestration inside this project.
- More complete player DNA: equipment, accomplishments, career results.
- Prediction result summaries after tournaments settle.
- Blue Line-style data quality and source lineage boards.
