"""Setup wizard — walks users through initial configuration.

Generates ~/.model-tracker/config.toml with sensible defaults based on
interactive prompts. This is a one-time flow after skill install.

Usage:
    python3 scripts/tracker.py --setup
"""

from __future__ import annotations

import os
import sys
import tomllib
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detect import detect, detect_os, detect_agent, detect_hardware  # noqa: E402
from storage import load_config, load_config_raw  # noqa: E402

DEFAULT_CONFIG_DIR = os.path.expanduser("~/.model-tracker")
DEFAULT_DATA_DIR = os.path.join(DEFAULT_CONFIG_DIR, "data")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.toml")


def setup(argv: list[str] | None = None) -> int:
    """Run the setup wizard interactively.

    On first run it creates ~/.model-tracker/config.toml. If the config
    already exists it asks to reconfigure; when reconfiguring, every prompt is
    pre-filled with the current value so you can just press Enter to keep it.
    """
    existing: Dict[str, Any] = {}
    if os.path.isfile(DEFAULT_CONFIG_PATH):
        print(f"Config already exists at {DEFAULT_CONFIG_PATH}")
        if input("Reconfigure? (y/n): ").strip().lower().startswith("y"):
            print("Overwriting existing config...\n")
            # Load current values (RAW — do not resolve $BWS:/$HERMES_SECRET
            # references, or the resolved secret would be written back to disk).
            existing = load_config_raw(DEFAULT_CONFIG_PATH)
        else:
            print("Skipping setup. Your config is untouched.")
            return 0
    else:
        os.makedirs(DEFAULT_CONFIG_DIR, exist_ok=True)

    print("\n=== model-tracker Setup Wizard ===\n")
    print("I'll ask a few questions to set up your configuration.\n")

    # 1. Backend (default to current choice when reconfiguring)
    print("--- 1. Storage Backend ---")
    backends = ["csv", "sqlite", "postgres"]
    cur_backend = existing.get("storage", {}).get("backend")
    default_backend = backends.index(cur_backend) + 1 if cur_backend in backends else 1
    for i, b in enumerate(backends, 1):
        mark = " (current)" if b == cur_backend else ""
        print(f"  {i}. {b}{mark}")
    backend = _prompt_choice("Choose backend", backends, default=default_backend)

    # 2. Backend-specific location / connection
    db_path = ""
    pg_dsn = ""
    if backend == "csv":
        print(f"\n--- 2. CSV Data Directory ---")
        print(f"  model-tracker writes one .csv file per table in this directory.")
        default_data = os.path.join(DEFAULT_DATA_DIR)
        cur = existing.get("storage", {}).get("csv", {}).get("data_dir", "")
        if cur:
            default_data = cur
            print(f"  Current: {cur}")
        db_path = input(f"  Directory [{default_data}]: ").strip()
        db_path = os.path.expanduser(db_path) if db_path else default_data
    elif backend == "sqlite":
        print(f"\n--- 2. SQLite Database File ---")
        print(f"  model-tracker stores all tables in this single file.")
        default_data = os.path.join(DEFAULT_DATA_DIR, "model-tracker.sqlite")
        cur = existing.get("storage", {}).get("sqlite", {}).get("db_path", "")
        if cur:
            default_data = cur
            print(f"  Current: {cur}")
        db_path = input(f"  File path [{default_data}]: ").strip()
        db_path = os.path.expanduser(db_path) if db_path else default_data
    elif backend == "postgres":
        cur_dsn = existing.get("storage", {}).get("postgres", {}).get("dsn", "")
        db_path, pg_dsn = _prompt_postgres(existing_dsn=cur_dsn)

    # 3. Auto-record
    print(f"\n--- 3. Auto-Record at Session Start ---")
    print(f"  Auto-record captures your system info (OS, agent, hardware)")
    print(f"  at the start of every session so you don't have to.")
    ar_cur = existing.get("auto_record", {}).get("enabled", False)
    ar_on = _prompt_bool("Enable auto-record?", default=ar_cur)
    trigger = None
    if ar_on:
        triggers = ["new-session", "re-entry", "both"]
        cur_trigger = existing.get("auto_record", {}).get("trigger", "new-session")
        default_trigger = triggers.index(cur_trigger) + 1 if cur_trigger in triggers else 1
        trigger = _prompt_choice(
            "When to auto-record", triggers, default=default_trigger,
            header="Trigger mode:",
        )

    # 4. Turn-based check-in
    print(f"\n--- 4. Turn-Based Check-In ---")
    print(f"  After N turns, the agent can ask if you want to rate")
    print(f"  your experience with the current model.")
    cur_thr = existing.get("checkin", {}).get("turn_threshold", 0)
    threshold_str = input(f"  Turn threshold (0 to disable) [{cur_thr}]: ").strip()
    try:
        threshold = int(threshold_str) if threshold_str else cur_thr
    except ValueError:
        threshold = cur_thr
    if threshold > 0:
        print(f"  Check-in enabled: every {threshold} turns.")

    # 5. Hardware overrides
    print(f"\n--- 5. Hardware Information ---")
    print(f"  Auto-detection will try to read your OS, CPU, RAM, and GPU.")
    sh = existing.get("static_hardware", {})
    has_static = any(sh.get(k) for k in ("os_make_version", "agent_make_version", "hardware_details"))
    default_static = "y" if has_static else "n"
    if input(f"  Use static (manually set) hardware values? (y/n) [{default_static}]: ").strip().lower() or default_static:
        print(f"\n  Enter your hardware details (or leave blank for auto-detect):")
        os_cur = sh.get("os_make_version", "")
        ag_cur = sh.get("agent_make_version", "")
        hw_cur = sh.get("hardware_details", "")
        if os_cur:
            print(f"    current OS: {os_cur}")
        if ag_cur:
            print(f"    current agent: {ag_cur}")
        if hw_cur:
            print(f"    current hardware: {hw_cur}")
        os_ver = input(f"  OS name/version [{os_cur}]: ").strip() or (os_cur or None)
        agent_ver = input(f"  Agent name/version [{ag_cur}]: ").strip() or (ag_cur or None)
        hw_details = input(f"  Hardware (CPU/RAM/GPU) [{hw_cur}]: ").strip() or (hw_cur or None)
    else:
        # Auto-detect right now to show what we found.
        print(f"\n  Attempting auto-detection...")
        results = detect()
        for field, val in results.items():
            if val:
                print(f"    {field}: {val}")
            else:
                print(f"    {field}: (not detected — will prompt at runtime)")
        print(f"\n  Auto-detection is enabled. You can always override later.")
        os_ver = None
        agent_ver = None
        hw_details = None

    # Write the config file.
    print(f"\n--- Writing config ---")
    config: Dict[str, Any] = {
        "storage": {"backend": backend},
    }
    if backend == "csv":
        config["storage"]["csv"] = {"data_dir": db_path}
    elif backend == "sqlite":
        config["storage"]["sqlite"] = {"db_path": db_path}
    elif backend == "postgres":
        config["storage"]["postgres"] = {"dsn": pg_dsn}

    config["auto_record"] = {"enabled": ar_on}
    if ar_on:
        config["auto_record"]["trigger"] = trigger

    config["checkin"] = {"turn_threshold": threshold}

    if os_ver or agent_ver or hw_details:
        config["static_hardware"] = {}
        if os_ver:
            config["static_hardware"]["os_make_version"] = os_ver
        if agent_ver:
            config["static_hardware"]["agent_make_version"] = agent_ver
        if hw_details:
            config["static_hardware"]["hardware_details"] = hw_details

    # Write TOML manually (no dep on tomllib for setup script).
    toml_text = _toml_dump(config)
    with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(toml_text)
        f.flush()
        os.fsync(f.fileno())

    print(f"\n✓ Config written to {DEFAULT_CONFIG_PATH}")
    print("\nYou're all set! Use `tracker.py record-session` to start tracking.")
    return 0


