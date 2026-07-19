"""model-tracker storage layer — driver ABC, registry, UUID v7, config.

Nothing in here knows about CSV vs SQLite vs Postgres. Drivers self-register;
tracker.py and ranking.py resolve drivers only through get_driver().
"""

from __future__ import annotations

import abc
import os
import subprocess
import sys
import re
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Secret resolution — no credentials in config files
# ---------------------------------------------------------------------------

# Patterns: $HERMES_SECRET:key or $BWS:uuid
_PAT_HERMES = re.compile(r'^\$HERMES_SECRET:(.+)$')
_PAT_BWS = re.compile(r'^\$BWS:([0-9a-f-]{36})$')


def resolve_secret(value: str) -> str:
    """Resolve a config value that may reference secrets.

    Resolution order:
      1. Direct string (returned as-is if it doesn't start with `$`).
      2. `$HERMES_SECRET:key` → reads `HERMES_SECRET_<key>` env var.
      3. `$BWS:uuid` → runs `bws secret get <uuid> --output env` and evals.
    """
    if not isinstance(value, str) or not value.startswith("$"):
        return value

    # 1. HERMES_SECRET env var
    m = _PAT_HERMES.match(value)
    if m:
        key = m.group(1)
        env_key = f"HERMES_SECRET_{key.upper().replace('-', '_').replace('.', '_')}"
        env_val = os.environ.get(env_key)
        if env_val:
            return env_val
        raise EnvironmentError(
            f"Secret '{key}' referenced via $HERMES_SECRET:{key} but "
            f"env var {env_key} is not set."
        )

    # 2. Bitwarden Secrets Manager
    m = _PAT_BWS.match(value)
    if m:
        uuid_str = m.group(1)
        try:
            proc = subprocess.run(
                ["/home/rahlquist/.local/bin/bws", "secret", "get", uuid_str, "--output", "env"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                raise EnvironmentError(
                    f"bws secret get {uuid_str} failed (exit {proc.returncode}): {proc.stderr.strip()}"
                )
            # Output is like: export GITHUB_TOKEN="ghp_..."
            # Extract just the value.
            env_str = proc.stdout.strip()
            eval_env: Dict[str, str] = {}
            for line in env_str.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Strip 'export ' prefix if present.
                if line.startswith("export "):
                    line = line[7:]
                eq = line.index("=")
                key = line[:eq].strip()
                # Extract value between quotes.
                val = line[eq+1:].strip().strip("'\"")
                os.environ[key] = val
                eval_env[key] = val
            # Return the first (and usually only) secret value found.
            if eval_env:
                return list(eval_env.values())[0]
            return env_str
        except FileNotFoundError:
            raise EnvironmentError(
                "bws CLI not found at /home/rahlquist/.local/bin/bws. "
                "Cannot resolve $BWS secret."
            )

    # Unknown prefix — return raw so the caller can handle it.
    return value


def _resolve_secrets_recursive(obj: Any) -> Any:
    """Walk a config dict/list and call resolve_secret() on every string value."""
    if isinstance(obj, dict):
        return {k: _resolve_secrets_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_secrets_recursive(item) for item in obj]
    if isinstance(obj, str) and obj.startswith("$"):
        return resolve_secret(obj)
    return obj

# ---------------------------------------------------------------------------
# UUID v7 (RFC 9562)
# ---------------------------------------------------------------------------


def uuid7() -> "object":
    """Generate an RFC 9562 UUID version 7 (time-ordered).

    Uses stdlib uuid.uuid7() when available (CPython 3.14+), otherwise a
    bundled RFC-9562-compliant generator so the skill runs on 3.10+.
    """
    try:
        import uuid as _uuid

        return _uuid.uuid7()
    except AttributeError:
        return _uuid7_fallback()


def _uuid7_fallback() -> "object":
    """RFC 9562 UUIDv7, timestamp-ordered. ~20 lines, no deps."""
    import os as _os
    import time as _time
    import uuid as _uuid

    # 48-bit unix ms timestamp
    ts = int(_time.time() * 1000)
    rand = _os.urandom(10)
    # layout: 48 bits ts, 4 bits ver(7), 12 bits rand_a, 2 bits var(10), 62 bits rand_b
    # Build via ints to avoid bit-math on bytes.
    value = (ts & 0xFFFFFFFFFFFF) << 80
    value |= (0x7) << 76
    value |= (int.from_bytes(rand[0:2], "big") & 0x0FFF) << 64
    value |= (0b10) << 62
    value |= int.from_bytes(rand[2:10], "big") & ((1 << 62) - 1)
    # uuid.UUID accepts a 128-bit int
    return _uuid.UUID(int=value)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

# Minimal TOML reader fallback for Python < 3.11 (no tomllib).
# Supports exactly the subset used by config.example.toml: sections, simple
# key = value (str/int/float/bool), comments (#), and nested [a.b] sections.

_TRUE = {"true", "yes", "1", "on"}
_FALSE = {"false", "no", "0", "off"}


def _toml_coerce(token: str) -> Any:
    token = token.strip()
    if (token.startswith('"') and token.endswith('"')) or (
        token.startswith("'") and token.endswith("'")
    ):
        return token[1:-1]
    low = token.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token  # leave as string


def _minimal_toml_load(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    cur: Dict[str, Any] = root
    stack: List[Dict[str, Any]] = [root]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            path = line[1:-1].strip().split(".")
            cur = root
            for seg in path:
                cur = cur.setdefault(seg, {})
            stack.append(cur)
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            cur[key.strip()] = _toml_coerce(val)
    return root


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load config from a TOML file.

    Resolution order:
      1. explicit ``path`` argument
      2. ``--config`` is handled by the caller; here we also honor
         ``MODEL_TRACKER_CONFIG`` env (used in tests)
      3. ``~/.model-tracker/config.toml``

    Returns a dict. Missing file -> sensible defaults (backend 'csv',
    data_dir under ~/.model-tracker/data). Never raises on missing file.
    """
    candidates = []
    if path:
        candidates.append(path)
    if os.environ.get("MODEL_TRACKER_CONFIG"):
        candidates.append(os.environ["MODEL_TRACKER_CONFIG"])
    candidates.append(os.path.expanduser("~/.model-tracker/config.toml"))

    cfg: Dict[str, Any] = {}
    for cand in candidates:
        if cand and os.path.isfile(cand):
            text = open(cand, "r", encoding="utf-8").read()
            if sys.version_info >= (3, 11):
                import tomllib

                cfg = tomllib.loads(text)
            else:
                cfg = _minimal_toml_load(text)
            break

    # Apply defaults for anything absent.
    storage = cfg.setdefault("storage", {})
    storage.setdefault("backend", "csv")
    storage.setdefault("csv", {}).setdefault(
        "data_dir", os.path.expanduser("~/.model-tracker/data")
    )
    storage.setdefault("sqlite", {}).setdefault(
        "db_path", os.path.expanduser("~/.model-tracker/data/model-tracker.sqlite")
    )
    # Postgres DSN may come from env.
    pg = storage.setdefault("postgres", {})
    env_dsn = os.environ.get("MODEL_TRACKER_PG_DSN")
    if env_dsn:
        pg["dsn"] = env_dsn
    cfg.setdefault("ranking", {})

    # --- auto_record ---
    ar = cfg.setdefault("auto_record", {})
    ar.setdefault("enabled", False)
    ar.setdefault("trigger", "new-session")

    # --- checkin ---
    ch = cfg.setdefault("checkin", {})
    ch.setdefault("turn_threshold", 0)  # 0 = disabled

    # --- static_hardware ---
    sh = cfg.setdefault("static_hardware", {})
    sh.setdefault("os_make_version", "")
    sh.setdefault("agent_make_version", "")
    sh.setdefault("hardware_details", "")

    # Resolve any secret references before returning.
    cfg = _resolve_secrets_recursive(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Driver ABC + registry
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Authoritative schema (column order). Mirrors references/SCHEMA.md.
# Drivers use this for CSV headers and to validate/order inserts.
# Do NOT add or rename columns here.
# ---------------------------------------------------------------------------

TABLES: Dict[str, List[str]] = {
    "system_info": [
        "id", "os_make_version", "agent_make_version", "hardware_details", "created_at",
    ],
    "system_config": [
        "id", "system_info_id", "run_id", "run_name", "stats", "nturns",
        "ctx_length", "num_compressed", "was_complete", "was_errors", "created_at",
    ],
    "model_info": [
        "id", "system_config_id", "model_alias", "model_name", "model_source",
        "model_context_size", "model_hosted", "model_hosted_location",
        "model_free", "model_added", "model_removed", "model_last_use", "created_at",
    ],
    "user_notes": [
        "id", "model_info_id", "user_notes", "user_rating", "agent_rating", "created_at",
    ],
}

# FK column -> referenced table (advisory for CSV; enforced for SQL).
FK_COLUMNS: Dict[str, str] = {
    "system_config.system_info_id": "system_info",
    "model_info.system_config_id": "system_config",
    "user_notes.model_info_id": "model_info",
}


class StorageDriver(abc.ABC):
    """Standard data-access interface for model-tracker backends.

    Every driver (built-in or third-party) implements this exact contract.
    Implementations MUST flush durably before insert()/update() return so an
    interrupted session loses at most the row in flight.
    """

    @abc.abstractmethod
    def init(self, config: dict) -> None:
        """Connect/open and create tables/files if absent (idempotent)."""
        raise NotImplementedError

    @abc.abstractmethod
    def insert(self, table: str, row: dict) -> str:
        """Write one row, flush durable before returning, return the id."""
        raise NotImplementedError

    @abc.abstractmethod
    def update(self, table: str, id: str, changes: dict) -> None:
        """Partial update by id, flushed durably."""
        raise NotImplementedError

    @abc.abstractmethod
    def query(
        self, table: str, filters: Optional[dict] = None, order_by: Optional[str] = None
    ) -> List[dict]:
        """Simple equality filters; return list of row dicts."""
        raise NotImplementedError

    @abc.abstractmethod
    def checkpoint(self) -> None:
        """Force-flush any buffered state. No-op if already per-write durable."""
        raise NotImplementedError

    @abc.abstractmethod
    def close(self) -> None:
        """Close/flush and release resources."""
        raise NotImplementedError


_REGISTRY: Dict[str, type] = {}


def register_driver(name: str, cls: type) -> None:
    """Register a storage driver under ``name``."""
    _REGISTRY[name] = cls


def get_driver(name: str) -> type:
    """Return the driver class registered under ``name`` (not instantiated)."""
    if name not in _REGISTRY:
        # Lazily import built-ins so importing this package never hard-fails
        # before the driver modules exist, and so we don't import psycopg
        # (a heavy optional dep) unless Postgres is actually requested.
        try:
            from . import csv_driver  # noqa: F401
            from . import sqlite_driver  # noqa: F401
            from . import postgres_driver  # noqa: F401
        except Exception as e:  # pragma: no cover - defensive
            raise KeyError(
                f"Unknown storage driver '{name}'. Built-ins failed to load: {e}. "
                f"Known: {sorted(_REGISTRY)}"
            ) from e
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown storage driver '{name}'. Known: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def list_drivers() -> List[str]:
    return sorted(_REGISTRY)


def make_driver(name: str, config: dict) -> StorageDriver:
    """Instantiate and init the named driver with ``config``."""
    cls = get_driver(name)
    inst = cls()
    inst.init(config)
    return inst
