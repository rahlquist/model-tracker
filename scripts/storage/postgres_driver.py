"""PostgreSQL storage driver for model-tracker.

NOTE: This driver requires `psycopg` (v3) and a reachable PostgreSQL server.
It is imported lazily inside this module so CSV/SQLite users need no install.

RUNTIME STATUS: As of build, no live PostgreSQL instance was reachable in the
build environment, so this driver was NOT runtime-tested. It is implemented
strictly against the StorageDriver ABC contract and the schema in
references/SCHEMA.md. Verify against a real server before relying on it.

Backend type mapping (see references/SCHEMA.md):
  UUID v7 -> UUID, BIGINT -> BIGINT, INT -> INTEGER,
  BOOLEAN -> BOOLEAN, TEXT -> TEXT, REAL -> DOUBLE PRECISION.
Real FK constraints are enforced by the server.
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from storage import StorageDriver, TABLES, register_driver  # noqa: E402


_DDL = {
    "system_info": """
        CREATE TABLE IF NOT EXISTS system_info (
            id UUID PRIMARY KEY,
            os_make_version TEXT,
            agent_make_version TEXT,
            hardware_details TEXT,
            created_at BIGINT
        )
    """,
    "system_config": """
        CREATE TABLE IF NOT EXISTS system_config (
            id UUID PRIMARY KEY,
            system_info_id UUID,
            run_id TEXT,
            run_name TEXT,
            stats TEXT,
            nturns BIGINT,
            ctx_length BIGINT,
            num_compressed INTEGER,
            was_complete BOOLEAN,
            was_errors TEXT,
            created_at BIGINT,
            FOREIGN KEY (system_info_id) REFERENCES system_info(id)
        )
    """,
    "model_info": """
        CREATE TABLE IF NOT EXISTS model_info (
            id UUID PRIMARY KEY,
            system_config_id UUID,
            model_alias TEXT,
            model_name TEXT,
            model_source TEXT,
            model_context_size BIGINT,
            model_hosted BOOLEAN,
            model_hosted_location TEXT,
            model_free BOOLEAN,
            model_added BIGINT,
            model_removed BIGINT,
            model_last_use BIGINT,
            created_at BIGINT,
            FOREIGN KEY (system_config_id) REFERENCES system_config(id)
        )
    """,
    "user_notes": """
        CREATE TABLE IF NOT EXISTS user_notes (
            id UUID PRIMARY KEY,
            model_info_id UUID,
            user_notes TEXT,
            user_rating INTEGER,
            agent_rating DOUBLE PRECISION,
            created_at BIGINT,
            FOREIGN KEY (model_info_id) REFERENCES model_info(id)
        )
    """,
}


def _to_db(value):
    """Serialize a logical value for PostgreSQL."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value  # psycopg adapts UUID natively
    return value


class PostgresDriver(StorageDriver):
    def __init__(self) -> None:
        self._dsn = ""
        self._conn = None

    def init(self, config: dict) -> None:
        # Lazy import: only CSV/SQLite users must not pay for psycopg.
        try:
            import psycopg  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "The PostgreSQL backend requires `psycopg` (v3). "
                "Install it with: pip install psycopg[binary]"
            ) from e

        pg = config.get("storage", {}).get("postgres", {})
        dsn = pg.get("dsn") or os.environ.get("MODEL_TRACKER_PG_DSN")
        if not dsn:
            raise RuntimeError(
                "No PostgreSQL DSN. Set storage.postgres.dsn in config or the "
                "MODEL_TRACKER_PG_DSN environment variable."
            )
        self._dsn = dsn

        import psycopg

        self._conn = psycopg.connect(self._dsn, autocommit=True)
        for ddl in _DDL.values():
            self._conn.execute(ddl)

    def _conn_or_raise(self):
        if self._conn is None:
            raise RuntimeError("Driver not initialized (call init() first).")
        return self._conn

    def insert(self, table: str, row: dict) -> str:
        cols = TABLES[table]
        if "id" not in row or row["id"] is None:
            row["id"] = uuid.uuid7()
        rid = str(row["id"])
        # Store as UUID object for the native column type.
        values = [_to_db(row.get(c)) for c in cols]
        conn = self._conn_or_raise()
        placeholders = ", ".join([f"%s"] * len(cols))
        colnames = ", ".join(cols)
        # autocommit=True => commit-per-write (durability rule).
        conn.execute(
            f"INSERT INTO {table} ({colnames}) VALUES ({placeholders})", values
        )
        return rid

    def update(self, table: str, id: str, changes: dict) -> None:
        cols = TABLES[table]
        conn = self._conn_or_raise()
        sets = []
        vals = []
        for k, v in changes.items():
            if k not in cols:
                raise KeyError(f"Unknown column '{k}' for table '{table}'.")
            sets.append(f"{k} = %s")
            vals.append(_to_db(v))
        if not sets:
            return
        vals.append(str(id))
        cur = conn.execute(
            f"UPDATE {table} SET {', '.join(sets)} WHERE id = %s", vals
        )
        if cur.rowcount == 0:
            raise KeyError(f"No row id={id} in '{table}'.")

    def query(self, table: str, filters: dict | None = None, order_by: str | None = None) -> list:
        cols = TABLES[table]
        conn = self._conn_or_raise()
        sql = f"SELECT {', '.join(cols)} FROM {table}"
        args = []
        if filters:
            clauses = [f"{k} = %s" for k in filters]
            sql += " WHERE " + " AND ".join(clauses)
            args = [str(v) for v in filters.values()]
        if order_by:
            if order_by not in cols:
                raise KeyError(f"Unknown order_by column '{order_by}' for '{table}'.")
            sql += f" ORDER BY {order_by}"
        rows = conn.execute(sql, args).fetchall()
        result = []
        for r in rows:
            # psycopg returns UUID objects for UUID columns; normalize to str.
            row = {}
            for i, c in enumerate(cols):
                val = r[i]
                if isinstance(val, uuid.UUID):
                    val = str(val)
                row[c] = val
            result.append(row)
        return result

    def checkpoint(self) -> None:
        # autocommit=True => nothing buffered. No-op.
        return

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


register_driver("postgres", PostgresDriver)
