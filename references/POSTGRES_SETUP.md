# PostgreSQL Setup — model-tracker

model-tracker supports PostgreSQL as a storage backend. This guide is for people
setting it up by hand. If you ran `tracker.py setup`, the wizard can build the
connection string for you interactively — but you still need a server, a role,
and a database, which is what this document covers.

## 1. You need a running PostgreSQL server

model-tracker does **not** install or run PostgreSQL for you. You need a server
that the machine running model-tracker can reach over the network (or locally).

Install (examples — adapt to your OS):
- Debian/Ubuntu: `sudo apt install postgresql`
- Fedora/RHEL: `sudo dnf install postgresql-server && sudo postgresql-setup --initdb && sudo systemctl enable --now postgresql`
- macOS: `brew install postgresql && brew services start postgresql`
- Docker: `docker run -d --name pg -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16`

Make sure it's listening on the host/port you'll connect to (default 5432).

## 2. The DSN, piece by piece

model-tracker connects with a **DSN** (data source name) — one string that
contains everything needed to reach the database:

```
postgresql://USER:PASSWORD@HOST:PORT/DATABASE
```

| Part | Meaning | Example |
|------|---------|---------|
| `postgresql://` | Fixed scheme that model-tracker (psycopg) expects | `postgresql://` |
| `USER` | The PostgreSQL role (login user) to connect as | `modeltracker` |
| `PASSWORD` | That role's password | `s3cret` |
| `HOST` | Server hostname or IP. `localhost` for a local server | `localhost` or `192.168.8.249` |
| `PORT` | Listening port (default 5432) | `5432` |
| `DATABASE` | The database (NOT the schema) to use | `modeltracker` |

Concrete example:
```
postgresql://modeltracker:s3cret@localhost:5432/modeltracker
```

Special characters in the password must be percent-encoded (e.g. `@` → `%40`,
`:` → `%3A`, `/` → `%2F`). If your password is simple, you won't need this.

## 3. Create the role and database (one-time)

Connect to PostgreSQL as a superuser (typically the built-in `postgres` role):

```bash
# Linux (peer auth usually lets you sudo in):
sudo -u postgres psql

# Or, if you already have a superuser password:
psql -U postgres -h localhost
```

At the `postgres=#` prompt, run:

```sql
-- 1. A dedicated login role (user). Pick a real password.
CREATE ROLE modeltracker WITH LOGIN PASSWORD 'choose-a-strong-password';

-- 2. A database owned by that role.
CREATE DATABASE modeltracker OWNER modeltracker;

-- 3. (Optional but recommended) explicit privileges.
GRANT ALL PRIVILEGES ON DATABASE modeltracker TO modeltracker;
```

Then exit: `\q`

> You do **not** need to create the tables. model-tracker auto-creates all four
> tables (`system_info`, `system_config`, `model_info`, `user_notes`) on first
> connection. Just provide a role that can CREATE TABLE in the target database.

## 4. Test the connection

Before pointing model-tracker at it, confirm the role can connect:

```bash
psql "postgresql://modeltracker:choose-a-strong-password@localhost:5432/modeltracker" \
  -c "SELECT current_database(), current_user;"
```

If that returns your database and user, you're good.

## 5. Give the DSN to model-tracker

The password should **not** live in `config.toml` in plaintext. You have three options:

### A. Environment variable (recommended)
Leave the config empty and export the DSN in your shell:
```bash
export MODEL_TRACKER_PG_DSN="postgresql://modeltracker:PASSWORD@HOST:5432/modeltracker"
```
Add that line to `~/.bashrc` / `~/.profile` to persist it. model-tracker reads
`MODEL_TRACKER_PG_DSN` automatically when `storage.postgres.dsn` is empty.

### B. Directly in config.toml (simplest, less secure)
```toml
[storage]
backend = "postgres"
[storage.postgres]
dsn = "postgresql://modeltracker:PASSWORD@HOST:5432/modeltracker"
```
The password is stored in plaintext in the file. Don't commit this file to git.

### C. Bitwarden Secrets Manager
Store the full DSN as a secret in BWSM, then reference its UUID:
```toml
[storage]
backend = "postgres"
[storage.postgres]
dsn = "$BWS:2e16ef0f-0349-4351-97cf-b485011b640b"
```
model-tracker resolves `$BWS:<uuid>` at runtime via the `bws` CLI.

## 6. Install the driver

Only the PostgreSQL backend needs an extra package:
```bash
pip install psycopg[binary]
```
CSV and SQLite use the standard library and need nothing.

## 7. Verify from model-tracker

```bash
python3 scripts/tracker.py auto-record    # creates a system_info row
python3 scripts/tracker.py record-session --run-id test-1 --turn-count 1
python3 scripts/tracker.py list system_info
```
If rows appear, the backend works.

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `connection refused` | Server not running, or wrong HOST/PORT. Check it's listening (`ss -ltnp \| grep 5432`). |
| `password authentication failed` | Wrong USER/PASSWORD, or the role has no LOGIN. Re-check `CREATE ROLE ... WITH LOGIN`. |
| `database "X" does not exist` | You connected to a database you didn't create. Create it (§3) or fix the DATABASE part of the DSN. |
| `role "X" does not exist` | The USER in the DSN wasn't created. |
| `permission denied` creating tables | The role isn't the DB owner and lacks CREATE. Run as owner or `GRANT`. |
| `fe_sendauth: no password supplied` | `pg_hba.conf` requires a password but none was in the DSN — include PASSWORD. |
