"""SQLite storage driver for model-tracker.

UUIDs stored as canonical 36-char TEXT. Booleans as INTEGER 0/1.
Every connection sets PRAGMA foreign_keys=ON and PRAGMA journal_mode=WAL.
Each insert/update commits before returning (crash-safety under WAL).
Invalid FKs are rejected by SQLite when foreign_keys is ON.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from storage import StorageDriver, TABLES, register_driver  # noqa: E402


_DDL = {
    "system_info": """
        CREATE TABLE IF NOT EXISTS system_info (
            id TEXT PRIMARY KEY,
            os_make_version TEXT,
            agent_make_version TEXT,
            hardware_details TEXT,
            created_at INTEGER
        )
    """,
    "system_config": """
        CREATE TABLE IF NOT EXISTS system_config (
            id TEXT PRIMARY KEY,
            system_info_id TEXT,
            run_id TEXT,
            run_name TEXT,
            stats TEXT,
            nturns INTEGER,
            ctx_length INTEGER,
            num_compressed INTEGER,
            was_complete INTEGER,
            was_errors TEXT,
            created_at INTEGER,
            FOREIGN KEY (system_info_id) REFERENCES system_info(id)
        )
    """,
    "model_info": """
        CREATE TABLE IF NOT EXISTS model_info (
            id TEXT PRIMARY KEY,
            system_config_id TEXT,
            model_alias TEXT,
            model_name TEXT,
            model_source TEXT,
            model_context_size INTEGER,
            model_hosted INTEGER,
            model_hosted_location TEXT,
            model_free INTEGER,
            model_added INTEGER,
            model_removed INTEGER,
            model_last_use INTEGER,
            created_at INTEGER,
            FOREIGN KEY (system_config_id) REFERENCES system_config(id)
        )
    """,
    "user_notes": """
        CREATE TABLE IF NOT EXISTS user_notes (
            id TEXT PRIMARY KEY,
            model_info_id TEXT,
            user_notes TEXT,
            user_rating INTEGER,
            agent_rating REAL,
            created_at INTEGER,
            FOREIGN KEY (model_info_id) REFERENCES model_info(id)
        )
    """,
}


def _to_db(value):
    """Serialize a logical value for SQLite storage."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _from_db(row: dict) -> dict:
    """Coerce SQLite values back to logical types (booleans)."""
    out = dict(row)
    for col in ("was_complete", "model_hosted", "model_free"):
        if col in out and out[col] is not None:
            out[col] = bool(out[col])
    return out


class SqliteDriver(StorageDriver):
    def __init__(self) -> None:
        self._db_path = ""
        self._conn: sqlite3.Connection | None = None

    def init(self, config: dict) -> None:
        db_path = config.get("storage", {}).get("sqlite", {}).get(
            "db_path", "~/.model-tracker/data/model-tracker.sqlite"
        )
        self._db_path = os.path.expanduser(db_path)
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        # Crash-safety + FK enforcement.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.commit()
        for table, ddl in _DDL.items():
            self._conn.execute(ddl)
        self._conn.commit()

    def _conn_or_raise(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Driver not initialized (call init() first).")
        return self._conn

    def insert(self, table: str, row: dict) -> str:
        cols = TABLES[table]
        if "id" not in row or row["id"] is None:
            row["id"] = uuid.uuid7()
        rid = str(row["id"])
        values = [_to_db(row.get(c)) for c in cols]
        conn = self._conn_or_raise()
        placeholders = ", ".join(["?"] * len(cols))
        colnames = ", ".join(cols)
        # FK enforcement is active via PRAGMA; bad FK raises IntegrityError.
        conn.execute(
            f"INSERT INTO {table} ({colnames}) VALUES ({placeholders})", values
        )
        conn.commit()
        return rid

    def update(self, table: str, id: str, changes: dict) -> None:
        cols = TABLES[table]
        conn = self._conn_or_raise()
        sets = []
        vals = []
        for k, v in changes.items():
            if k not in cols:
                raise KeyError(f"Unknown column '{k}' for table '{table}'.")
            sets.append(f"{k} = ?")
            vals.append(_to_db(v))
        if not sets:
            return
        vals.append(str(id))
        cur = conn.execute(
            f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", vals
        )
        if cur.rowcount == 0:
            raise KeyError(f"No row id={id} in '{table}'.")
        conn.commit()

    def query(self, table: str, filters: dict | None = None, order_by: str | None = None) -> list:
        cols = TABLES[table]
        conn = self._conn_or_raise()
        sql = f"SELECT {', '.join(cols)} FROM {table}"
        args = []
        if filters:
            clauses = [f"{k} = ?" for k in filters]
            sql += " WHERE " + " AND ".join(clauses)
            args = [str(v) for v in filters.values()]
        if order_by:
            if order_by not in cols:
                raise KeyError(f"Unknown order_by column '{order_by}' for '{table}'.")
            sql += f" ORDER BY {order_by}"
        cur = conn.execute(sql, args)
        return [_from_db(dict(r)) for r in cur.fetchall()]

    def checkpoint(self) -> None:
        conn = self._conn_or_raise()
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None


register_driver("sqlite", SqliteDriver)
