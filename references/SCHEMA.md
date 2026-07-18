# SCHEMA — model-tracker

Four tables, one standard shape across all three backends. This document is the
authoritative column list. Do **not** add columns or rename. Types below are the
*logical* types; backend-specific physical mappings are in the per-backend
section.

## Global conventions

- **Primary keys** are named `id`, type **UUID version 7 (RFC 9562)**, generated
  **app-side** (never by the database). Canonical form: 36-char lowercase
  `xxxxxxxx-xxxx-7xxx-xxxx-xxxxxxxxxxxx` (version nibble = `7`).
- **Epoch fields** are Unix seconds as integers (no milliseconds).
- **No structured JSON columns.** Freeform dumps stay `TEXT`.
- `created_at` is set app-side at insert time (epoch seconds).

---

## Table 1: `system_info`

| column | logical type | notes |
|---|---|---|
| id | UUID v7 | PK |
| os_make_version | TEXT | OS make and version |
| agent_make_version | TEXT | agent tool make and version |
| hardware_details | TEXT | CPU, RAM, VRAM description |
| created_at | BIGINT | epoch seconds |

One row per distinct physical/agent environment. Reuse the most recent row when
the system is unchanged; otherwise create a new one.

## Table 2: `system_config`

| column | logical type | notes |
|---|---|---|
| id | UUID v7 | PK |
| system_info_id | UUID | FK → system_info.id |
| run_id | TEXT | run identifier as referenced by the agent tool |
| run_name | TEXT | human name for the run, when available |
| stats | TEXT | verbatim statistics dump (e.g. `/status` output); stored as-is |
| nturns | BIGINT | number of turns |
| ctx_length | BIGINT | context length |
| num_compressed | INT | number of compressions |
| was_complete | BOOLEAN | run completed successfully |
| was_errors | TEXT | any and all errors (empty = none) |
| created_at | BIGINT | epoch seconds |

One row per recorded session/run. `stats` is opaque; the skill never parses it.

## Table 3: `model_info`

| column | logical type | notes |
|---|---|---|
| id | UUID v7 | PK |
| system_config_id | UUID | FK → system_config.id |
| model_alias | TEXT | |
| model_name | TEXT | |
| model_source | TEXT | |
| model_context_size | BIGINT | |
| model_hosted | BOOLEAN | |
| model_hosted_location | TEXT | |
| model_free | BOOLEAN | |
| model_added | BIGINT | epoch seconds, nullable |
| model_removed | BIGINT | epoch seconds, nullable |
| model_last_use | BIGINT | epoch seconds, nullable |
| created_at | BIGINT | epoch seconds |

One row per model used in a run. `model_last_use` is updated on each use.

## Table 4: `user_notes`

| column | logical type | notes |
|---|---|---|
| id | UUID v7 | PK |
| model_info_id | UUID | FK → model_info.id |
| user_notes | TEXT | user's freeform notes |
| user_rating | INT | 1–10, validated on write |
| agent_rating | REAL | **derived** — never user-entered; populated/refreshed only by `rank` |
| created_at | BIGINT | epoch seconds |

`agent_rating` is nullable until the first `rank` run. No operation other than
`rank` may write it.

---

## Backend physical mappings

### PostgreSQL

| logical | PG type |
|---|---|
| UUID v7 | `UUID` |
| BIGINT | `BIGINT` |
| INT | `INTEGER` |
| BOOLEAN | `BOOLEAN` |
| TEXT | `TEXT` |
| REAL | `DOUBLE PRECISION` |

- Real FK constraints, enforced by the server.
- DDL sketch:

```sql
CREATE TABLE IF NOT EXISTS system_info (
  id UUID PRIMARY KEY,
  os_make_version TEXT,
  agent_make_version TEXT,
  hardware_details TEXT,
  created_at BIGINT
);
CREATE TABLE IF NOT EXISTS system_config (
  id UUID PRIMARY KEY,
  system_info_id UUID REFERENCES system_info(id),
  run_id TEXT,
  run_name TEXT,
  stats TEXT,
  nturns BIGINT,
  ctx_length BIGINT,
  num_compressed INTEGER,
  was_complete BOOLEAN,
  was_errors TEXT,
  created_at BIGINT
);
CREATE TABLE IF NOT EXISTS model_info (
  id UUID PRIMARY KEY,
  system_config_id UUID REFERENCES system_config(id),
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
  created_at BIGINT
);
CREATE TABLE IF NOT EXISTS user_notes (
  id UUID PRIMARY KEY,
  model_info_id UUID REFERENCES model_info(id),
  user_notes TEXT,
  user_rating INTEGER,
  agent_rating DOUBLE PRECISION,
  created_at BIGINT
);
```

### SQLite

| logical | SQLite type | notes |
|---|---|---|
| UUID v7 | `TEXT` | 36-char canonical lowercase |
| BIGINT | `INTEGER` | |
| INT | `INTEGER` | |
| BOOLEAN | `INTEGER` | 0/1 |
| TEXT | `TEXT` | |
| REAL | `REAL` | |

- `PRAGMA foreign_keys=ON` on **every** connection.
- `PRAGMA journal_mode=WAL` for crash-safety.
- FK enforcement is on by default with the pragma; invalid FKs are rejected at insert.

### CSV

- One file per table (`system_info.csv`, `system_config.csv`, `model_info.csv`,
  `user_notes.csv`) in the configured data directory.
- Header row = column names in the schema order shown above.
- UUIDs: canonical 36-char lowercase strings.
- Booleans: `true`/`false` (lowercase).
- Nulls: empty field.
- FK integrity is **advisory**: the driver verifies a referenced id exists before
  insert and errors clearly if not. No enforcement across separate processes
  beyond that check.
- Each write is an append + flush + fsync (see STORAGE_API.md durability rule).

---

## Column order (authoritative, for CSV headers and insert dicts)

```
system_info:       id, os_make_version, agent_make_version, hardware_details, created_at
system_config:     id, system_info_id, run_id, run_name, stats, nturns, ctx_length,
                   num_compressed, was_complete, was_errors, created_at
model_info:        id, system_config_id, model_alias, model_name, model_source,
                   model_context_size, model_hosted, model_hosted_location,
                   model_free, model_added, model_removed, model_last_use, created_at
user_notes:        id, model_info_id, user_notes, user_rating, agent_rating, created_at
```
