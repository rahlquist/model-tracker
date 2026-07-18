import os, sys, tempfile, shutil, subprocess, json

REPO = "/home/rahlquist/model-tracker"
TMP = tempfile.mkdtemp(prefix="mt-rank-")
DATADIR = os.path.join(TMP, "data"); os.makedirs(DATADIR)
cfg_path = os.path.join(TMP, "config.toml")
with open(cfg_path, "w") as f:
    f.write(f'[storage]\nbackend = "csv"\n[storage.csv]\ndata_dir = "{DATADIR}"\n')
env = dict(os.environ, MODEL_TRACKER_CONFIG=cfg_path)
PY = sys.executable
TRACKER = os.path.join(REPO, "scripts", "tracker.py")

def run(*a):
    r = subprocess.run([PY, TRACKER, *a], capture_output=True, text=True, env=env)
    print(r.stdout)
    if r.returncode != 0:
        print("STDERR:", r.stderr); raise SystemExit(f"FAILED {a}")
    return r

# Seed via direct driver to control every field precisely.
sys.path.insert(0, os.path.join(REPO, "scripts"))
from storage import make_driver, load_config
from storage.csv_driver import CsvDriver
import time
d = CsvDriver(); d.init(load_config(cfg_path))

t = int(time.time())
si = d.insert("system_info", {"os_make_version":"L","agent_make_version":"H","hardware_details":"x","created_at":t})

# Model A: good, complete, no errors, rated 9 and 10
scA = d.insert("system_config", {"system_info_id":si,"run_id":"rA","run_name":"A","stats":"","nturns":100,"ctx_length":128000,"num_compressed":0,"was_complete":True,"was_errors":"","created_at":t})
mA = d.insert("model_info", {"system_config_id":scA,"model_alias":"A","model_name":"model-a","model_context_size":200000,"model_hosted":True,"model_free":False,"model_added":t,"model_last_use":t+3,"created_at":t})
d.insert("user_notes", {"model_info_id":mA,"user_notes":"good","user_rating":9,"created_at":t})
d.insert("user_notes", {"model_info_id":mA,"user_notes":"great","user_rating":10,"created_at":t})

# Model B: incomplete run, rated 7
scB = d.insert("system_config", {"system_info_id":si,"run_id":"rB","run_name":"B","stats":"","nturns":50,"ctx_length":128000,"num_compressed":1,"was_complete":False,"was_errors":"","created_at":t})
mB = d.insert("model_info", {"system_config_id":scB,"model_alias":"B","model_name":"model-b","model_context_size":200000,"model_hosted":True,"model_free":False,"model_added":t,"model_last_use":t+2,"created_at":t})
d.insert("user_notes", {"model_info_id":mB,"user_notes":"ok","user_rating":7,"created_at":t})

# Model C: errors, rated 5
scC = d.insert("system_config", {"system_info_id":si,"run_id":"rC","run_name":"C","stats":"","nturns":30,"ctx_length":128000,"num_compressed":0,"was_complete":True,"was_errors":"timeout","created_at":t})
mC = d.insert("model_info", {"system_config_id":scC,"model_alias":"C","model_name":"model-c","model_context_size":200000,"model_hosted":True,"model_free":False,"model_added":t,"model_last_use":t+1,"created_at":t})
d.insert("user_notes", {"model_info_id":mC,"user_notes":"bad","user_rating":5,"created_at":t})

# Model D: no notes -> unranked
scD = d.insert("system_config", {"system_info_id":si,"run_id":"rD","run_name":"D","stats":"","nturns":10,"ctx_length":128000,"num_compressed":0,"was_complete":True,"was_errors":"","created_at":t})
d.insert("model_info", {"system_config_id":scD,"model_alias":"D","model_name":"model-d","model_added":t,"model_last_use":t,"created_at":t})

d.close()

print("=== rank (plain text) ===")
run("rank")
print("\n=== rank (markdown) ===")
run("rank", "--markdown")

# verify agent_rating written back
rows = subprocess.run([PY, TRACKER, "list", "user_notes", "--json"], capture_output=True, text=True, env=env)
data = json.loads(rows.stdout)
print("\nuser_notes agent_rating values:", [r.get("agent_rating") for r in data])

shutil.rmtree(TMP, ignore_errors=True)
print("\nRANK SMOKE: DONE")
