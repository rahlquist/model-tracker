---
name: model-tracker
description: 'Track AI models across agentic sessions. Record system context, run stats, notes, and ratings, then generate usage-weighted ranking reports. Use to track models, log runs, capture session stats, rate models, or generate model ranking reports.'
compatibility: 'Requires Python 3.10+; optional: sqlite3 (stdlib), psycopg (v3) for the PostgreSQL backend.'
metadata:
  version: "1.0"
---

# model-tracker

Track the models you use in agentic AI tools and see which ones really shine.

## What it does

- `setup` — interactive one-time wizard that walks you through backend choice, data paths, auto-record settings, and turn-check-in thresholds. Generates `~/.model-tracker/config.toml`.
- `auto-record` — detects your OS, agent, CPU, RAM, and GPU on every new session and creates a `system_info` row (or reuses the most recent one). Prompt if anything can't be auto-detected.
- `record-session` — end-of-session capture (system → config → models → notes). Each row is written and flushed immediately, so a crash loses at most the row in flight.
- `add-note` / `edit-note` — manage `user_notes` outside any session. Ratings 1–10.
- `edit` — partial update to any column in any table (type-validated, rejects unknown columns and `agent_rating`).
- `list` / `show` — browse rows with equality filters; `show` resolves FK ids to readable summaries.
- `rank` — computes a usage-weighted `agent_rating` per model, writes it back, and prints a report (plain-text or `--markdown`).

Everything flows through a driver registry — the CLI never imports a concrete backend directly.

## Quick start

```bash
# One-time setup — answers a few questions, writes your config
python3 scripts/tracker.py setup

# Start tracking!
python3 scripts/tracker.py record-session --run-id my-session-1 --turn-count 24
```

You can also batch a whole session from a JSON file:
```bash
python3 scripts/tracker.py record-session --from-json session-data.json
```

## Configuration

Config lives at `~/.model-tracker/config.toml` by default. Override the path with `--config PATH` or the `MODEL_TRACKER_CONFIG` environment variable.

### Storage backend

```toml
[storage]
backend = "csv"      # one of: csv, sqlite, postgres

# For CSV:
[storage.csv]
data_dir = "~/.model-tracker/data"

# For SQLite:
[storage.sqlite]
db_path = "~/.model-tracker/data/model-tracker.sqlite"

### PostgreSQL (credentials)

**Keep your database password out of the config file.** The config file can be accidentally committed to git, read by other people, or copied around. Instead, model-tracker looks up your password from a secure vault at runtime.

**How it works — the simple version:**
1. You save your database connection string (server, username, password) in your password manager (Bitwarden) or as an environment variable.
2. In your config file, you just put a reference — like a pointer — that says "look up the password in Bitwarden."
3. When model-tracker starts, it secretly fetches the real credentials from Bitwarden before connecting. The config file never contains your password.

**Step by step — if you're using Bitwarden:**

1. Open Bitwarden and create a new "Secret" entry for your PostgreSQL connection. It can be called anything — "postgres-dsn" or "database" or "db-password" — doesn't matter. Just put your full connection string as the value:
   ```
   postgresql://modeltracker:secretpass@192.168.8.249:5432/modeltracker
   ```

2. Find the secret's UUID (a long unique ID). Open a terminal and run:
   ```bash
   bws secret list <your-project-uuid>
   ```
   You'll see output like:
   ```
   UUID                                 NAME
   2e16ef0f-0349-...  postgres-dsn
   ```
   Copy the UUID (the `2e16ef0f-0349-...` part).

3. Put that UUID in your config file:
   ```toml
   [storage]
   backend = "postgres"
   [storage.postgres]
   dsn = "$BWS:2e16ef0f-0349-4351-97cf-b485011b640b"
   ```
   The `$BWS:` prefix tells model-tracker: "Go get this from Bitwarden."

**Alternative — if you're using environment variables instead:**

If you prefer not to use Bitwarden, you can put the DSN in an environment variable instead:

```toml
[storage]
backend = "postgres"
[storage.postgres]
dsn = "$HERMES_SECRET:pg_dsn"   # reads HERMES_SECRET_PG_DSN from your environment
```

Then set it in your shell or systemd service file:
```bash
export HERMES_SECRET_PG_DSN="postgresql://modeltracker:pass@host:5432/modeltracker"
```

**Alternative — just leave it empty:**

If you set the `MODEL_TRACKER_PG_DSN` environment variable, you can leave the config empty and model-tracker will use that:

```toml
[storage.postgres]
dsn = ""  # will read MODEL_TRACKER_PG_DSN from environment
```

**The three options at a glance:**

| Method | Where the password lives | Config looks like |
|---|---|---|
| Bitwarden | Your password vault | `dsn = "$BWS:uuid-here"` |
| Environment variable (HERMES_SECRET) | Your shell/system | `dsn = "$HERMES_SECRET:pg_dsn"` |
| Environment variable (MODEL_TRACKER_PG_DSN) | Your shell/system | `dsn = ""` |

**Important:** This secret reference syntax works for *any* config value, not just the PostgreSQL DSN. If any value in your config.toml starts with `$BWS:` or `$HERMES_SECRET:`, it will be resolved securely before use.

### Auto-record at session start

When enabled, `auto-record` auto-detects your OS, agent, and hardware at the start of a new session:

```toml
[auto_record]
enabled = true
trigger = "new-session"  # new-session | re-entry | both
```

- `new-session`: only when context is empty (app launch, new chat).
- `re-entry`: when you re-enter a session you've previously exited.
- `both`: always check at app startup.

If auto-detection can't find a field, it prompts you interactively.

### Turn-based check-in

After N turns, the agent asks whether you'd like to rate your experience with the models used in this session:

```toml
[checkin]
turn_threshold = 10  # 0 = disabled
```

### Static hardware overrides

Prefer to record fixed hardware values? Set them here and skip auto-detection:

```toml
[static_hardware]
os_make_version = "Ubuntu 24.04"
agent_make_version = "Hermes 1.0"
hardware_details = "CPU: AMD Ryzen 9 3900X (6 cores); 16 GB RAM"
```

### Ranking weights

```toml
[ranking]
USAGE_WEIGHT = 1.0
PENALTY_INCOMPLETE = 2.0
PENALTY_ERRORS = 1.0
EXCLUDE_INCOMPLETE = true
```

## Commands

### `setup` — one-time wizard

```bash
python3 scripts/tracker.py setup
```

Walks you through:
1. Backend choice (CSV, SQLite, or PostgreSQL)
2. Data path
3. PostgreSQL DSN (if PostgreSQL selected)
4. Auto-record toggle and trigger mode
5. Turn-check-in threshold
6. Hardware info (static vs. auto-detect)

Writes `~/.model-tracker/config.toml`. Run again at any time to reconfigure.

### `auto-record` — capture system info

```bash
python3 scripts/tracker.py auto-record
```

Auto-detects OS, agent, CPU, RAM, and GPU. Creates a `system_info` row (or reuses the most recent one).

### `record-session` — end-of-session capture

```bash
# Interactive — prompted for each field
python3 scripts/tracker.py record-session

