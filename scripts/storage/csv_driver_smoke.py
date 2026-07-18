"""Smoke test for the CSV driver. Run from repo root:
    python3 scripts/storage/csv_driver.py
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage import TABLES
from storage.csv_driver import CsvDriver


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="mt-csv-")
    try:
        cfg = {"storage": {"csv": {"data_dir": tmpdir}}}
        d = CsvDriver()
        d.init(cfg)

        # Insert one row in each table, respecting FK order.
        si = d.insert("system_info", {
            "os_make_version": "Linux 6.1", "agent_make_version": "Hermes 1.0",
            "hardware_details": "16c/32G", "created_at": 1700000000,
        })
        sc = d.insert("system_config", {
            "system_info_id": si, "run_id": "run-1", "run_name": "first",
            "stats": "verbatim", "nturns": 12, "ctx_length": 128000,
            "num_compressed": 1, "was_complete": True, "was_errors": "",
            "created_at": 1700000001,
        })
        mi = d.insert("model_info", {
            "system_config_id": sc, "model_alias": "opus", "model_name": "claude-opus",
            "model_source": "anthropic", "model_context_size": 200000,
            "model_hosted": True, "model_hosted_location": "us", "model_free": False,
            "model_added": 1690000000, "model_removed": None, "model_last_use": 1700000001,
            "created_at": 1700000002,
        })
        un = d.insert("user_notes", {
            "model_info_id": mi, "user_notes": "great", "user_rating": 9,
            "agent_rating": None, "created_at": 1700000003,
        })

        print("inserted ids:", si[:8], sc[:8], mi[:8], un[:8])

        # Query back
        rows = d.query("system_info")
        assert len(rows) == 1 and rows[0]["id"] == si, rows
        # boolean parse round-trips
        sc_row = d.query("system_config", {"run_id": "run-1"})[0]
        assert sc_row["was_complete"] is True, sc_row
        print("query round-trip OK; was_complete parsed:", sc_row["was_complete"])

        # Update
        d.update("user_notes", un, {"user_rating": 10, "user_notes": "excellent"})
        upd = d.query("user_notes", {"id": un})[0]
        assert upd["user_rating"] == 10 and upd["user_notes"] == "excellent", upd
        print("update OK")

        # Re-open: new driver instance, same dir -> durable
        d.close()
        d2 = CsvDriver()
        d2.init(cfg)
        assert len(d2.query("system_info")) == 1
        assert len(d2.query("user_notes")) == 1
        print("re-open durability OK; version-nibble check:")
        for tid in (si, sc, mi, un):
            assert tid[14] == "7", tid
        print("  all ids are UUID v7 (nibble 7): OK")

        # FK advisory check: bad FK rejects
        try:
            d2.insert("model_info", {"system_config_id": "bogus-id", "model_alias": "x"})
            raise SystemExit("FK check FAILED: bad insert accepted")
        except ValueError as e:
            print("FK advisory rejection OK:", str(e)[:50])

        # Unknown column in update rejected
        try:
            d2.update("user_notes", un, {"nonexistent": 1})
            raise SystemExit("unknown-column check FAILED")
        except KeyError as e:
            print("unknown-column rejection OK:", str(e)[:50])

        print("\nCSV SMOKE TEST: PASS")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
