import requests
import json

base_url = "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/"
platforms = ['bugcrowd', 'federacy', 'hackerone', 'intigriti', 'yeswehack']

for p in platforms:
    url = f"{base_url}{p}_data.json"
    try:
        data = requests.get(url).json()
        if not data: continue
        
        prog = data[0]
        in_scope = prog.get('targets', {}).get('in_scope', [])
        if in_scope and isinstance(in_scope, list):
            print(f"\n--- {p} in_scope[0] ---")
            print(in_scope[0])
            
    except Exception as e:
        print(f"Error checking {p}: {e}")
