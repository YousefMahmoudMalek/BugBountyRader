import requests
import json

base_url = "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/"
platforms = ['bugcrowd', 'federacy', 'hackerone', 'intigriti', 'yeswehack']

for p in platforms:
    url = f"{base_url}{p}_data.json"
    try:
        data = requests.get(url).json()
        if not data:
            print(f"--- {p} --- empty")
            continue
            
        prog = data[0]
        print(f"\n--- {p} ---")
        print(f"Keys: {list(prog.keys())}")
        
        # Look for bounty info
        if p == 'bugcrowd':
            print(f"Bounty Example: {prog.get('max_bounty') if 'max_bounty' in prog else 'Not found'}")
        elif p == 'hackerone':
            print(f"Bounty Example: {prog.get('offers_bounties')}")
        
        # Check target structure
        targets = prog.get('targets', [])
        print(f"Targets Type: {type(targets)}")
        if isinstance(targets, dict):
            print(f"Target Keys: {list(targets.keys())}")
        elif isinstance(targets, list) and targets:
            print(f"First Target: {targets[0]}")
            
    except Exception as e:
        print(f"Error checking {p}: {e}")