def _prompt_bool(label: str, default: bool = False) -> bool:
    prompt_str = f"{label} (y/n) [{'Y' if default else 'n'}]: "
    resp = input(prompt_str).strip().lower()
    if resp == "":
        return default
    return resp in ("y", "yes", "true", "1", "t")


def _prompt_choice(
    label: str,
    choices: list[str],
    default: int = 1,
    header: str = "Choose:",
) -> str:
    print(f"\n{header}")
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    resp = input(f"\n{label} [{default}]: ").strip()
    try:
        idx = int(resp) if resp else default
    except ValueError:
        idx = default
    idx = max(1, min(len(choices), idx))
    return choices[idx - 1]


def _prompt_postgres(existing_dsn: str = "") -> "tuple[str, str]":
    """Interactively build a PostgreSQL DSN and choose how to store it.

    Returns (db_path_unused, pg_dsn). ``pg_dsn`` is either the full DSN
    (plaintext), an empty string (driver falls back to MODEL_TRACKER_PG_DSN),
    or a ``$BWS:<uuid>`` reference.

    When ``existing_dsn`` is provided (reconfigure), the user may keep it
    instead of re-entering everything.
    """
    import getpass

    print(f"\n--- 2. PostgreSQL Connection ---")
    if existing_dsn:
        masked = existing_dsn
        if masked.startswith("$BWS:"):
            masked = f"{existing_dsn} (Bitwarden reference)"
        elif ":" in masked and "@" in masked:
            # mask the password portion for display
            try:
                head, tail = masked.split("://", 1)
                userpart, rest = tail.split("@", 1)
                masked = f"{head}://{userpart.split(':')[0]}:***@{rest}"
            except ValueError:
                pass
        keep = input(f"  Current DSN: {masked}\n  Keep current? (y/n) [y]: ").strip().lower()
        if keep != "n":
            print("  Keeping existing DSN.")
            return "", existing_dsn

    print(f"  I'll build the connection string (DSN) from a few questions.")
    print(f"  Format: postgresql://USER:PASSWORD@HOST:PORT/DATABASE\n")

    host = input("  Host [localhost]: ").strip() or "localhost"
    port = input("  Port [5432]: ").strip() or "5432"
    dbname = input("  Database name [modeltracker]: ").strip() or "modeltracker"
    user = input("  Username [modeltracker]: ").strip() or "modeltracker"
    pw = getpass.getpass("  Password (hidden, not echoed): ")

    dsn = f"postgresql://{user}:{pw}@{host}:{port}/{dbname}"
    print(f"\n  Built DSN: postgresql://{user}:***@{host}:{port}/{dbname}")

    print(f"  How should the DSN (with password) be stored?")
    print(f"    1. Env var MODEL_TRACKER_PG_DSN (recommended - password out of config)")
    print(f"    2. Directly in config.toml (simplest - plaintext password)")
    print(f"    3. Bitwarden Secrets Manager (reference by UUID)")
    choice = input("  Choice [1]: ").strip() or "1"

    if choice == "2":
        print(f"  Note: the password will be saved in plaintext in config.toml.")
        return "", dsn
    if choice == "3":
        uuid_str = input("  Bitwarden secret UUID: ").strip()
        if not uuid_str:
            print(f"  No UUID given - falling back to env var method.")
            choice = "1"
        else:
            return "", f"$BWS:{uuid_str}"

    # Default: env var method. Leave dsn empty; driver reads MODEL_TRACKER_PG_DSN.
    print(f"\n  Add this to your shell profile (~/.bashrc or ~/.profile), then")
    print(f"  restart your shell (or run the line now):")
    print(f'    export MODEL_TRACKER_PG_DSN="{dsn}"')
    return "", ""


