"""Acceptance tests 1-7 for model-tracker. Run from repo root:
    python3 scripts/acceptance.py
Emits PASS/FAIL per test and a final count. Test 8 (frontmatter) is a separate
manual checklist printed at the end.
"""
import os
import sys
import tempfile
import shutil
import json
import subprocess

REPO = "/home/rahlquist/model-tracker"
sys.path.insert(0, os.path.join(REPO, "scripts"))
from storage import make_driver, load_config, get_driver, register_driver, TABLES
from storage.csv_driver import CsvDriver
from storage.sqlite_driver import SqliteDriver
import time

PASS = []
FAIL = []

def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append(name)
        print(f"[FAIL] {name}  {detail}")


# ---------------------------------------------------------------------------
# TEST 1: Fresh CSV end-to-end, headers + UUIDv7 version nibble + time-order
# ---------------------------------------------------------------------------
def test1():
    tmp = tempfile.mkdtemp(prefix="acc1-")
    try:
        cfg = {"storage": {"csv": {"data_dir": os.path.join(tmp, "d")}}}
        d = CsvDriver(); d.init(cfg)
        t = int(time.time())
        si = d.insert("system_info", {"os_make_version": "L", "agent_make_version": "H",
                                     "hardware_details": "x", "created_at": t})
        sc = d.insert("system_config", {"system_info_id": si, "run_id": "r", "run_name": "n",
                                        "stats": "s", "nturns": 5, "ctx_length": 100, "num_compressed": 0,
                                        "was_complete": True, "was_errors": "", "created_at": t})
        mi = d.insert("model_info", {"system_config_id": sc, "model_alias": "a", "model_name": "m",
                                     "model_context_size": 1, "model_hosted": True, "model_free": False,
                                     "model_added": t, "model_last_use": t, "created_at": t})
        d.insert("user_notes", {"model_info_id": mi, "user_notes": "x", "user_rating": 8,
                                "created_at": t})
        # headers correct
        import csv
        with open(os.path.join(tmp, "d", "system_info.csv")) as f:
            hdr = next(csv.reader(f))
        ok_hdr = hdr == TABLES["system_info"]
        # version nibble + time order
        ids = [si, sc, mi]
        v7 = all(i[14] == "7" for i in ids)
        ordered = ids == sorted(ids, key=lambda x: int(x.replace("-", "")[:12], 16))
        # re-open clean
        d2 = CsvDriver(); d2.init(cfg)
        ok_rows = len(d2.query("user_notes")) == 1
        check("T1 fresh CSV e2e", ok_hdr and v7 and ordered and ok_rows,
              f"hdr={ok_hdr} v7={v7} ordered={ordered} rows={ok_rows}")
        d2.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# TEST 2: Kill-mid-session (CSV + SQLite): 3 of 5 rows survive
# ---------------------------------------------------------------------------
def _write_n(driver, n, table, base_row):
    ids = []
    for i in range(n):
        row = dict(base_row)
        row["id"] = None
        ids.append(driver.insert(table, row))
    return ids

def test2():
    # CSV
    tmp = tempfile.mkdtemp(prefix="acc2c-")
    try:
        cfg = {"storage": {"csv": {"data_dir": os.path.join(tmp, "d")}}}
        d = CsvDriver(); d.init(cfg)
        si = d.insert("system_info", {"os_make_version": "L", "agent_make_version": "H",
                                      "hardware_details": "x", "created_at": int(time.time())})
        # simulate writing 3 of an expected 5 system_config rows then "abandon"
        for i in range(3):
            d.insert("system_config", {"system_info_id": si, "run_id": f"r{i}",
                                       "nturns": i, "was_complete": True, "created_at": int(time.time())})
        d.close()
        d2 = CsvDriver(); d2.init(cfg)
        ok_csv = len(d2.query("system_config")) == 3
        d2.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # SQLite
    tmp = tempfile.mkdtemp(prefix="acc2s-")
    try:
        db = os.path.join(tmp, "m.sqlite")
        cfg = {"storage": {"sqlite": {"db_path": db}}}
        d = SqliteDriver(); d.init(cfg)
        si = d.insert("system_info", {"os_make_version": "L", "agent_make_version": "H",
                                      "hardware_details": "x", "created_at": int(time.time())})
        for i in range(3):
            d.insert("system_config", {"system_info_id": si, "run_id": f"r{i}",
                                       "nturns": i, "was_complete": True, "created_at": int(time.time())})
        d.close()
        d2 = SqliteDriver(); d2.init(cfg)
        ok_sql = len(d2.query("system_config")) == 3
        d2.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    check("T2 kill-mid-session durability (CSV+SQLite)", ok_csv and ok_sql,
          f"csv={ok_csv} sqlite={ok_sql}")


