# Architecture

Golf Lab follows the Sports Research Database / Blue Line shape, not the
Fairway Ledger static-PWA shape.

## Principle

The browser should never carry the warehouse. It should render polished,
small API payloads prepared by a local server.

```
source adapters / saved public CSVs
          |
          v
   data/golf_lab.db  (SQLite, ignored)
          |
          v
    app.py JSON API  (/api/summary, /api/player-card, ...)
          |
          v
       web app       (premium dashboard)
```

## Data Layers

- `players`, `player_skill_snapshots`
- `events`, `fields`
- `courses`, `course_setups`
- `rounds`, `strokes_gained`
- `weather_snapshots`
- `odds_snapshots`
- `model_predictions`, `prediction_ledger`
- `source_fetches`

## Module Boundaries

- `schema.sql`: complete warehouse schema and app views.
- `golf_lab_import.py`: writes to the database from starter data or local CSV
  warehouse files.
- `golf_lab_analytics.py`: read-only analytics payload builders.
- `app_common.py`: shared DB and JSON helpers.
- `app.py`: HTTP server and route wiring.
- `web/`: static dashboard.

## Extension Rule

New data source? Add the table/view to `schema.sql`, write an importer that
records `source_fetches`, and expose a compact API shape. The UI should not
parse raw source files or bulk warehouse records.

