import json
import subprocess
import time

def run_git(cmd):
    return subprocess.check_output(cmd, shell=True, text=True)

state_file = "state.json"
try:
    with open(state_file) as f:
        state = json.load(f)
except Exception as e:
    print(f"Error loading state.json: {e}")
    exit(1)

print("Extracting commit history from git...")
# Get daily commits: take first commit of each day from oldest to newest
out = run_git("git log --reverse --pretty=format:'%H %ct %cs' state.json")
seen_dates = set()
daily_commits = []
for line in out.strip().split('\n'):
    parts = line.split()
    if len(parts) == 3:
        h, ct, cs = parts
        if cs not in seen_dates:
            seen_dates.add(cs)
            daily_commits.append((h, float(ct), cs))

print(f"Scanning {len(daily_commits)} historical daily commits...")
first_seen_map = {}
t0 = time.time()

for i, (commit_hash, ts, cs) in enumerate(daily_commits):
    try:
        content = run_git(f"git show {commit_hash}:state.json")
        d = json.loads(content)
        programs = d.get("programs", {}) if isinstance(d, dict) else {}
        if not programs and isinstance(d, dict) and "notified_handles" in d:
            for nh in d["notified_handles"]:
                if nh not in first_seen_map:
                    first_seen_map[nh] = ts
        for uid in programs.keys():
            if uid not in first_seen_map:
                first_seen_map[uid] = ts
    except Exception:
        continue

print(f"Discovered first_seen timestamps for {len(first_seen_map)} programs in {time.time()-t0:.1f}s.")

# Update the state.json programs with their restored first_seen timestamps
programs = state.get("programs", {})
updated_count = 0
now = time.time()

for uid, entry in programs.items():
    if isinstance(entry, dict):
        if uid in first_seen_map:
            entry["first_seen"] = first_seen_map[uid]
            updated_count += 1
        else:
            entry["first_seen"] = entry.get("last_seen", now)

with open(state_file, "w") as f:
    json.dump(state, f, indent=2)

print(f"Successfully updated {updated_count} programs in state.json with historical first_seen timestamps!")

