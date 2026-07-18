"""Smoke test for the SQLite driver. Run from repo root:
    python3 scripts/storage/sqlite_driver_smoke.py
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage import list_drivers
from storage.sqlite_driver import SqliteDriver


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="mt-sqlite-")
    try:
        db = os.path.join(tmpdir, "mt.sqlite")
        cfg = {"storage": {"sqlite": {"db_path": db}}}
        d = SqliteDriver()
        d.init(cfg)

        assert "sqlite" in list_drivers(), list_drivers()

        # WAL mode active?
        mode = d._conn.execute("PRAGMA journal_mode").fetchone()[0]
        print("journal_mode:", mode)
        assert mode.lower() == "wal", mode
        fk = d._conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1, fk
        print("PRAGMA foreign_keys=ON confirmed")

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
            "model_context_size": 200000, "model_hosted": True, "model_free": False,
            "model_added": 1690000000, "model_last_use": 1700000001,
            "created_at": 1700000002,
        })
        un = d.insert("user_notes", {
            "model_info_id": mi, "user_notes": "great", "user_rating": 9,
            "created_at": 1700000003,
        })
        print("inserted ids:", si[:8], sc[:8], mi[:8], un[:8])

        # Boolean round-trip
        sc_row = d.query("system_config", {"run_id": "run-1"})[0]
        assert sc_row["was_complete"] is True, sc_row
        print("bool round-trip OK")

        # Update + unknown column rejection
        d.update("user_notes", un, {"user_rating": 10})
        assert d.query("user_notes", {"id": un})[0]["user_rating"] == 10
        try:
            d.update("user_notes", un, {"nope": 1})
            raise SystemExit("unknown-column check FAILED")
        except KeyError:
            print("unknown-column rejection OK")

        # Re-open durable
        d.close()
        d2 = SqliteDriver()
        d2.init(cfg)
        assert len(d2.query("user_notes")) == 1
        for tid in (si, sc, mi, un):
            assert tid[14] == "7", tid
        print("re-open durability + UUIDv7 OK")

        # FK enforcement: bad FK must be rejected
        try:
            d2.insert("model_info", {"system_config_id": "bogus", "model_alias": "x"})
            raise SystemExit("FK enforcement FAILED: bad insert accepted")
        except Exception as e:
            print("FK rejection OK:", type(e).__name__)

        print("\nSQLITE SMOKE TEST: PASS")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
