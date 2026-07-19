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

DEFAULT_CONFIG_DIR = os.path.expanduser("~/.model-tracker")
DEFAULT_DATA_DIR = os.path.join(DEFAULT_CONFIG_DIR, "data")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.toml")


def setup(argv: list[str] | None = None) -> int:
    """Run the setup wizard interactively."""
    # Check if config already exists — offer to skip or reconfigure.
    if os.path.isfile(DEFAULT_CONFIG_PATH):
        print(f"Config already exists at {DEFAULT_CONFIG_PATH}")
        if input("Reconfigure? (y/n): ").strip().lower().startswith("y"):
            print("Overwriting existing config...")
        else:
            print("Skipping setup. Your config is untouched.")
            return 0
    else:
        os.makedirs(DEFAULT_CONFIG_DIR, exist_ok=True)

    print("\n=== model-tracker Setup Wizard ===\n")
    print("I'll ask a few questions to set up your configuration.\n")

    # 1. Backend
    print("--- 1. Storage Backend ---")
    backends = ["csv", "sqlite", "postgres"]
    for i, b in enumerate(backends, 1):
        print(f"  {i}. {b}")
    backend = _prompt_choice(
        "Choose backend", backends, default=1,
    )

    # 2. Data path
    if backend == "csv":
        default_data = os.path.join(DEFAULT_DATA_DIR)
    elif backend == "sqlite":
        default_data = os.path.join(DEFAULT_DATA_DIR, "model-tracker.sqlite")
    else:
        default_data = ""

    print(f"\n--- 2. Data Path ---")
    print(f"  Where should data files / database be stored?")
    db_path = input(f"  Path [{default_data}]: ").strip()
    if not db_path:
        db_path = default_data
    db_path = os.path.expanduser(db_path)

    # 3. PostgreSQL DSN (if applicable)
    pg_dsn = ""
    if backend == "postgres":
        print(f"\n--- 3. PostgreSQL Connection ---")
        pg_dsn = input(
            "  PostgreSQL DSN "
            f"(or env var MODEL_TRACKER_PG_DSN) [{db_path}]: "
        ).strip()
        if not pg_dsn:
            pg_dsn = db_path  # user might have put dsn as path
        if pg_dsn:
            print(f"  Using DSN: {pg_dsn[:20]}...")

    # 4. Auto-record
    print(f"\n--- 4. Auto-Record at Session Start ---")
    print(f"  Auto-record captures your system info (OS, agent, hardware)")
    print(f"  at the start of every session so you don't have to.")
    ar_on = _prompt_bool("Enable auto-record?", default=False)
    trigger = None
    if ar_on:
        triggers = ["new-session", "re-entry", "both"]
        trigger = _prompt_choice(
            "When to auto-record", triggers, default=1,
            header="Trigger mode:",
        )

    # 5. Turn-based check-in
    print(f"\n--- 5. Turn-Based Check-In ---")
    print(f"  After N turns, the agent can ask if you want to rate")
    print(f"  your experience with the current model.")
    threshold_str = input("  Turn threshold (0 to disable) [0]: ").strip()
    try:
        threshold = int(threshold_str) if threshold_str else 0
    except ValueError:
        threshold = 0
    if threshold > 0:
        print(f"  Check-in enabled: every {threshold} turns.")

    # 6. Hardware overrides
    print(f"\n--- 6. Hardware Information ---")
    print(f"  Auto-detection will try to read your OS, CPU, RAM, and GPU.")
    if input("  Use static (manually set) hardware values? (y/n) [n]: ") \
            .strip().lower().startswith("y"):
        print("\n  Enter your hardware details (or leave blank for auto-detect):")
        os_ver = input("  OS name/version: ").strip() or None
        agent_ver = input("  Agent name/version: ").strip() or None
        hw_details = input("  Hardware (CPU/RAM/GPU): ").strip() or None
    else:
        # Auto-detect right now to show what we found.
        print("\n  Attempting auto-detection...")
        results = detect()
        for field, val in results.items():
            if val:
                print(f"    {field}: {val}")
            else:
                print(f"    {field}: (not detected — will prompt at runtime)")
        print("\n  Auto-detection is enabled. You can always override later.")
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
