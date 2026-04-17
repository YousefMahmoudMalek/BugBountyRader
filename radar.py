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
SCOPE_WEBHOOK_URL = os.getenv("SCOPE_WEBHOOK_URL")
WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE")
WHATSAPP_API_KEY = os.getenv("WHATSAPP_API_KEY")

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            # Migration: If it's the old list format, convert to the new dict format
            if isinstance(data.get("notified_handles"), list):
                print("Migrating state to new Scope Tracking format...")
                new_state = {"programs": {}}
                for entry in data["notified_handles"]:
                    new_state["programs"][entry] = [] # Silently populate as we don't have old targets
                return new_state
            return data
    return {"programs": {}}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def fetch_programs(platform, url):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Warning: Fetching {platform} failed (Attempt {attempt+1}/{max_retries}). Retrying in 5s...")
                time.sleep(5)
            else:
                print(f"Error fetching {platform} data: {e}")
                return []

def extract_targets(program, platform):
    """Cleanly extracts a list of in-scope target strings for each platform."""
    targets = []
    try:
        if platform == "HackerOne":
            raw_targets = program.get("targets", [])
            # Some H1 programs provide a list of strings, others provide dicts
            for t in raw_targets:
                if isinstance(t, dict):
                    targets.append(t.get("asset_identifier", t.get("target", "")))
                else:
                    targets.append(str(t))
        elif platform == "Bugcrowd":
            raw_targets = program.get("targets", {}).get("in_scope", [])
            for t in raw_targets:
                targets.append(t.get("target", ""))
        elif platform == "Intigriti":
            raw_targets = program.get("targets", {}).get("in_scope", [])
            for t in raw_targets:
                targets.append(t.get("endpoint", t.get("target", "")))
    except Exception as e:
        print(f"  Warning: Target extraction failed for {platform}: {e}")
    
    # Filter out empty or duplicate strings
    return sorted(list(set([t for t in targets if t])))

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
    context = program.get("_scope_update_context", "This is a brand new bug bounty program.")
    full_prompt = f"{GEMINI_PROMPT}\n\nCONTEXT: {context}\n\nProgram Data from {platform}:\n{json.dumps(program, indent=2)}"
    
    models_to_try = [
        'models/gemini-2.0-flash',
        'models/gemini-2.5-flash',
        'models/gemini-flash-latest'
    ]
    max_retries = 3
    
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
                # Debug info for invalid key
                if "API_KEY_INVALID" in error_str or "INVALID_ARGUMENT" in error_str:
                    masked_key = f"{GEMINI_API_KEY[:4]}...{GEMINI_API_KEY[-4:]}" if GEMINI_API_KEY else "NONE"
                    print(f"  [!] API Key Error. Current key being used: {masked_key}")
                
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "QUOTA" in error_str:
                    retries += 1
                    wait_time = 5 * retries # Much shorter wait
                    print(f"  Quota hit (429). Retrying in {wait_time}s... (Attempt {retries}/{max_retries})")
                    time.sleep(wait_time)
                elif "404" in error_str or "NOT_FOUND" in error_str:
                    print(f"  Model {model_name} not available in this region. Skipping...")
                    break
                else:
                    print(f"  AI error with {model_name}: {e}")
                    break 
        
        print(f"  {model_name} failed or timed out. Trying next model if available...")

    return "AI analysis failed after multiple attempts and fallbacks.", 0

