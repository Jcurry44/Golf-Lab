from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DEFAULT_DB = ROOT / "data" / "golf_lab.db"
SCHEMA = ROOT / "schema.sql"


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def connect(db_path: Path = DEFAULT_DB, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, factory=ClosingConnection)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in row.keys()} for row in rows]


def table_payload(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict[str, Any]:
    rows = conn.execute(sql, params).fetchall()
    return {
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows_to_dicts(rows),
    }


def one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return {key: row[key] for key in row.keys()} if row else None


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


def int_param(values: dict[str, list[str]], key: str, default: int, low: int, high: int) -> int:
    try:
        value = int((values.get(key) or [default])[0])
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def str_param(values: dict[str, list[str]], key: str, default: str = "") -> str:
    return str((values.get(key) or [default])[0] or "").strip()
