#!/usr/bin/env python3
"""model-tracker CLI — all skill operations.

Every storage operation goes through the driver registry (storage.get_driver /
make_driver). No operation here imports a concrete driver module.

Subcommands:
  record-session  end-of-session capture (system -> config -> models -> notes)
  add-note        add a user_notes row for an existing model, outside any session
  edit-note       edit user_notes/user_rating by note id (never agent_rating)
  edit            partial update to any row in any table by table+id
  list            list rows of a table with optional equality filters
  show            show one row by id, resolving FK ids to readable summaries
  rank            compute ranking report and refresh stored agent_rating

Global option:
  --config PATH   TOML config (default ~/.model-tracker/config.toml)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

# Make the package importable regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from storage import (  # noqa: E402
    make_driver,
    load_config,
    TABLES,
    uuid7,
)

EPOCH = int


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def parse_bool(s: str) -> bool:
    if s is None:
        return False
    s = str(s).strip().lower()
    if s in ("y", "yes", "true", "1", "t"):
        return True
    if s in ("n", "no", "false", "0", "f", ""):
        return False
    raise ValueError(f"Cannot parse boolean from '{s}' (use y/n/true/false/1/0).")


def parse_rating(s) -> int:
    try:
        v = int(s)
    except (TypeError, ValueError):
        raise ValueError(f"user_rating must be an integer, got '{s}'.")
    if not (1 <= v <= 10):
        raise ValueError(f"user_rating must be 1-10, got {v}.")
    return v


def parse_epoch(s) -> int:
    if s is None or s == "":
        return None
    try:
        v = int(s)
    except (TypeError, ValueError):
        raise ValueError(f"epoch must be an integer (Unix seconds), got '{s}'.")
    return v


def validate_column(table: str, col: str) -> None:
    if col not in TABLES[table]:
        raise KeyError(
            f"Unknown column '{col}' for table '{table}'. "
            f"Valid: {TABLES[table]}"
        )


def coerce_value(table: str, col: str, value) -> object:
    """Coerce a CLI/JSON value to the column's logical type for storage."""
    if col == "user_rating":
        return parse_rating(value)
    if col in ("was_complete", "model_hosted", "model_free"):
        return parse_bool(value)
    if col in (
        "created_at", "nturns", "ctx_length", "num_compressed",
        "model_context_size", "model_added", "model_removed", "model_last_use",
    ):
        return parse_epoch(value)
    if col == "agent_rating":
        # Only `rank` may write this; reject elsewhere.
        raise ValueError("agent_rating is derived; only `rank` may write it.")
    return value


# ---------------------------------------------------------------------------
# Interactive prompt helpers
# ---------------------------------------------------------------------------

def prompt(label: str, default=None, validator=lambda x: x, required=False):
    """Prompt for a value. If stdin is not a tty, error on missing required."""
    if sys.stdin.isatty():
        dflt = f" [{default}]" if default is not None else ""
        raw = input(f"{label}{dflt}: ").strip()
        if raw == "" and default is not None:
            raw = default
    else:
        raw = default
    if raw is None:
        if required:
            raise SystemExit(f"Missing required value for: {label}")
        return None
    return validator(raw)


# ---------------------------------------------------------------------------
# FK resolution for `show`
# ---------------------------------------------------------------------------

_FK_TARGET = {
    "system_config.system_info_id": ("system_info", "id"),
    "model_info.system_config_id": ("system_config", "id"),
    "user_notes.model_info_id": ("model_info", "id"),
}