def send_discord_alert(message, webhook_url=None):
    url = webhook_url or DISCORD_WEBHOOK_URL
    if not url:
        if "--test" in sys.argv:
            print("  [!] Discord alert skipped (No Webhook URL found)")
        return
    payload = {"content": message}
    try:
        requests.post(url, json=payload, timeout=10)
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
    print("Running LIVE notification test for both webhooks...")
    print("Fetching latest programs to provide real AI analysis samples...")
    
    for platform, url in DATA_SOURCES.items():
        print(f"  Testing {platform}...")
        programs = fetch_programs(platform, url)
        if not programs:
            print(f"  [!] No programs found for {platform}")
            continue
            
        latest = programs[0]
        handle = latest.get("handle") or latest.get("name")
        prog_url = latest.get("url") or "Check platform for link"
        print(f"  Latest program on {platform}: {handle}")
        
        # Test 1: New Program (Webhook 1)
        print(f"  [Test 1/2] Sending New Program alert to Webhook #1...")
        ai_summary, rating = analyze_with_ai(latest, platform)
        
        test_msg = f"🧪 **BugBountyRadar LIVE TEST (New Program)**\n"
        test_msg += f"**Platform:** {platform}\n"
        test_msg += f"**Program:** {handle} (Rating: {rating}/10)\n"
        test_msg += f"**Link:** {prog_url}\n"
        test_msg += f"\n--- AI SUMMARY ---\n{ai_summary}\n"
        
        send_discord_alert(test_msg, DISCORD_WEBHOOK_URL)
        
        # Test 2: Scope Expansion (Webhook 2)
        print(f"  [Test 2/2] Sending Scope Expansion alert to Webhook #2...")
        scope_context = "This is a simulated scope update. New targets found: test-scope-1.com, test-scope-2.com"
        latest_with_context = latest.copy()
        latest_with_context["_scope_update_context"] = scope_context
        
        ai_summary, rating = analyze_with_ai(latest_with_context, platform)
        
        update_msg = f"🧪 **BugBountyRadar LIVE TEST (Scope Expansion)**\n"
        update_msg += f"**Program:** {handle} ({platform})\n"
        update_msg += f"**New Assets:** `test-scope-1.com, test-scope-2.com` \n"
        update_msg += f"**Link:** {prog_url}\n"
        update_msg += f"\n--- AI ANALYSIS OF NEW ASSETS ---\n{ai_summary}\n"
        
        send_discord_alert(update_msg, SCOPE_WEBHOOK_URL)
        break # Only test one platform to avoid spam
    
    print("\nTest finished. Check both Discord channels!")

def main():
    if "--test" in sys.argv:
        run_test()
        return

    state = load_state()
    new_programs_found = 0
    scope_updates_found = 0
    is_initial_run = len(state.get("programs", {})) == 0

    print(f"\n--- BugBountyRadar Check Started: {time.strftime('%Y-%m-%d %H:%M:%S')} ---")

    if is_initial_run:
        print("Initial run. Seeding state...")

    for platform, url in DATA_SOURCES.items():
        print(f"Checking {platform}...")
        programs = fetch_programs(platform, url)
        
        for program in programs:
            handle = program.get("handle") or program.get("name")
            prog_url = program.get("url") or "Check platform for link"
            if not handle:
                continue

            unique_id = f"{platform}:{handle}"
            current_targets = extract_targets(program, platform)

            # Case 1: Initial Run (Silent Seed)
            if is_initial_run:
                state["programs"][unique_id] = current_targets
                continue

            # Case 2: Brand New Program
            if unique_id not in state["programs"]:
                print(f"New program found: {handle} on {platform}")
                
                ai_summary, rating = analyze_with_ai(program, platform)
                
                # Notification fallback
                if "FAILED" in ai_summary.upper() or "SKIPPED" in ai_summary.upper():
                    rating = 10
                
                if rating >= MIN_RATING:
                    alert_msg = f"🚀 **New Bug Bounty Program!**\n"
                    alert_msg += f"**Platform:** {platform}\n"
                    alert_msg += f"**Program:** {handle}\n"
                    alert_msg += f"**Link:** {prog_url}\n"
                    alert_msg += f"\n--- AI SUMMARY ---\n{ai_summary}\n"
                    
                    send_discord_alert(alert_msg, DISCORD_WEBHOOK_URL)
                    send_whatsapp_alert(alert_msg)
                    new_programs_found += 1
                
                state["programs"][unique_id] = current_targets
                save_state(state)
                continue

            # Case 3: Existing Program - Check for Scope Updates
            old_targets = state["programs"].get(unique_id, [])
            new_targets = [t for t in current_targets if t not in old_targets]

            if new_targets:
                print(f"Scope update found for {handle}: {len(new_targets)} new targets added.")
                
                # Skip AI for scope updates as requested to save time/quota
                ai_summary = "AI analysis skipped for scope expansion."
                # We skip analyze_with_ai entirely here

                alert_msg = f"🛰️ **Scope Expansion Detect!**\n"
                alert_msg += f"**Program:** {handle} ({platform})\n"
                alert_msg += f"**New Assets:** `{', '.join(new_targets)}` \n"
                alert_msg += f"**Link:** {prog_url}\n"
                
                # Send to Webhook #2
                send_discord_alert(alert_msg, SCOPE_WEBHOOK_URL)
                
                state["programs"][unique_id] = current_targets
                scope_updates_found += 1
                save_state(state)

    if is_initial_run:
        save_state(state)
        print("State seeded.")
    else:
        print(f"Processed {new_programs_found} new programs and {scope_updates_found} scope updates.")

if __name__ == "__main__":
    main()
