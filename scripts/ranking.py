"""Ranking algorithm for model-tracker.

Implements the locked algorithm from the build prompt (§6). Exposes weights as
config. `compute_and_apply_ratings` writes agent_rating back to each
user_notes row via the driver; `build_report` renders the plain-text (or
Markdown) ranking table.

Algorithm (per model M, grouped by model_info.model_name, falling back to
model_alias when name is empty):

    eligible_notes = user_notes joined to M
    base    = avg(user_rating over eligible_notes)   # null if no notes -> unranked
    usage   = sum(nturns over all system_config linked to M via model_info)
    score   = base * (1 + log10(1 + usage) * USAGE_WEIGHT)
    if any linked system_config.was_complete = false: score -= PENALTY_INCOMPLETE
    if any linked system_config.was_errors is non-empty: score -= PENALTY_ERRORS
    agent_rating = clamp(score, 1.0, 10.0)

Tie-breaks: equal agent_rating -> higher total nturns first; still equal ->
most recent model_last_use.
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from storage import TABLES  # noqa: E402


# --- Config constants (overridable in config.toml [ranking]) -------------
USAGE_WEIGHT = 1.0
PENALTY_INCOMPLETE = 2.0
PENALTY_ERRORS = 1.0
EXCLUDE_INCOMPLETE = True


def load_ranking_config(cfg: dict) -> dict:
    """Read ranking overrides from config; fall back to module defaults."""
    r = dict(cfg.get("ranking", {}))
    return {
        "USAGE_WEIGHT": r.get("USAGE_WEIGHT", USAGE_WEIGHT),
        "PENALTY_INCOMPLETE": r.get("PENALTY_INCOMPLETE", PENALTY_INCOMPLETE),
        "PENALTY_ERRORS": r.get("PENALTY_ERRORS", PENALTY_ERRORS),
        "EXCLUDE_INCOMPLETE": r.get("EXCLUDE_INCOMPLETE", EXCLUDE_INCOMPLETE),
    }


def _clamp(x: float, lo: float = 1.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, x))


def compute_and_apply_ratings(driver, rcfg: dict) -> List[dict]:
    """Compute agent_rating for every model and write it back to user_notes.

    Returns a list of per-model result dicts (one per model_info row).
    """
    models = driver.query("model_info")
    notes = driver.query("user_notes")
    configs = driver.query("system_config")

    # index configs by id
    cfg_by_id = {c["id"]: c for c in configs}

    results: List[dict] = []
    for m in models:
        mid = m["id"]
        # notes linked to this model
        linked_notes = [n for n in notes if n.get("model_info_id") == mid]
        # configs linked to this model
        sc = cfg_by_id.get(m.get("system_config_id"))
        linked_cfgs = [sc] if sc else []

        if rcfg["EXCLUDE_INCOMPLETE"]:
            base_notes = [n for n in linked_notes
                          if not (sc and sc.get("was_complete") is False)]
        else:
            base_notes = linked_notes

        if not base_notes:
            # No eligible notes -> unranked (None). Still record the row.
            base = None
        else:
            ratings = [n["user_rating"] for n in base_notes
                       if n.get("user_rating") is not None]
            base = (sum(ratings) / len(ratings)) if ratings else None

        usage = sum((c.get("nturns") or 0) for c in linked_cfgs)

        if base is None:
            agent_rating = None
            score = None
        else:
            score = base * (1 + math.log10(1 + usage) * rcfg["USAGE_WEIGHT"])
            flags_incomplete = any(
                c.get("was_complete") is False for c in linked_cfgs
            )
            flags_errors = any(
                (c.get("was_errors") or "").strip() != "" for c in linked_cfgs
            )
            if flags_incomplete:
                score -= rcfg["PENALTY_INCOMPLETE"]
            if flags_errors:
                score -= rcfg["PENALTY_ERRORS"]
            agent_rating = _clamp(score)

        # Write agent_rating back to each linked user_notes row.
        if agent_rating is not None:
            for n in linked_notes:
                driver.update("user_notes", n["id"], {"agent_rating": agent_rating})

        label = m.get("model_name") or m.get("model_alias") or "<unnamed>"
        results.append({
            "model_id": mid,
            "label": label,
            "agent_rating": agent_rating,
            "avg_user_rating": base,
            "total_turns": usage,
            "sessions": len(linked_cfgs),
            "has_incomplete": any(c.get("was_complete") is False for c in linked_cfgs),
            "has_errors": any((c.get("was_errors") or "").strip() != "" for c in linked_cfgs),
            "last_use": m.get("model_last_use"),
        })

    # Sort by agent_rating desc, then total_turns desc, then last_use desc.
    def sort_key(r):
        # Unranked (None) sorts last.
        ar = r["agent_rating"] if r["agent_rating"] is not None else -1.0
        lu = r["last_use"] or 0
        return (ar, r["total_turns"], lu)

    results.sort(key=sort_key, reverse=True)
    # assign rank numbers (only ranked get a rank)
    rank = 1
    for r in results:
        if r["agent_rating"] is None:
            r["rank"] = ""
        else:
            r["rank"] = rank
            rank += 1
    return results


def build_report(results: List[dict], rcfg: dict, markdown: bool = False) -> str:
    """Render the ranking report as plain text or Markdown."""
    header = ["Rank", "Model", "Agent", "AvgUser", "Turns", "Sess", "Inc", "Err", "LastUse"]
    rows = []
    for r in results:
        rows.append([
            str(r["rank"]) if r["rank"] != "" else "-",
            r["label"],
            f"{r['agent_rating']:.2f}" if r["agent_rating"] is not None else "unranked",
            f"{r['avg_user_rating']:.2f}" if r["avg_user_rating"] is not None else "-",
            str(r["total_turns"]),
            str(r["sessions"]),
            "Y" if r["has_incomplete"] else "",
            "Y" if r["has_errors"] else "",
            str(r["last_use"]) if r["last_use"] else "-",
        ])

    if markdown:
        lines = ["| " + " | ".join(header) + " |",
                 "|" + "|".join(["---"] * len(header)) + "|"]
        for row in rows:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    # plain text: fixed-width
    widths = [len(h) for h in header]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    def fmt(row):
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
    lines = [fmt(header), fmt(["-" * w for w in widths])]
    lines.extend(fmt(row) for row in rows)
    lines.append("")
    lines.append(
        f"Weights: USAGE_WEIGHT={rcfg['USAGE_WEIGHT']} "
        f"PENALTY_INCOMPLETE={rcfg['PENALTY_INCOMPLETE']} "
        f"PENALTY_ERRORS={rcfg['PENALTY_ERRORS']} "
        f"EXCLUDE_INCOMPLETE={rcfg['EXCLUDE_INCOMPLETE']}"
    )
    return "\n".join(lines)


# Imported by tracker.py via: from ranking import compute_and_apply_ratings, build_report, load_ranking_config
__all__ = ["compute_and_apply_ratings", "build_report", "load_ranking_config",
           "USAGE_WEIGHT", "PENALTY_INCOMPLETE", "PENALTY_ERRORS", "EXCLUDE_INCOMPLETE"]
