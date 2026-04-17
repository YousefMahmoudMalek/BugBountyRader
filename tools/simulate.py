import json
import random
import os
import subprocess
import sys

# Ensure UTF-8 for Windows terminal emojis
if sys.platform == "win32":
    import codecs
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

# Add the parent directory to sys.path so we can find config.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import STATE_FILE

def simulate():
    if not os.path.exists(STATE_FILE):
        print(f"Error: {STATE_FILE} not found. Run seed first!")
        return

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    if not state.get("programs"):
        print("Error: No programs in state. Run seed first!")
        return

    print("🧪 Simulating a 'Hit'...")
    
    # 1. Simulate a New Program by removing one
    all_uids = list(state["programs"].keys())
    if all_uids:
        removed_prog = random.choice(all_uids)
        del state["programs"][removed_prog]
        print(f"  - Removed program: {removed_prog} (Will trigger Webhook #1)")

    # 2. Simulate a Scope Expansion by removing a target from an existing program
    # Search for a program that has targets
    prog_with_targets = [uid for uid, targets in state["programs"].items() if len(targets) > 1]
    if prog_with_targets:
        target_uid = random.choice(prog_with_targets)
        removed_target = state["programs"][target_uid].pop() # Remove the last target
        print(f"  - Removed asset from {target_uid}: {removed_target['target']} (Will trigger Webhook #2)")

    # Save the 'corrupted' state
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    print("\n🚀 Memory modified. Running scan now...")
    subprocess.run(["python", "radar.py"])

if __name__ == "__main__":
    simulate()
