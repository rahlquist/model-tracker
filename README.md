# model-tracker

An Agent Skill that lets you **track the AI models you use across agentic
sessions** and **rank them by real usage**.

It records, per session:

- the model(s) used, their source/hosting/context size,
- the system & hardware context the run happened on,
- start/stop-style timestamps and verbatim `/status` statistics,
- freeform notes and a 1–10 user rating,

…then produces a **usage-weighted ranking report** (`agent_rating`) across all
recorded runs.

Storage is **user-selectable**: **CSV**, **SQLite**, or **PostgreSQL** — all
behind one standard driver API, so third parties can add new backends without
touching the rest of the skill.

---

## Why

If you drive several models through an agent, you eventually want answers to:

- *Which model actually earns its keep for the work I do?*
- *How much did I lean on each one, and did the runs complete cleanly?*
- *What did I think of model X the last three times I used it?*

`model-tracker` captures that evidence as you go and turns it into a ranked,
comparable picture — instead of a fading memory of "I think opus was good."

---

## Install

The skill is a portable directory. Copy it into your agent's skills folder, or
just run the scripts directly:

```bash
# 1. pick a backend
cp assets/config.example.toml ~/.model-tracker/config.toml
#    edit: backend = "csv" | "sqlite" | "postgres"

# 2. run operations
python3 scripts/tracker.py --config ~/.model-tracker/config.toml record-session
python3 scripts/tracker.py --config ~/.model-tracker/config.toml add-note
python3 scripts/tracker.py --config ~/.model-tracker/config.toml rank
```

**Requirements:** Python 3.10+. The CSV and SQLite backends use only the
standard library (`sqlite3`, and `tomllib` on 3.11+). The PostgreSQL backend
needs `psycopg` (v3), imported lazily — CSV/SQLite users never install it:

```bash
pip install "psycopg[binary]"   # only for the postgres backend
```

---

## Quick start

```bash
# End-of-session capture (interactive, or --from-json for batch):
python3 scripts/tracker.py record-session --from-json run.json

# Add a note for a model later, outside any session:
python3 scripts/tracker.py add-note --model-id <id> --note "fast but terse" --rating 8

# Edit a note / rating (never touches the derived agent_rating):
python3 scripts/tracker.py edit-note <note_id> --rating 9

# Edit any column in any table (type-validated):
python3 scripts/tracker.py edit model_info <id> --set model_context_size=256000

# List / inspect:
python3 scripts/tracker.py list model_info --filter model_hosted=true
python3 scripts/tracker.py show model_info <id>

# Rank:
python3 scripts/tracker.py rank            # plain-text table
python3 scripts/tracker.py rank --markdown # Markdown table
```

### Batched `record-session`

Pass `--from-json FILE` to capture a whole run in one shot (any block may be
omitted to prompt interactively):

```json
{
  "system_info": {"os_make_version": "Linux 6.1", "agent_make_version": "Hermes 1.0", "hardware_details": "16c/32G"},
  "system_config": {"run_id": "r1", "run_name": "nightly", "stats": "<verbatim /status dump>",
                    "nturns": 12, "ctx_length": 128000, "num_compressed": 1,
                    "was_complete": true, "was_errors": ""},
  "models": [{"model_alias": "opus", "model_name": "claude-opus", "model_context_size": 200000,
              "model_hosted": true, "model_free": false}],
  "notes": [{"model_alias": "opus", "user_notes": "great", "user_rating": 9}]
}
```

---

## How it works

### Data model

Four tables, one shape across all backends:

| table | purpose |
|---|---|
| `system_info` | one row per distinct OS/agent/hardware environment |
| `system_config` | one row per recorded run (stats, turns, completeness, errors) |
| `model_info` | one row per model used in a run |
| `user_notes` | freeform notes + 1–10 `user_rating` (+ derived `agent_rating`) |

Full schema, types, and per-backend DDL/format mappings: **`references/SCHEMA.md`**.

Key conventions (all enforced by the code, not trusted to the DB):

- **Primary keys are UUID version 7** (RFC 9562), generated app-side.
- **Epoch fields are Unix seconds** (integers).
- **No structured JSON columns** — `stats` is stored verbatim as text.
- `created_at` is set at insert time.

### Storage backends

| backend | file/store | notes |
|---|---|---|
| `csv` | one `.csv` per table in a data dir | header = schema columns; nulls empty; booleans `true`/`false`; FK checked before insert (advisory) |
| `sqlite` | single `.sqlite` file | UUID as TEXT; `PRAGMA foreign_keys=ON` + `journal_mode=WAL` |
| `postgres` | a database | native `UUID`/`BIGINT`/`BOOLEAN`; real FK constraints |

