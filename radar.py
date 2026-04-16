import os
import json
import requests
from google import genai
import sys
import re
import time
from dotenv import load_dotenv
from config import GEMINI_PROMPT, DATA_SOURCES, STATE_FILE, MIN_RATING

# Load environment variables
load_dotenv()

# Secrets
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE")
WHATSAPP_API_KEY = os.getenv("WHATSAPP_API_KEY")

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
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching {platform} data: {e}")
        return []

def extract_rating(ai_text):
    """Extracts X from 'RATING: X/10' format."""
    match = re.search(r"RATING:\s*(\d+)/10", ai_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0

def analyze_with_ai(program, platform):
    if not GEMINI_API_KEY:
        return "AI analysis skipped (No API Key).", 10

    client = genai.Client(api_key=GEMINI_API_KEY)
    full_prompt = f"{GEMINI_PROMPT}\n\nProgram Data from {platform}:\n{json.dumps(program, indent=2)}"
    
    # Implementation of Fallback and Retries
    models_to_try = ['gemini-2.0-flash', 'gemini-1.5-flash']
    max_retries = 2
    
    for model_name in models_to_try:
        retries = 0
        while retries < max_retries:
            try:
                print(f"  Attempting AI analysis with {model_name}...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=full_prompt
                )
                text = response.text
                rating = extract_rating(text)
                return text, rating
            except Exception as e:
                error_str = str(e).upper()
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    retries += 1
                    wait_time = 30 * retries
                    print(f"  Rate limit hit (429). Retrying in {wait_time}s... (Attempt {retries}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    print(f"  AI error with {model_name}: {e}")
                    break # Try next model if it's not a rate limit error
        
        print(f"  {model_name} failed or timed out. Trying next model if available...")

    return "AI analysis failed after multiple attempts and fallbacks.", 0

def send_discord_alert(message):
    if not DISCORD_WEBHOOK_URL:
        if "--test" in sys.argv:
            print("  [!] Discord skipped (No DISCORD_WEBHOOK_URL environment variable found)")
        return
    payload = {"content": message}
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending Discord alert: {e}")

def send_whatsapp_alert(message):
    if not (WHATSAPP_PHONE and WHATSAPP_API_KEY):
        if "--test" in sys.argv:
            print("  [!] WhatsApp skipped (No WHATSAPP_PHONE or WHATSAPP_API_KEY found)")
        return
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

def run_test():
    print("Running notification test...")
    test_msg = "🔔 **BugBountyRadar Connection Test**\nYour alert setup is working correctly! 🚀"
    
    send_discord_alert(test_msg)
    send_whatsapp_alert(test_msg)
    
    print("\nTest finished.")
    print("-" * 30)
    print("NOTE: If you added secrets to GitHub, they will ONLY work when running on GitHub.")
    print("To test locally, you need to add them to a '.env' file in this folder.")
    print("-" * 30)

def main():
    if "--test" in sys.argv:
        run_test()
        return

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
            
            ai_summary, rating = analyze_with_ai(program, platform)
            
            if rating < MIN_RATING:
                print(f"Skipping {handle} (Rating {rating} < Min {MIN_RATING})")
                state["notified_handles"].append(unique_id)
                save_state(state)
                continue

            alert_msg = f"🚀 **New Bug Bounty Program!**\n"
            alert_msg += f"**Platform:** {platform}\n"
            alert_msg += f"**Program:** {handle}\n"
            alert_msg += f"\n--- AI SUMMARY ---\n{ai_summary}\n"
            
            send_discord_alert(alert_msg)
            send_whatsapp_alert(alert_msg)
            
            state["notified_handles"].append(unique_id)
            new_programs_found += 1
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
