import os
import json
import requests
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE")  # For CallMeBot
WHATSAPP_API_KEY = os.getenv("WHATSAPP_API_KEY") # For CallMeBot

DATA_SOURCES = {
    "HackerOne": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/hackerone_data.json",
    "Bugcrowd": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/bugcrowd_data.json",
    "Intigriti": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/intigriti_data.json"
}

STATE_FILE = "state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"notified_handles": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def fetch_programs(platform, url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching {platform} data: {e}")
        return []

def analyze_with_ai(program, platform):
    if not GEMINI_API_KEY:
        return "AI analysis skipped (No API Key)."

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

    prompt = f"""
    Analyze the following bug bounty program from {platform} and provide a short summary.
    Include:
    1. A rating from 1 to 10 (based on potential rewards and program reputation if known).
    2. Key highlights (e.g., target types, specific high-pay rewards).
    3. A brief "Should I hunt?" advice.

    Program Data:
    {json.dumps(program)[:3000]}  # Limit data sent to AI
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI analysis failed: {e}"

def send_discord_alert(message):
    if not DISCORD_WEBHOOK_URL:
        return
    
    payload = {"content": message}
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending Discord alert: {e}")

def send_whatsapp_alert(message):
    if not (WHATSAPP_PHONE and WHATSAPP_API_KEY):
        return
    
    # CallMeBot API format: https://api.callmebot.com/whatsapp.php?phone=[phone]&text=[message]&apikey=[apikey]
    url = "https://api.callmebot.com/whatsapp.php"
    params = {
        "phone": WHATSAPP_PHONE,
        "text": message,
        "apikey": WHATSAPP_API_KEY
    }
    try:
        requests.get(url, params=params, timeout=10)
    except Exception as e:
        print(f"Error sending WhatsApp alert: {e}")

def main():
    state = load_state()
    new_programs_found = 0
    is_initial_run = len(state["notified_handles"]) == 0

    if is_initial_run:
        print("Initial run detected. Seeding state with existing programs without notifying...")

    for platform, url in DATA_SOURCES.items():
        print(f"Checking {platform}...")
        programs = fetch_programs(platform, url)
        
        for program in programs:
            handle = program.get("handle") or program.get("name")
            if not handle:
                continue

            unique_id = f"{platform}:{handle}"
            if unique_id in state["notified_handles"]:
                continue

            if is_initial_run:
                state["notified_handles"].append(unique_id)
                continue

            print(f"New program found: {handle} on {platform}")
            
            # AI Analysis
            ai_summary = analyze_with_ai(program, platform)
            
            # Format message
            alert_msg = f"🚀 **New Bug Bounty Program!**\n"
            alert_msg += f"**Platform:** {platform}\n"
            alert_msg += f"**Program:** {handle}\n"
            alert_msg += f"--- AI SUMMARY ---\n{ai_summary}\n"
            
            # Send alerts
            send_discord_alert(alert_msg)
            send_whatsapp_alert(alert_msg)
            
            # Update state
            state["notified_handles"].append(unique_id)
            new_programs_found += 1
            
            # Save state after each to avoid losing progress
            save_state(state)

    if is_initial_run:
        save_state(state)
        print("State seeded successfully. Future runs will notify new programs.")
    elif new_programs_found == 0:
        print("No new programs found.")
    else:
        print(f"Processed {new_programs_found} new programs.")

if __name__ == "__main__":
    main()
