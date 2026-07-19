---
name: model-tracker
description: 'Track the AI models you use across agentic sessions. Records per-session stats, system/hardware context, timestamps, freeform notes, and 1-10 user ratings into CSV, SQLite, or PostgreSQL behind one standard API; produces a usage-weighted ranking report. Use when you want to track models, log this run, capture session stats, rate a model, or generate a model ranking report.'
compatibility: Requires Python 3.10+; optional: sqlite3 (stdlib), psycopg (v3) for the PostgreSQL backend.
metadata:
  version: "1.0"
---

# model-tracker

Track the models you use in agentic AI tools and rank them by real usage.

## What it does

- `record-session` — end-of-session capture (system → config → models → notes), each row written immediately so a crash loses at most the row in flight.
- `add-note` / `edit-note` — manage `user_notes` outside any session.
- `edit` — partial update to any column in any table (type-validated; rejects unknown columns; never writes derived `agent_rating`).
- `list` / `show` — browse rows with equality filters; `show` resolves FK ids to readable summaries.
- `rank` — computes a usage-weighted `agent_rating` per model, writes it back, prints a report (plain-text or `--markdown`).

Storage backend is user-selectable: **CSV**, **SQLite**, or **PostgreSQL** — all behind one driver registry. No operation code references a concrete backend.

## Setup

```bash
cp assets/config.example.toml ~/.model-tracker/config.toml
# edit: backend = "csv" | "sqlite" | "postgres"
```

Run every operation through the CLI (also callable by an agent directly):

```bash
python3 scripts/tracker.py --config ~/.model-tracker/config.toml <subcommand> [args]
```

Global flag: `--config PATH` (defaults to `~/.model-tracker/config.toml`; also
`MODEL_TRACKER_CONFIG` env). PostgreSQL DSN: `storage.postgres.dsn` or the
`MODEL_TRACKER_PG_DSN` env var (never hardcode credentials).

## Reference docs (load only when needed)

- `references/SCHEMA.md` — full schema for all 4 tables and all 3 backends. Load when you need column names, types, or DDL.
- `references/STORAGE_API.md` — the driver interface contract and how to add a backend. Load **only** when adding or debugging a storage backend.
- `references/RANKING.md` — the ranking algorithm, weights, and a worked example. Load when tuning `rank` behavior or explaining scores.

## Operations

### record-session (end of a run)
Flow: reuse the most recent `system_info` if the system is unchanged (interactive prompt), else create one → create `system_config` (run_id, run_name, verbatim `stats`, nturns, ctx_length, num_compressed, was_complete, was_errors) → one `model_info` per model used → optionally `user_notes` per model.

Interactive: `record-session` prompts for each field. Non-interactive / batched: pass `--from-json FILE` with this shape (any block may be omitted to prompt):

```json
{
  "system_info": {"os_make_version": "...", "agent_make_version": "...", "hardware_details": "..."},
  "system_config": {"run_id": "r1", "run_name": "nightly", "stats": "...", "nturns": 12,
                    "ctx_length": 128000, "num_compressed": 1, "was_complete": true, "was_errors": ""},
  "models": [{"model_alias": "opus", "model_name": "claude-opus", "model_context_size": 200000,
              "model_hosted": true, "model_free": false}],
  "notes": [{"model_alias": "opus", "user_notes": "great", "user_rating": 9}]
}
```

Each row is inserted and flushed durably as it is collected. UUID v7 ids are
generated app-side. `agent_rating` is never written here.

### add-note (no active session)
`add-note --model-id <id> --note "text" --rating 8`
If `--model-id` omitted, prompt for an alias/name to look the model up. Rating 1–10. Writes a `user_notes` row with `agent_rating = NULL`.

### edit-note
`edit-note <note_id> --note "revised" --rating 10`
Updates `user_notes` / `user_rating` only. **Cannot** modify `agent_rating` (the command rejects it). After any `edit` or `edit-note` that changes `user_rating`, run `rank` again to refresh the derived `agent_rating` values — otherwise `rank` will report stale scores.

### edit (any table/column)
`edit <table> <id> --set col=value [--set col2=value2]`
Schema-validated: unknown columns rejected with a clear message; values coerced
(bools via y/n/true/false/1/0, epochs as integers, ratings 1–10). `agent_rating`
is rejected here — only `rank` writes it. Example:
`edit model_info <id> --set model_context_size=256000`

### list / show
`list <table> [--filter col=value ...] [--order-by col] [--json]`
`show <table> <id> [--json]` — prints the row and resolves FK ids to a readable
summary (e.g. `system_config_id_summary: nightly`).
Tables: `system_info`, `system_config`, `model_info`, `user_notes`.

### rank
`rank [--markdown]`
Computes `agent_rating` for every model (see `references/RANKING.md`), writes it
back to each `user_notes` row, then prints: rank, model, agent_rating, avg
user_rating, total turns, sessions, incomplete/error flags, last use. Weights
are configurable in `config.toml [ranking]`.

## Input validation rules (always enforced)
- `user_rating`: integer 1–10.
- epoch fields: integers (Unix seconds); nulls allowed where the schema permits.
- booleans: y/n/true/false/1/0 (lenient).
- unknown columns: rejected with a clear message.
- `agent_rating`: derived only; never user-entered, never writable outside `rank`.

## Durability
Every `insert`/`update` is durable before the call returns: CSV appends +
flushes + fsyncs; SQLite commits per write under WAL; Postgres commits per
write. An interrupted recording session loses at most the row in flight. CSV FK
integrity is advisory (driver checks referenced ids exist); SQL backends enforce
FKs server-side.

## Common edge cases
- **Resuming after a crash mid-`record-session`:** already-written rows are safe; re-run `record-session` and reuse the existing `system_info` (the prompt offers the most recent one).
    1. **Check what was written:** use `list system_info --json` and `list system_config --json` (or whichever tables were partially written) to see existing rows. For raw inspection, the data dir is configured in `config.toml` — the `[storage]` section sets `backend`, and `[storage.csv]`/`[storage.sqlite]`/`[storage.postgres]` sets the data location (`data_dir`, `db_path`, or `dsn`). Inspect the appropriate path from your config.
    2. **For SQLite:** run `PRAGMA integrity_check;` to confirm WAL consistency after a crash
    3. **Resume:** re-run `record-session` — if the system is unchanged, list existing rows with `list system_info` and reuse the most recent row (or update it with `edit system_info <id> --set os_make_version=...` if hardware changed).
- **Model with no notes:** appears in `rank` as `unranked` (NULL `agent_rating`); it simply has no basis yet.
- **Incomplete run:** with default `EXCLUDE_INCOMPLETE=true`, its notes are excluded from `base`; if that leaves no eligible notes, the model is unranked. Penalties from incomplete or errored runs apply only to the model whose linked runs have those flags — they never affect other models.
- **Switching backends:** point `config.toml` at a different backend; data is not auto-migrated between backends.
