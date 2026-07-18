import os, sys, tempfile, shutil, json, subprocess

REPO = "/home/rahlquist/model-tracker"
TMP = tempfile.mkdtemp(prefix="mt-ops-")
DATADIR = os.path.join(TMP, "data")
os.makedirs(DATADIR, exist_ok=True)

# temp config
cfg_path = os.path.join(TMP, "config.toml")
with open(cfg_path, "w") as f:
    f.write(f'[storage]\nbackend = "csv"\n[storage.csv]\ndata_dir = "{DATADIR}"\n')

env = dict(os.environ, MODEL_TRACKER_CONFIG=cfg_path)
PY = sys.executable
TRACKER = os.path.join(REPO, "scripts", "tracker.py")

def run(*a):
    r = subprocess.run([PY, TRACKER, *a], capture_output=True, text=True, env=env)
    print(">>>", " ".join(a))
    print(r.stdout)
    if r.returncode != 0:
        print("STDERR:", r.stderr)
        raise SystemExit(f"FAILED: {a}")
    return r

# record-session via JSON
payload = {
    "system_info": {"os_make_version": "Linux 6.1", "agent_make_version": "Hermes 1.0", "hardware_details": "16c/32G"},
    "system_config": {"run_id": "run-1", "run_name": "first", "stats": "verbatim", "nturns": 12, "ctx_length": 128000, "num_compressed": 1, "was_complete": True, "was_errors": ""},
    "models": [{"model_alias": "opus", "model_name": "claude-opus", "model_context_size": 200000, "model_hosted": True, "model_free": False}],
    "notes": [{"model_alias": "opus", "user_notes": "great", "user_rating": 9}],
}
pj = os.path.join(TMP, "rec.json")
with open(pj, "w") as f:
    json.dump(payload, f)

run("record-session", "--from-json", pj)
run("list", "system_info")
# capture model id
rows = subprocess.run([PY, TRACKER, "list", "model_info", "--json"], capture_output=True, text=True, env=env)
mi = json.loads(rows.stdout)[0]["id"]
print("model id:", mi[:8])
run("add-note", "--model-id", mi, "--note", "later thought", "--rating", "8")
# capture note id
nr = subprocess.run([PY, TRACKER, "list", "user_notes", "--json"], capture_output=True, text=True, env=env)
nid = json.loads(nr.stdout)[0]["id"]
print("note id:", nid[:8])
# edit-note cannot set agent_rating
r = subprocess.run([PY, TRACKER, "edit-note", nid, "--rating", "10"], capture_output=True, text=True, env=env)
print("edit-note ok:", r.returncode == 0)
# edit arbitrary column with type validation
run("edit", "model_info", mi, "--set", "model_context_size=256000")
# reject unknown column
r = subprocess.run([PY, TRACKER, "edit", "model_info", mi, "--set", "bogus=1"], capture_output=True, text=True, env=env)
print("edit unknown-column rejected:", r.returncode != 0)
# show resolves FK
run("show", "model_info", mi)

shutil.rmtree(TMP, ignore_errors=True)
print("\nOPS (non-rank) CLI SMOKE: DONE")