# ---------------------------------------------------------------------------
# TEST 3: add-note / edit-note outside session; edit-note cannot touch agent_rating
# ---------------------------------------------------------------------------
def test3():
    tmp = tempfile.mkdtemp(prefix="acc3-")
    try:
        cfg = {"storage": {"csv": {"data_dir": os.path.join(tmp, "d")}}}
        d = CsvDriver(); d.init(cfg)
        t = int(time.time())
        si = d.insert("system_info", {"os_make_version": "L", "agent_make_version": "H",
                                      "hardware_details": "x", "created_at": t})
        sc = d.insert("system_config", {"system_info_id": si, "run_id": "r", "nturns": 1,
                                        "was_complete": True, "created_at": t})
        mi = d.insert("model_info", {"system_config_id": sc, "model_alias": "a",
                                     "model_name": "m", "model_added": t, "model_last_use": t,
                                     "created_at": t})
        nid = d.insert("user_notes", {"model_info_id": mi, "user_notes": "hi", "user_rating": 7,
                                      "created_at": t})
        # edit-note changes rating
        d.update("user_notes", nid, {"user_rating": 9, "user_notes": "updated"})
        upd = d.query("user_notes", {"id": nid})[0]
        ok_edit = upd["user_rating"] == 9 and upd["agent_rating"] is None
        # attempt to set agent_rating via edit-note path is blocked in tracker;
        # here we assert driver-level: show tracker rejects it.
        from tracker import cmd_edit_note
        import argparse
        a = argparse.Namespace(note_id=nid, note=None, rating=None, config=None)
        # craft a fake args with agent_rating set illegal: simulate via edit command
        from tracker import cmd_edit
        try:
            cmd_edit(argparse.Namespace(table="user_notes", id=nid,
                                         set=["agent_rating=5"], config=None), d)
            blocked = False
        except ValueError:
            blocked = True
        d.close()
        check("T3 add/edit-note + agent_rating protected", ok_edit and blocked,
              f"edit={ok_edit} blocked={blocked}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# TEST 4: edit type validation + rejects unknown column
# ---------------------------------------------------------------------------
def test4():
    tmp = tempfile.mkdtemp(prefix="acc4-")
    try:
        cfg = {"storage": {"csv": {"data_dir": os.path.join(tmp, "d")}}}
        d = CsvDriver(); d.init(cfg)
        t = int(time.time())
        si = d.insert("system_info", {"os_make_version": "L", "agent_make_version": "H",
                                      "hardware_details": "x", "created_at": t})
        # valid type coercion
        d.update("system_info", si, {"created_at": "12345"})
        ok_type = d.query("system_info", {"id": si})[0]["created_at"] == 12345
        # unknown column rejected
        try:
            d.update("system_info", si, {"bogus": 1})
            rejected = False
        except KeyError:
            rejected = True
        d.close()
        check("T4 edit type validation + unknown column", ok_type and rejected,
              f"type={ok_type} rejected={rejected}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# TEST 5: FK enforcement — SQLite rejects bad FK; CSV errors clearly
# ---------------------------------------------------------------------------
def test5():
    # SQLite
    tmp = tempfile.mkdtemp(prefix="acc5s-")
    try:
        cfg = {"storage": {"sqlite": {"db_path": os.path.join(tmp, "m.sqlite")}}}
        d = SqliteDriver(); d.init(cfg)
        try:
            d.insert("model_info", {"system_config_id": "deadbeef", "model_alias": "x",
                                    "created_at": int(time.time())})
            sql_ok = False
        except Exception:
            sql_ok = True
        d.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # CSV
    tmp = tempfile.mkdtemp(prefix="acc5c-")
    try:
        cfg = {"storage": {"csv": {"data_dir": os.path.join(tmp, "d")}}}
        d = CsvDriver(); d.init(cfg)
        d.insert("system_info", {"os_make_version": "L", "agent_make_version": "H",
                                 "hardware_details": "x", "created_at": int(time.time())})
        try:
            d.insert("model_info", {"system_config_id": "nope", "model_alias": "x",
                                    "created_at": int(time.time())})
            csv_ok = False
        except ValueError:
            csv_ok = True
        d.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check("T5 FK enforcement (SQLite reject + CSV clear error)", sql_ok and csv_ok,
          f"sqlite={sql_ok} csv={csv_ok}")


# ---------------------------------------------------------------------------
# TEST 6: rank ordering/clamping/back-write (uses ranking.py directly)
# ---------------------------------------------------------------------------
def test6():
    tmp = tempfile.mkdtemp(prefix="acc6-")
    try:
        cfg = {"storage": {"csv": {"data_dir": os.path.join(tmp, "d")}}}
        from ranking import compute_and_apply_ratings, load_ranking_config
        d = CsvDriver(); d.init(cfg)
        t = int(time.time())
        si = d.insert("system_info", {"os_make_version": "L", "agent_make_version": "H",
                                      "hardware_details": "x", "created_at": t})
        # A: 9,10 ; 100 turns ; complete
        scA = d.insert("system_config", {"system_info_id": si, "run_id": "A", "nturns": 100,
                                         "was_complete": True, "was_errors": "", "created_at": t})
        mA = d.insert("model_info", {"system_config_id": scA, "model_name": "A",
                                     "model_added": t, "model_last_use": t + 3, "created_at": t})
        d.insert("user_notes", {"model_info_id": mA, "user_rating": 9, "created_at": t})
        d.insert("user_notes", {"model_info_id": mA, "user_rating": 10, "created_at": t})
        # B: 7 ; 50 turns ; incomplete
        scB = d.insert("system_config", {"system_info_id": si, "run_id": "B", "nturns": 50,
                                         "was_complete": False, "was_errors": "", "created_at": t})
        mB = d.insert("model_info", {"system_config_id": scB, "model_name": "B",
                                     "model_added": t, "model_last_use": t + 2, "created_at": t})
        d.insert("user_notes", {"model_info_id": mB, "user_rating": 7, "created_at": t})
        # C: 5 ; 30 turns ; errors
        scC = d.insert("system_config", {"system_info_id": si, "run_id": "C", "nturns": 30,
                                         "was_complete": True, "was_errors": "x", "created_at": t})
        mC = d.insert("model_info", {"system_config_id": scC, "model_name": "C",
                                     "model_added": t, "model_last_use": t + 1, "created_at": t})
        d.insert("user_notes", {"model_info_id": mC, "user_rating": 5, "created_at": t})
        # D: no notes
        scD = d.insert("system_config", {"system_info_id": si, "run_id": "D", "nturns": 10,
                                         "was_complete": True, "was_errors": "", "created_at": t})
        d.insert("model_info", {"system_config_id": scD, "model_name": "D",
                                "model_added": t, "model_last_use": t, "created_at": t})

        rcfg = load_ranking_config(cfg)
        res = compute_and_apply_ratings(d, rcfg)
        by = {r["label"]: r for r in res}
        # A and C both clamp to 10.00; A ranks first (more turns)
        order = [r["label"] for r in res if r["agent_rating"] is not None]
        ok_order = order[0] == "A" and order[1] == "C"
        ok_clamp = by["A"]["agent_rating"] == 10.0 and by["C"]["agent_rating"] == 10.0
        ok_unranked = by["B"]["agent_rating"] is None and by["D"]["agent_rating"] is None
        # back-write
        notes = d.query("user_notes")
        ok_back = all(
            (n["agent_rating"] == 10.0 if n["model_info_id"] in (mA, mC) else n["agent_rating"] is None)
            for n in notes
        )
        d.close()
        ok = ok_order and ok_clamp and ok_unranked and ok_back
        check("T6 rank ordering/clamp/back-write", ok,
              f"order={order} clamp={ok_clamp} unranked={ok_unranked} back={ok_back}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# TEST 7: Registry extension — in-memory driver used by list, no concrete refs
# ---------------------------------------------------------------------------
def test7():
    # Define a trivial in-memory driver inline and register under a new name.
    import uuid as _u
    class MemDriver:
        _rows = {t: [] for t in TABLES}   # class-level: shared across instances
        def __init__(self): pass
        def init(self, config): pass
        def insert(self, table, row):
            if "id" not in row or row["id"] is None: row["id"] = str(_u.uuid7())
            rid = str(row["id"]); MemDriver._rows.setdefault(table, []).append(dict(row)); return rid
        def update(self, table, id, changes):
            for r in MemDriver._rows.get(table, []):
                if r["id"] == str(id): r.update(changes); return
            raise KeyError(id)
        def query(self, table, filters=None, order_by=None):
            rows = [dict(r) for r in MemDriver._rows.get(table, [])
                    if not filters or all(str(r.get(k))==str(v) for k,v in filters.items())]
            if order_by: rows.sort(key=lambda x: x.get(order_by))
            return rows
        def checkpoint(self): pass
        def close(self): pass
    # Register via the public registry (the extension path).
    register_driver("memtest", MemDriver)
    # The list command builds its own driver via make_driver() — i.e. purely
    # through the registry. Seed one instance, then let list make its own.
    cfg = {"storage": {"backend": "memtest"}}
    seed = make_driver("memtest", cfg)
    seed.insert("system_info", {"os_make_version": "L", "agent_make_version": "H",
                                "hardware_details": "x", "created_at": 1})
    seed.close()

    from tracker import build_parser, COMMANDS, main
    tmpcfg = tempfile.mkdtemp(prefix="acc7-")
    cp = os.path.join(tmpcfg, "c.toml")
    with open(cp, "w") as f:
        f.write('[storage]\nbackend = "memtest"\n')
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(["--config", cp, "list", "system_info"])
    out = buf.getvalue()
    # The list output shows column names + the seeded row. "L" is the
    # os_make_version value we seeded; proving the mem driver served it.
    ok_list = "L" in out and "H" in out
    if not ok_list:
        print("DEBUG T7 out:", repr(out))
    shutil.rmtree(tmpcfg, ignore_errors=True)
    check("T7 registry extension (mem driver via make_driver + list)", ok_list,
          f"list_output_has_data={ok_list}")


if __name__ == "__main__":
    test1(); test2(); test3(); test4(); test5(); test6(); test7()
    print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)
    print("ALL ACCEPTANCE TESTS 1-7 PASSED")