def summarize_fk(driver, table: str, row: dict) -> dict:
    """Augment a row with human-readable FK summaries."""
    out = dict(row)
    for col, (tgt, _) in _FK_TARGET.items():
        tname, cname = col.split(".")
        if tname != table or cname not in row or row[cname] is None:
            continue
        ref = driver.query(tgt, {"id": row[cname]})
        if not ref:
            out[cname + "_summary"] = f"<missing {tgt}>"
            continue
        r = ref[0]
        if tgt == "system_info":
            out[cname + "_summary"] = r.get("os_make_version")
        elif tgt == "system_config":
            out[cname + "_summary"] = r.get("run_name") or r.get("run_id")
        elif tgt == "model_info":
            out[cname + "_summary"] = r.get("model_name") or r.get("model_alias")
    return out


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def cmd_record_session(args, driver):
    cfg = load_config(args.config)
    # Optional batched input from JSON file.
    if args.from_json:
        with open(args.from_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = None

    # --- system_info (reuse or create) ---
    if data and "system_info" in data:
        si_id = data["system_info"].get("id")
        if not si_id:
            si_id = _make_system_info(driver, data["system_info"])
    else:
        recent = driver.query("system_info", order_by="created_at")
        if recent and sys.stdin.isatty():
            print(f"Most recent system_info: {recent[-1].get('os_make_version')} "
                  f"(id {recent[-1]['id'][:8]})")
            reuse = prompt("Reuse most recent system_info? (y/n)", "n", parse_bool)
            if reuse:
                si_id = recent[-1]["id"]
            else:
                si_id = _make_system_info(driver, None)
        else:
            si_id = _make_system_info(driver, data.get("system_info") if data else None)

    # --- system_config ---
    sc_fields = _collect("system_config", (data.get("system_config") if data else None), {
        "system_info_id": lambda: si_id,
        "run_id": lambda: prompt("run_id", required=True),
        "run_name": lambda: prompt("run_name", ""),
        "stats": lambda: prompt("stats (verbatim dump)", ""),
        "nturns": lambda: prompt("nturns", "0", lambda x: parse_epoch(x) or 0),
        "ctx_length": lambda: prompt("ctx_length", "0", lambda x: parse_epoch(x) or 0),
        "num_compressed": lambda: prompt("num_compressed", "0", lambda x: parse_epoch(x) or 0),
        "was_complete": lambda: prompt("was_complete (y/n)", "y", parse_bool),
        "was_errors": lambda: prompt("was_errors", ""),
    })
    sc_id = driver.insert("system_config", sc_fields)
    print(f"  system_config id={sc_id[:8]}")

    # --- models ---
    models = (data.get("models") if data else None)
    if not models:
        models = []
        while True:
            if models and not prompt("Add another model? (y/n)", "n", parse_bool):
                break
            models.append(_collect_model(driver, data, sc_id))
    else:
        for m in models:
            _insert_model(driver, m, sc_id)

    # --- optional notes ---
    notes = (data.get("notes") if data else None)
    if notes:
        for n in notes:
            _insert_note_for_model(driver, n, None)
    elif sys.stdin.isatty():
        for m in (models or []):
            mid = m["_id"] if "_id" in m else m
            if prompt(f"Add a note for model {mid[:8]}? (y/n)", "n", parse_bool):
                _add_note_for_model(driver, mid)
    print("record-session complete.")


def _make_system_info(driver, preset):
    fields = _collect("system_info", preset, {
        "os_make_version": lambda: prompt("os_make_version", required=True),
        "agent_make_version": lambda: prompt("agent_make_version", required=True),
        "hardware_details": lambda: prompt("hardware_details", ""),
        "created_at": lambda: int(time.time()),
    }, preset=preset)
    sid = driver.insert("system_info", fields)
    print(f"  system_info id={sid[:8]}")
    return sid


def _collect_model(driver, data, sc_id):
    m = _collect("model_info", (data.get("model") if data else None), {
        "system_config_id": lambda: sc_id,
        "model_alias": lambda: prompt("model_alias", ""),
        "model_name": lambda: prompt("model_name", required=True),
        "model_source": lambda: prompt("model_source", ""),
        "model_context_size": lambda: prompt("model_context_size", "", lambda x: parse_epoch(x)),
        "model_hosted": lambda: prompt("model_hosted (y/n)", "n", parse_bool),
        "model_hosted_location": lambda: prompt("model_hosted_location", ""),
        "model_free": lambda: prompt("model_free (y/n)", "n", parse_bool),
        "model_added": lambda: prompt("model_added (epoch, blank=now)", int(time.time()), parse_epoch),
        "model_removed": lambda: prompt("model_removed (epoch, blank=none)", None, parse_epoch),
        "model_last_use": lambda: prompt("model_last_use (epoch, blank=now)", int(time.time()), parse_epoch),
        "created_at": lambda: int(time.time()),
    })
    mid = driver.insert("model_info", m)
    print(f"  model_info id={mid[:8]} ({m.get('model_name')})")
    m = dict(m)
    m["_id"] = mid
    return m


def _insert_model(driver, m, sc_id):
    mm = dict(m)
    mm["system_config_id"] = sc_id
    mm.setdefault("created_at", int(time.time()))
    mm.setdefault("model_last_use", int(time.time()))
    mid = driver.insert("model_info", mm)
    print(f"  model_info id={mid[:8]} ({mm.get('model_name')})")
    return mid


def _insert_note_for_model(driver, n, model_id):
    mid = model_id or n.get("model_info_id")
    if not mid and "model_alias" in n:
        found = driver.query("model_info", {"model_alias": n["model_alias"]})
        if not found:
            raise SystemExit(f"No model with alias '{n['model_alias']}'.")
        mid = found[0]["id"]
    nid = driver.insert("user_notes", {
        "model_info_id": mid,
        "user_notes": n.get("user_notes", ""),
        "user_rating": parse_rating(n.get("user_rating", 0)) if n.get("user_rating") else None,
        "agent_rating": None,
        "created_at": int(time.time()),
    })
    print(f"  user_notes id={nid[:8]}")


def _add_note_for_model(driver, model_id):
    note = prompt("note text", "")
    rating = prompt("user_rating (1-10)", required=True, validator=parse_rating)
    driver.insert("user_notes", {
        "model_info_id": model_id,
        "user_notes": note,
        "user_rating": rating,
        "agent_rating": None,
        "created_at": int(time.time()),
    })


def cmd_add_note(args, driver):
    model_id = args.model_id
    if not model_id:
        # Look up by alias/name interactively.
        key = prompt("model alias or name", required=True)
        found = driver.query("model_info", {"model_alias": key}) or \
                driver.query("model_info", {"model_name": key})
        if not found:
            raise SystemExit(f"No model matching '{key}'.")
        model_id = found[0]["id"]
        print(f"Resolved model id={model_id[:8]}")
    note = args.note if args.note is not None else prompt("note text", "")
    rating = args.rating if args.rating is not None else prompt(
        "user_rating (1-10)", required=True, validator=parse_rating)
    nid = driver.insert("user_notes", {
        "model_info_id": model_id,
        "user_notes": note,
        "user_rating": parse_rating(rating),
        "agent_rating": None,
        "created_at": int(time.time()),
    })
    print(f"add-note: user_notes id={nid[:8]}")


def cmd_edit_note(args, driver):
    changes = {}
    if args.note is not None:
        changes["user_notes"] = args.note
    if args.rating is not None:
        changes["user_rating"] = parse_rating(args.rating)
    if "agent_rating" in changes:
        raise ValueError("agent_rating is derived; edit-note cannot modify it.")
    if not changes:
        raise SystemExit("edit-note requires --note and/or --rating.")
    driver.update("user_notes", args.note_id, changes)
    print(f"edit-note: updated {args.note_id[:8]}")


def cmd_edit(args, driver):
    table = args.table
    row_id = args.id
    changes = {}
    for kv in args.set:
        if "=" not in kv:
            raise SystemExit(f"--set expects key=value, got '{kv}'.")
        col, _, val = kv.partition("=")
        validate_column(table, col)
        if col == "agent_rating":
            raise ValueError("agent_rating is derived; only `rank` may write it.")
        changes[col] = coerce_value(table, col, val)
    if not changes:
        raise SystemExit("edit requires at least one --set key=value.")
    driver.update(table, row_id, changes)
    print(f"edit: updated {table} {row_id[:8]} -> {list(changes)}")


def cmd_list(args, driver):
    filters = {}
    for kv in args.filter or []:
        if "=" not in kv:
            raise SystemExit(f"--filter expects key=value, got '{kv}'.")
        col, _, val = kv.partition("=")
        validate_column(args.table, col)
        filters[col] = val
    rows = driver.query(args.table, filters or None, args.order_by)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        if not rows:
            print(f"(no rows in {args.table})")
            return
        cols = TABLES[args.table]
        width = max((len(c) for c in cols), default=4)
        print(" | ".join(c.ljust(width) for c in cols))
        print("-+-".join("-" * width for c in cols))
        for r in rows:
            print(" | ".join(str(r.get(c, "")).ljust(width) for c in cols))


def cmd_show(args, driver):
    rows = driver.query(args.table, {"id": args.id})
    if not rows:
        raise SystemExit(f"No row id={args.id} in '{args.table}'.")
    row = summarize_fk(driver, args.table, rows[0])
    if args.json:
        print(json.dumps(row, indent=2, default=str))
        return
    for k, v in row.items():
        print(f"{k}: {v}")


def cmd_rank(args, driver):
    # Lazy import keeps ranking.py the single source of the algorithm and
    # avoids importing it when not needed.
    from ranking import compute_and_apply_ratings, build_report, load_ranking_config

    rcfg = load_ranking_config(load_config(args.config))
    rows = compute_and_apply_ratings(driver, rcfg)
    report = build_report(rows, rcfg, markdown=args.markdown)
    print(report)


# ---------------------------------------------------------------------------
# Generic collection helper for record-session
# ---------------------------------------------------------------------------

def _collect(table, data, spec, preset=None):
    """Build a row dict from spec. If data/preset supplies a key, use it
    (coerced); else call the prompt lambda."""
    out = {}
    src = data or preset or {}
    for col, fn in spec.items():
        if col in src and src[col] not in (None, ""):
            out[col] = coerce_value(table, col, src[col])
        else:
            out[col] = fn()
    return out


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tracker.py", description="model-tracker operations")
    p.add_argument("--config", default=None, help="TOML config path")
    sub = p.add_subparsers(dest="command", required=True)

    rs = sub.add_parser("record-session", help="capture end-of-session stats")
    rs.add_argument("--from-json", default=None, help="batched input JSON file")

    an = sub.add_parser("add-note", help="add a note for an existing model")
    an.add_argument("--model-id", default=None)
    an.add_argument("--note", default=None)
    an.add_argument("--rating", type=int, default=None)

    en = sub.add_parser("edit-note", help="edit user_notes/user_rating by note id")
    en.add_argument("note_id")
    en.add_argument("--note", default=None)
    en.add_argument("--rating", type=int, default=None)

    ed = sub.add_parser("edit", help="partial update to any table row by id")
    ed.add_argument("table")
    ed.add_argument("id")
    ed.add_argument("--set", action="append", default=[], dest="set",
                    help="key=value (repeatable)")

    ls = sub.add_parser("list", help="list table rows with optional filters")
    ls.add_argument("table")
    ls.add_argument("--filter", action="append", default=[], help="key=value")
    ls.add_argument("--order-by", default=None)
    ls.add_argument("--json", action="store_true")

    sh = sub.add_parser("show", help="show one row by id")
    sh.add_argument("table")
    sh.add_argument("id")
    sh.add_argument("--json", action="store_true")

    rk = sub.add_parser("rank", help="produce ranking report")
    rk.add_argument("--markdown", action="store_true")

    return p


COMMANDS = {
    "record-session": cmd_record_session,
    "add-note": cmd_add_note,
    "edit-note": cmd_edit_note,
    "edit": cmd_edit,
    "list": cmd_list,
    "show": cmd_show,
    "rank": cmd_rank,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    backend = cfg["storage"]["backend"]
    driver = make_driver(backend, cfg)
    try:
        COMMANDS[args.command](args, driver)
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