def _toml_dump(cfg: Dict[str, Any]) -> str:
    """Minimal TOML serializer for the config structure we produce."""
    lines: list[str] = []
    lines.append("# model-tracker configuration (auto-generated by setup wizard)")
    lines.append("# Edit as needed, or re-run: tracker.py --setup")
    lines.append("")

    def fmt_value(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return f'"{v}"'
        if isinstance(v, (int, float)):
            return str(v)
        return repr(v)

    def write_section(path: str, section: Dict[str, Any]) -> None:
        """Write a section header, its flat key=value pairs, then recurse into sub-sections."""
        lines.append(f"[{path}]")
        # Separate flat values from sub-sections
        flat = []
        sub_sections = []
        for key, val in section.items():
            if isinstance(val, dict):
                sub_sections.append((key, val))
            else:
                flat.append((key, val))
        for k, v in flat:
            lines.append(f"{k} = {fmt_value(v)}")
        lines.append("")
        # Now write nested sub-sections
        for key, val in sub_sections:
            write_section(f"{path}.{key}", val)
        lines.append("")

    for top_key, top_val in cfg.items():
        if isinstance(top_val, dict):
            write_section(top_key, top_val)
        else:
            lines.append(f"{top_key} = {fmt_value(top_val)}")
            lines.append("")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(setup())
