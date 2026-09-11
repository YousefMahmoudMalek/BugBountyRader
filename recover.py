import json
import subprocess

def run_git(cmd):
    return subprocess.check_output(cmd, shell=True, text=True)

state_file = "state.json"
try:
    with open(state_file) as f:
        state = json.load(f)
except Exception as e:
    print(f"Error loading state.json: {e}")
    exit(1)

# Get all commits that modified state.json, from oldest to newest
commits_out = run_git("git log --reverse --pretty=format:'%H %ct' state.json")
commits = [line.strip().split() for line in commits_out.strip().split('\n') if line.strip()]

first_seen_map = {}

for commit_hash, ts_str in commits:
    ts = float(ts_str)
    try:
        # Show file content at this commit
        file_content = run_git(f"git show {commit_hash}:state.json")
        old_state = json.loads(file_content)
        programs = old_state.get("programs", {})
        if not programs and isinstance(old_state, dict) and "programs" not in old_state:
            # maybe legacy format
            if isinstance(old_state.get("notified_handles"), list):
                for uid in old_state["notified_handles"]:
                    if uid not in first_seen_map:
                        first_seen_map[uid] = ts
            else:
                for uid in old_state.keys():
                    if uid != "notified_handles" and uid not in first_seen_map:
                        first_seen_map[uid] = ts
        else:
            for uid in programs.keys():
                if uid not in first_seen_map:
                    first_seen_map[uid] = ts
    except Exception as e:
        continue

# Now update the current state
programs = state.get("programs", {})
for uid, entry in programs.items():
    if isinstance(entry, dict):
        if uid in first_seen_map:
            entry["first_seen"] = first_seen_map[uid]
        else:
            entry["first_seen"] = entry.get("last_seen", 0)

with open(state_file, "w") as f:
    json.dump(state, f, indent=2)

print("Recovered first_seen dates!")
