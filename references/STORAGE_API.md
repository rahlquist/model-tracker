# STORAGE API — model-tracker

This document is the contract for the storage layer and the guide for adding a
new backend. **Load it only when adding or debugging a storage backend.** For
day-to-day use you never touch this — `tracker.py` and `ranking.py` go through
the registry and know nothing about concrete drivers.

## The interface

Every backend implements `StorageDriver` (in `scripts/storage/__init__.py`):

```python
class StorageDriver(abc.ABC):
    def init(self, config: dict) -> None: ...
    def insert(self, table: str, row: dict) -> str: ...
    def update(self, table: str, id: str, changes: dict) -> None: ...
    def query(self, table: str,
              filters: dict | None = None,
              order_by: str | None = None) -> list[dict]: ...
    def checkpoint(self) -> None: ...
    def close(self) -> None: ...
```

### Method contracts

- **`init(config)`** — connect/open and create tables/files if absent.
  Idempotent. `config` is the loaded TOML dict (see `load_config`).
- **`insert(table, row)`** — write one row. Generate `id` (UUID v7) if absent.
  **Must be durable before returning** (CSV: append + flush + fsync; SQLite:
  commit-per-write under WAL; Postgres: commit-per-write). Returns the row id
  (string). FK integrity: SQL backends enforce via the server; the CSV driver
  verifies referenced ids exist and raises `ValueError` otherwise.
- **`update(table, id, changes)`** — partial update by id, durable. Reject
  unknown columns with `KeyError`. Never the caller's job to set `agent_rating`.
- **`query(table, filters, order_by)`** — simple equality filters
  (`filters={col: val}`); returns list of row dicts with values typed
  (booleans, ints). `order_by` must be a known column.
- **`checkpoint()`** — force-flush any buffered state. No-op if per-write
  durable, but must exist.
- **`close()`** — flush and release resources.

### Authoritative schema

`TABLES` (dict: table → ordered column list) and `FK_COLUMNS`
(`"table.column" → referenced table`) live in `storage/__init__.py` and mirror
`references/SCHEMA.md`. Drivers use `TABLES` for CSV headers and insert ordering;
do not hardcode column lists.

### UUID v7 + config

- Use `from storage import uuid7` — it returns an RFC 9562 v7 `uuid.UUID`
  (stdlib `uuid.uuid7()` when available, else a bundled generator, so the skill
  runs on Python 3.10+).
- Use `from storage import load_config` for config; never read files directly.
  PostgreSQL DSN additionally respects the `MODEL_TRACKER_PG_DSN` env var.

## Registry (the extension point)

```python
from storage import register_driver, get_driver, make_driver, list_drivers
```

- `register_driver(name, cls)` registers a class under a string name.
- `get_driver(name)` returns the class (not instantiated).
- `make_driver(name, config)` instantiates + calls `init(config)`.
- The three built-ins self-register as `"csv"`, `"sqlite"`, `"postgres"` on
  first `get_driver` call (lazy import — Postgres's `psycopg` is only imported
  when `"postgres"` is actually requested).

**Nothing outside `scripts/storage/` may reference a concrete driver.** Add
backends only via the registry.

## Adding a backend (skeleton)

1. Create `scripts/storage/my_driver.py`.
2. Implement `StorageDriver` exactly. Honor durability + FK rules.
3. Self-register at module bottom: `register_driver("my", MyDriver)`.
4. That's it — `tracker.py`/`ranking.py` pick it up via `--config`
   `backend = "my"` (or any code calling `make_driver("my", cfg)`).

```python
# scripts/storage/inmemory_driver.py  (illustrative third-party backend)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from storage import StorageDriver, TABLES, register_driver

class InMemoryDriver(StorageDriver):
    def __init__(self): self._rows = {t: [] for t in TABLES}
    def init(self, config): pass
    def insert(self, table, row):
        import uuid as _u
        if "id" not in row or row["id"] is None: row["id"] = str(_u.uuid7())
        rid = str(row["id"]); self._rows[table].append(dict(row)); return rid
    def update(self, table, id, changes):
        for r in self._rows[table]:
            if r["id"] == str(id):
                r.update(changes); return
        raise KeyError(id)
    def query(self, table, filters=None, order_by=None):
        rows = [dict(r) for r in self._rows[table]
                if not filters or all(str(r.get(k))==str(v) for k,v in filters.items())]
        if order_by: rows.sort(key=lambda x: x.get(order_by))
        return rows
    def checkpoint(self): pass
    def close(self): pass

register_driver("inmemory", InMemoryDriver)
```

The acceptance tests register exactly such an in-memory driver under a new name
and run `list` against it, proving no operation code references concrete drivers.