Every `insert`/`update` is **durable before the call returns** (CSV: append +
flush + fsync; SQLite/Postgres: commit-per-write). An interrupted recording
session loses at most the row in flight.

### Adding a backend

Backends are a registry, not a switch statement. Implement the `StorageDriver`
interface and call `register_driver("name", cls)`. Nothing in `tracker.py` or
`ranking.py` references a concrete driver. Full contract + skeleton:
**`references/STORAGE_API.md`**.

---

## Ranking

`rank` computes, per model (grouped by `model_name`, falling back to
`model_alias`):

```
base    = avg(user_rating over eligible notes)        # null if none -> unranked
usage   = sum(nturns over linked system_config rows)
score   = base * (1 + log10(1 + usage) * USAGE_WEIGHT)
if any linked run was_complete = false: score -= PENALTY_INCOMPLETE
if any linked run had errors:         score -= PENALTY_ERRORS
agent_rating = clamp(score, 1.0, 10.0)
```

- With `EXCLUDE_INCOMPLETE=true` (default), notes from incomplete runs are
  dropped from `base`; if that leaves no eligible notes, the model is
  **unranked** (`agent_rating` stays `NULL`).
- Tie-breaks: higher total `nturns`, then most recent `model_last_use`.
- Weights live in `scripts/ranking.py` and are overridable in
  `config.toml [ranking]`:

  | key | default |
  |---|---|
  | `USAGE_WEIGHT` | `1.0` |
  | `PENALTY_INCOMPLETE` | `2.0` |
  | `PENALTY_ERRORS` | `1.0` |
  | `EXCLUDE_INCOMPLETE` | `true` |

Algorithm, weights, and a worked example: **`references/RANKING.md`**.

---

## CLI reference

| subcommand | what it does |
|---|---|
| `record-session` | end-of-run capture (system → config → models → notes), each row written immediately |
| `add-note` | add a `user_notes` row for an existing model, outside any session |
| `edit-note` | edit `user_notes`/`user_rating` by note id (never `agent_rating`) |
| `edit` | partial update to any column in any table (type-validated; rejects unknown columns) |
| `list` | list table rows with optional equality filters |
| `show` | show one row by id, resolving FK ids to readable summaries |
| `rank` | compute ranking, write `agent_rating` back, print the report |

Global flag: `--config PATH` (defaults to `~/.model-tracker/config.toml`; also
`MODEL_TRACKER_CONFIG` env). PostgreSQL DSN: `storage.postgres.dsn` or the
`MODEL_TRACKER_PG_DSN` env var — never hardcode credentials.

### Validation rules (always enforced)

- `user_rating`: integer 1–10.
- epoch fields: integers (Unix seconds); nulls allowed where the schema permits.
- booleans: `y`/`n`/`true`/`false`/`1`/`0` (lenient).
- unknown columns: rejected with a clear message.
- `agent_rating`: **derived only** — never user-entered, never writable outside `rank`.

---

## Repository layout

```
model-tracker/
├── SKILL.md                 # skill manifest + instructions
├── assets/
│   └── config.example.toml  # backend selection + connection template
├── references/
│   ├── SCHEMA.md            # full schema, all backends
│   ├── STORAGE_API.md       # driver contract + how to add a backend
│   └── RANKING.md           # ranking algorithm + worked example
└── scripts/
    ├── tracker.py           # CLI entry point (all operations)
    ├── ranking.py           # agent_rating computation
    ├── storage/
    │   ├── __init__.py      # StorageDriver ABC, registry, UUID v7, config
    │   ├── csv_driver.py
    │   ├── sqlite_driver.py
    │   └── postgres_driver.py
    ├── acceptance.py        # acceptance tests 1-7
    └── frontmatter_check.py # SKILL.md §2 frontmatter checklist
```

---

## Status

- ✅ CSV + SQLite backends — runtime-tested (smoke tests + acceptance suite).
- ⚠️ PostgreSQL backend — implemented strictly against the driver contract and
  schema, but **not runtime-tested** in the build environment (no live
  PostgreSQL was reachable). Verify against a real server before relying on it.
- ✅ All 8 acceptance criteria from the build spec pass (see `scripts/acceptance.py`).

## License

MIT — see [LICENSE](LICENSE).
