"""CSV storage driver for model-tracker.

One file per table in a user-chosen data directory. Header row = schema column
order. UUIDs canonical lowercase strings; booleans `true`/`false`; nulls empty.

Durability: every insert/update appends, flushes, and fsyncs the file handle
before returning, so an interrupted recording session loses at most the row in
flight.

FK integrity is advisory: before inserting a row with an FK column we verify the
referenced id exists in the parent table's file and error clearly otherwise.
"""

from __future__ import annotations

import csv
import os
import sys
import uuid

# Allow running as a standalone module or as part of the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from storage import StorageDriver, TABLES, FK_COLUMNS, register_driver  # noqa: E402


def _to_str(value) -> str:
    """Serialize a logical value to its CSV cell text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


# Columns that are integers when non-empty.
_INT_COLS = {
    "created_at", "nturns", "ctx_length", "num_compressed", "model_context_size",
    "model_added", "model_removed", "model_last_use", "user_rating",
}
# Columns that are floats when non-empty.
_FLOAT_COLS = {"agent_rating"}


def _from_str(text: str, column: str):
    """Parse a CSV cell back into a Python value, typed by column name hints."""
    if text is None or text == "":
        return None
    # Boolean columns (by name) -> bool
    if column in (
        "was_complete",
        "model_hosted",
        "model_free",
    ):
        return text.strip().lower() in ("true", "1", "yes", "y")
    if column in _INT_COLS:
        try:
            return int(text)
        except ValueError:
            return text
    if column in _FLOAT_COLS:
        try:
            return float(text)
        except ValueError:
            return text
    return text


class CsvDriver(StorageDriver):
    def __init__(self) -> None:
        self._data_dir: str = ""
        self._handles: dict = {}  # table -> open file handle (append mode)
        self._paths: dict = {}

    # -- lifecycle ---------------------------------------------------------

    def init(self, config: dict) -> None:
        data_dir = config.get("storage", {}).get("csv", {}).get("data_dir", "~/.model-tracker/data")
        self._data_dir = os.path.expanduser(data_dir)
        os.makedirs(self._data_dir, exist_ok=True)
        for table, cols in TABLES.items():
            path = os.path.join(self._data_dir, f"{table}.csv")
            self._paths[table] = path
            if not os.path.exists(path):
                # Create with header row.
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(cols)
                    f.flush()
                    os.fsync(f.fileno())

    # -- internal helpers --------------------------------------------------

    def _path(self, table: str) -> str:
        if table not in TABLES:
            raise KeyError(f"Unknown table '{table}'. Known: {sorted(TABLES)}")
        return self._paths[table]

    def _verify_fk(self, table: str, row: dict) -> None:
        # Check each FK column present in the row.
        for col, target in FK_COLUMNS.items():
            tname, cname = col.split(".")
            if tname != table:
                continue
            if cname in row and row[cname] is not None:
                ref_id = str(row[cname])
                if not self._id_exists(target, ref_id):
                    raise ValueError(
                        f"FK violation: {col}={ref_id} not found in '{target}'."
                    )

    def _id_exists(self, table: str, id_str: str) -> bool:
        path = self._path(table)
        if not os.path.exists(path):
            return False
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r.get("id") == id_str:
                    return True
        return False

    def _append(self, table: str, cols, values) -> None:
        path = self._path(table)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(values)
            f.flush()
            os.fsync(f.fileno())

    # -- API ---------------------------------------------------------------

    def insert(self, table: str, row: dict) -> str:
        cols = TABLES[table]
        # Generate id if missing.
        if "id" not in row or row["id"] is None:
            row["id"] = uuid.uuid7()
        rid = str(row["id"])
        # Advisory FK check.
        self._verify_fk(table, row)
        # Build ordered values, filling missing with empty.
        values = [_to_str(row.get(c)) for c in cols]
        self._append(table, cols, values)
        return rid

    def update(self, table: str, id: str, changes: dict) -> None:
        cols = TABLES[table]
        path = self._path(table)
        tmp = path + ".tmp"
        found = False
        with open(path, "r", newline="", encoding="utf-8") as fin, \
                open(tmp, "w", newline="", encoding="utf-8") as fout:
            reader = csv.DictReader(fin)
            writer = csv.DictWriter(fout, fieldnames=cols)
            writer.writeheader()
            for r in reader:
                if r["id"] == str(id):
                    found = True
                    for k, v in changes.items():
                        if k not in cols:
                            raise KeyError(f"Unknown column '{k}' for table '{table}'.")
                        r[k] = _to_str(v)
                writer.writerow({c: r.get(c, "") for c in cols})
            fout.flush()
            os.fsync(fout.fileno())
        if not found:
            os.remove(tmp)
            raise KeyError(f"No row id={id} in '{table}'.")
        os.replace(tmp, path)

    def query(self, table: str, filters: dict | None = None, order_by: str | None = None) -> list:
        cols = TABLES[table]
        path = self._path(table)
        if not os.path.exists(path):
            return []
        result = []
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                row = {c: _from_str(r.get(c), c) for c in cols}
                # filters
                if filters:
                    ok = all(
                        str(row.get(k)) == str(v) for k, v in filters.items()
                    )
                    if not ok:
                        continue
                result.append(row)
        if order_by:
            result.sort(
                key=lambda x: (x.get(order_by) is None, x.get(order_by)),
                reverse=False,
            )
        return result

    def checkpoint(self) -> None:
        # Writes are fsync'd per write; nothing buffered. No-op.
        return

    def close(self) -> None:
        return


register_driver("csv", CsvDriver)