# Batched from JSON
python3 scripts/tracker.py record-session --from-json data.json

# With overrides (set by Hermes via /commands)
python3 scripts/tracker.py record-session --run-id ctx-abc123 --turn-count 24
```

Flow: reuse or create `system_info` → create `system_config` (run_id, stats, nturns, etc.) → one `model_info` per model used → optionally `user_notes` per model.

JSON shape (any section may be omitted to prompt):
```json
{
  "system_info": {"os_make_version": "...", "agent_make_version": "...", "hardware_details": "..."},
  "system_config": {"run_id": "r1", "run_name": "nightly", "stats": "...",
                    "nturns": 12, "ctx_length": 128000,
                    "was_complete": true, "was_errors": ""},
  "models": [
    {"model_alias": "opus", "model_name": "claude-opus",
     "model_context_size": 200000, "model_hosted": true, "model_free": false}
  ],
  "notes": [{"model_alias": "opus", "user_notes": "great", "user_rating": 9}]
}
```

### `add-note` — add a note outside a session

```bash
python3 scripts/tracker.py add-note --model-id <id> --note "text" --rating 8
```

Looks up the model by alias or name if `--model-id` is omitted. Rating 1–10.

### `edit-note` — update a note's text or rating

```bash
python3 scripts/tracker.py edit-note <note_id> --note "revised" --rating 10
```

Cannot modify `agent_rating`. After updating `user_rating`, run `rank` to refresh scores.

### `edit` — update any table/column

```bash
python3 scripts/tracker.py edit model_info <id> --set model_context_size=256000
```

Schema-validated: unknown columns are rejected; values are coerced (bools via y/n/true/false/1/0, epochs as integers, ratings 1–10). `agent_rating` is rejected here — only `rank` writes it.

### `list` / `show`

```bash
python3 scripts/tracker.py list system_info [--filter col=value ...] [--order-by col] [--json]
python3 scripts/tracker.py show system_config <id> [--json]
```

`show` resolves FK ids to readable summaries (e.g. `system_config_id_summary: nightly`).

### `rank` — produce the ranking report

```bash
python3 scripts/tracker.py rank [--markdown]
```

Computes `agent_rating` for every model (see `references/RANKING.md`), writes it back, and prints a report with rank, model, scores, flags, and last use.

## Input validation (always enforced)

| Field | Rule |
|---|---|
| `user_rating` | integer 1–10 |
| epoch fields | integers (Unix seconds); nulls allowed where the schema permits |
| booleans | y/n/true/false/1/0 (lenient) |
| unknown columns | rejected with a clear message |
| `agent_rating` | derived only — never user-entered, never writable outside `rank` |

## Durability

Every `insert`/`update` is durable before the call returns: CSV appends + flushes + fsyncs; SQLite commits per write under WAL; Postgres commits per write. An interrupted recording session loses at most the row in flight.

## Common gotchas

- **Model with no notes** — shows as `unranked` in `rank` reports.
- **Incomplete run** — with default `EXCLUDE_INCOMPLETE=true`, its notes are excluded from `base`; if no eligible notes remain, the model stays unranked. Penalties apply only to the affected model.
- **Auto-detection may not find agent version** — set `agent_make_version` in `config.toml` or pass it via the `HERMES_VERSION` environment variable.
- **PostgreSQL requires `psycopg`** — install with `pip install psycopg[binary]`. Only needed if using the `postgres` backend.

## Reference docs (load when needed)

- `references/SCHEMA.md` — full schema for all 4 tables and all 3 backends. Load when you need column names, types, or DDL.
- `references/STORAGE_API.md` — the driver interface contract and how to add a backend. Load **only** when adding or debugging a storage backend.
- `references/RANKING.md` — the ranking algorithm, weights, and a worked example. Load when tuning `rank` behavior or explaining scores.
