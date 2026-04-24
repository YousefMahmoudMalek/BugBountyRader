import os
import json
import requests
from google import genai
from google.genai import types
import sys
import re
import time
from dotenv import load_dotenv
from config import NEW_PROGRAM_PROMPT, SCOPE_UPDATE_PROMPT, DATA_SOURCES, STATE_FILE, MIN_RATING

# Load environment variables
load_dotenv()

# Secrets
# Parse multiple keys into a list
_raw_keys = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
GEMINI_API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]
CURRENT_KEY_INDEX = 0

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SCOPE_WEBHOOK_URL = os.getenv("SCOPE_WEBHOOK_URL")
LOG_WEBHOOK_URL = os.getenv("LOG_WEBHOOK_URL")
WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE")
WHATSAPP_API_KEY = os.getenv("WHATSAPP_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Global log buffer for session summary
LOG_BUFFER = []

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                return {"programs": {}}

            # 1. Legacy Migration: notified_handles list -> programs dict
            if isinstance(data.get("notified_handles"), list):
                print("Migrating legacy state format...")
                new_state = {"programs": {}}
                now = time.time()
                for entry in data["notified_handles"]:
                    new_state["programs"][entry] = {"targets": [], "last_seen": now}
                return new_state
            
            # 2. Schema Migration: [targets] -> {"targets": [...], "last_seen": ...}
            if "programs" in data:
                modified = False
                now = time.time()
                for uid, val in data["programs"].items():
                    if isinstance(val, list):
                        data["programs"][uid] = {"targets": val, "last_seen": now}
                        modified = True
                if modified:
                    print("Migrated program entries to include 'last_seen' timestamp.")
            
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
    """Extracts a list of target dictionaries with metadata (type, bounty, etc)."""
    targets = []
    try:
        # Common structure is targets -> in_scope
        raw_list = []
        if isinstance(program.get("targets"), dict):
            raw_list = program["targets"].get("in_scope", [])
        elif isinstance(program.get("targets"), list):
            raw_list = program["targets"]

        for t in raw_list:
            if not isinstance(t, dict):
                # Fallback for simple string targets
                targets.append({"target": str(t), "type": "Other", "bounty": True})
                continue

            # Extract target string based on platform keys
            target_str = t.get("asset_identifier") or t.get("target") or t.get("endpoint") or ""
            if not target_str: continue

            # Extract type
            asset_type = t.get("asset_type") or t.get("type") or "Other"
            
            # Extract bounty (Default to True unless explicitly False)
            bounty = t.get("eligible_for_bounty")
            if bounty is None:
                bounty = t.get("offers_awards")
            if bounty is None:
                bounty = True # Default assumption for public BBP

            # Extract severity
            severity = t.get("max_severity") or t.get("impact") or "Unknown"

            targets.append({
                "target": str(target_str).strip(),
                "type": str(asset_type).title(),
                "bounty": bool(bounty),
                "severity": str(severity)
            })
    except Exception as e:
        print(f"  Warning: Target extraction failed for {platform}: {e}")
    
    # Sort and remove duplicates by 'target'
    seen = set()
    unique_targets = []
    for t in targets:
        if t["target"] not in seen:
            unique_targets.append(t)
            seen.add(t["target"])
    return unique_targets

def extract_rating(ai_text):
    """Extracts X from 'RATING: X/10' format."""
    match = re.search(r"RATING:\s*(\d+)/10", ai_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0

def analyze_with_deepseek(program, platform, base_prompt, context):
    if not DEEPSEEK_API_KEY:
        return None
    
    # DeepSeek is OpenAI-compatible
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "You are a professional bug bounty scout. Return concise, actionable intel."},
            {"role": "user", "content": f"{base_prompt}\n\nCONTEXT: {context}\n\nData: {json.dumps(program)}"}
        ],
        "temperature": 0.7,
        "max_tokens": 1000
    }
    
    try:
        print(f"  AI (DeepSeek) | Platform: {platform}")
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()
        
        if response.status_code != 200:
            err = data.get("error", {}).get("message", "Unknown Error")
            print(f"  [!] DeepSeek Error {response.status_code}: {err}")
            return None
            
        text = data["choices"][0]["message"]["content"]
        return text
    except Exception as e:
        print(f"  [!] DeepSeek Connection Failed: {e}")
        return None

def analyze_with_ai(program, platform, prompt_type="new_program"):
    global CURRENT_KEY_INDEX
    
    # Select prompt template (Shared by all providers)
    if prompt_type == "scope_update":
        base_prompt = SCOPE_UPDATE_PROMPT
        ctx = program.get("_scope_update_context", "New assets added.")
        context = f"Analyze these SPECIFIC new assets: {ctx}"
    else:
        base_prompt = NEW_PROGRAM_PROMPT
        context = "New bug bounty program. Perform surface level research."

    # 1. Try Gemini Key Pool First (User Priority)
    if GEMINI_API_KEYS:
        full_prompt = f"{base_prompt}\n\nCONTEXT: {context}\n\nProgram Data from {platform}:\n{json.dumps(program, indent=2)}"
        models_to_try = ['models/gemini-2.5-flash', 'models/gemini-3-flash', 'models/gemini-2.0-flash', 'models/gemini-1.5-flash-latest']
        
        # Try max 2 keys to stay fast
        max_key_attempts = min(len(GEMINI_API_KEYS), 2)  
        keys_to_attempt = GEMINI_API_KEYS[CURRENT_KEY_INDEX:] + GEMINI_API_KEYS[:CURRENT_KEY_INDEX]
        keys_to_attempt = keys_to_attempt[:max_key_attempts]
        
        for api_key in keys_to_attempt:
            CURRENT_KEY_INDEX = GEMINI_API_KEYS.index(api_key)
            client = genai.Client(api_key=api_key)
            key_exhausted = False
            
            for model_name in models_to_try:
                for use_search in [True, False]:
                    retries = 0
                    while retries < 1:
                        try:
                            search_label = "(Search)" if use_search else "(Basic)"
                            print(f"  AI (Gemini-{model_name}) {search_label} | Key: ...{api_key[-5:]}")
                            
                            config = None
                            if use_search:
                                grounding_tool = types.Tool(google_search=types.GoogleSearch())
                                config = types.GenerateContentConfig(tools=[grounding_tool])

                            response = client.models.generate_content(model=model_name, contents=full_prompt, config=config)
                            return response.text, extract_rating(response.text)
                        except Exception as e:
                            err_msg = str(e).upper()
                            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                                if use_search: break 
                                else:
                                    print(f"  [!] Key exhausted (429). Rotating...")
                                    key_exhausted = True
                                    break 
                            elif "404" in err_msg: break 
                            else: retries += 1
                    if key_exhausted or (not use_search and retries >= 1): break 
                if key_exhausted: break 

    # 2. Try DeepSeek as Fallback
    ds_res = analyze_with_deepseek(program, platform, base_prompt, context)
    if ds_res:
        return ds_res, extract_rating(ds_res)

    return "AI analysis failed (Gemini exhausted + DeepSeek fail).", 0

def send_discord_alert(message, webhook_url=None, title="BugBountyRadar Info", use_embed=True):
    url = webhook_url or DISCORD_WEBHOOK_URL
    if not url: return
    
    if use_embed:
        # We use Embeds to support masked links and look "Premium"
        desc = message
        if len(desc) > 3900:
            desc = desc[:3850] + "\n\n... (Truncated)"
            
        payload = {
            "embeds": [{
                "title": title,
                "description": desc,
                "color": 0x5865F2, # Discord Blurple
                "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            }]
        }
    else:
        # Plain text
        clean_msg = message
        if len(clean_msg) > 1950:
            clean_msg = clean_msg[:1900] + "\n\n... (Truncated)"
        payload = {"content": f"🚀 **{title}**\n{clean_msg}"}
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code >= 400:
            print(f"  [!] Discord Error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Error sending Discord alert: {e}")

def send_log_alert(message):
    """Sends a message to the dedicated logging webhook."""
    if not LOG_WEBHOOK_URL:
        return
    
    # Truncate log content for Discord (2000 limit)
    clean_msg = message
    if len(clean_msg) > 1950:
        clean_msg = clean_msg[:1900] + "\n\n... (Log Truncated)"
        
    payload = {"content": f"📅 **{time.strftime('%Y-%m-%d %H:%M:%S')}**\n{clean_msg}"}
    try:
        requests.post(LOG_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending log alert: {e}")

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
    print("Running LIVE notification test for all 3 webhooks...")
    
    # Test 3: Log Webhook
    print(f"  [Test 1/3] Sending Log alert to Webhook #3...")
    send_log_alert("🚀 **BugBountyRadar TEST RUN**\nStatus: Healthy\nPlatforms: 5\nAI Status: Active")

    for platform, url in DATA_SOURCES.items():
        print(f"  Testing {platform}...")
        programs = fetch_programs(platform, url)
        if not programs: continue
            
        latest = programs[0]
        handle = latest.get("handle") or latest.get("name")
        prog_url = latest.get("url") or "Check platform for link"
        
        # Test 1: New Program
        print(f"  [Test 2/3] Sending New Program alert to Webhook #1...")
        ai_summary, rating = analyze_with_ai(latest, platform)
        
        test_msg = f"🧪 **BugBountyRadar LIVE TEST (New Program)**\n"
        test_msg += f"**Platform:** {platform}\n"
        test_msg += f"**Program:** {handle} (Rating: {rating}/10)\n"
        test_msg += f"**Link:** {prog_url}\n"
        test_msg += f"\n--- AI SUMMARY ---\n{ai_summary}\n"
        send_discord_alert(test_msg, DISCORD_WEBHOOK_URL)
        
        # Test 2: Scope Expansion
        print(f"  [Test 3/3] Sending Scope Expansion alert to Webhook #2...")
        update_msg = f"🧪 **BugBountyRadar LIVE TEST (Scope Expansion)**\n"
        update_msg += f"**Program:** {handle} ({platform})\n"
        update_msg += f"**New Assets:** `[WEB] test-domain.com`, `[APK] com.test.app` \n"
        update_msg += f"**Link:** {prog_url}\n"
        send_discord_alert(update_msg, SCOPE_WEBHOOK_URL)
        break
    
    print("\nTest finished. Check all 3 Discord channels!")

def main():
    if "--test" in sys.argv:
        run_test()
        return

    is_seed_run = "--seed" in sys.argv
    state = load_state()
    new_programs_found = 0
    scope_updates_found = 0
    ai_success = 0
    ai_fail = 0
    is_initial_run = len(state.get("programs", {})) == 0

    if is_seed_run:
        print("\n--- BugBountyRadar SEED MODE (Silent) ---")
    else:
        print(f"\n--- BugBountyRadar Check Started: {time.strftime('%Y-%m-%d %H:%M:%S')} ---")

    now = time.time()
    REOPEN_THRESHOLD = 3600 * 6 # 6 hours (Considered 'closed' if gone for this long)
    ALERT_THRESHOLD = 3600 * 24 * 7 # 7 days (Re-alert if gone for longer than this)

    for platform, url in DATA_SOURCES.items():
        print(f"Checking {platform}...")
        programs = fetch_programs(platform, url)
        
        for program in programs:
            handle = program.get("handle") or program.get("name")
            prog_url = program.get("url") or "Check platform link"
            if not handle: continue

            unique_id = f"{platform}:{handle}"
            current_targets = extract_targets(program, platform)
            
            # Initial Run / Seed Mode
            if is_initial_run or is_seed_run:
                state["programs"][unique_id] = {"targets": current_targets, "last_seen": now}
                continue

            # Case: New Program or Reopened after long time
            if unique_id not in state["programs"]:
                print(f"New program found: {handle} on {platform}")
                is_new = True
                should_alert_new = True
                old_data = []
            else:
                is_new = False
                entry = state["programs"][unique_id]
                prev_last_seen = entry.get("last_seen", 0)
                time_since = now - prev_last_seen
                old_data = entry.get("targets", [])
                
                # Check if it was gone for a while
                if time_since > REOPEN_THRESHOLD:
                    days_since = int(time_since // (3600 * 24))
                    time_desc = f"{days_since} days" if days_since > 0 else f"{int(time_since // 3600)} hours"
                    
                    if time_since > ALERT_THRESHOLD:
                        print(f"Program REOPENED (Long absence: {time_desc}): {handle}")
                        should_alert_new = True
                    else:
                        print(f"Program reopened ({time_desc}): {handle}")
                        LOG_BUFFER.append(f"🔄 **Reopened**: `{handle}` ({platform}) - Back after {time_desc}")
                        should_alert_new = False
                else:
                    should_alert_new = False

                # Always update last_seen for seen programs
                state["programs"][unique_id]["last_seen"] = now

            # Trigger "New Program" alert if actually new OR reopened after long time
            if should_alert_new:
                ai_summary, rating = analyze_with_ai(program, platform)
                
                if "FAILED" in ai_summary.upper() or "SKIPPED" in ai_summary.upper():
                    rating = 10
                    ai_fail += 1
                    LOG_BUFFER.append(f"❌ **AI FAIL**: `{handle}` ({platform})")
                else:
                    ai_success += 1

                if rating >= MIN_RATING:
                    alert_title = "New Bug Bounty Program!"
                    if not is_new:
                        time_since = now - prev_last_seen
                        days = int(time_since // (3600 * 24))
                        alert_title = f"🛰️ Program Reopened! (After {days} days)"

                    alert_msg = f"**Platform:** {platform}\n"
                    alert_msg += f"**Program:** {handle}\n"
                    alert_msg += f"**Link:** {prog_url}\n"
                    alert_msg += f"\n--- AI SUMMARY ---\n{ai_summary}\n"
                    send_discord_alert(alert_msg, DISCORD_WEBHOOK_URL, title=alert_title, use_embed=False)
                    new_programs_found += 1
                
                state["programs"][unique_id] = {"targets": current_targets, "last_seen": now}
                # No need to save here, we save at the end
                continue

            # Case: Scope Update
            old_targets_strs = [t["target"] if isinstance(t, dict) else t for t in old_data]
            new_targets = [t for t in current_targets if t["target"] not in old_targets_strs]

            if new_targets:
                print(f"Scope update for {handle}: {len(new_targets)} new targets. Analyzing...")
                target_summary = ", ".join([f"[{t['type']}] {t['target']}" for t in new_targets])
                program["_scope_update_context"] = target_summary
                ai_summary, rating = analyze_with_ai(program, platform, prompt_type="scope_update")

                if "FAILED" in ai_summary.upper() or "SKIPPED" in ai_summary.upper():
                    ai_fail += 1
                    LOG_BUFFER.append(f"❌ **AI FAIL (Scope)**: `{handle}`")
                else:
                    ai_success += 1

                scope_url = prog_url
                if platform == "HackerOne": scope_url = f"https://hackerone.com/{handle}/policy_scopes"
                elif platform == "Intigriti": scope_url = f"{prog_url}/scope"

                asset_list = []
                for nt in new_targets:
                    b_label = "💰" if nt["bounty"] else "📋"
                    asset_list.append(f"{b_label} `[{nt['type']}]` {nt['target']}")

                alert_msg = f"**Program:** {handle} ({platform})\n"
                alert_msg += f"**New Assets:**\n" + "\n".join(asset_list) + f"\n\n**Scope Link:** {scope_url}\n"
                alert_msg += f"\n--- AI IMPACT ANALYSIS ---\n{ai_summary}\n"
                
                send_discord_alert(alert_msg, SCOPE_WEBHOOK_URL, title="🛰️ Scope Expansion Detected!")
                state["programs"][unique_id]["targets"] = current_targets
                scope_updates_found += 1

    if is_initial_run or is_seed_run:
        save_state(state)
        print("State seeded successfully.")
    else:
        # Final Consolidated Log Summary
        summary = f"✅ **Scan Finished** | New: {new_programs_found} | Scope: {scope_updates_found} | AI: {ai_success} OK, {ai_fail} FAIL"
        print(summary)
        
        # Save state at the end of every scan to persist last_seen updates
        save_state(state)

        full_log = [f"📡 **Scan Summary** - {time.strftime('%H:%M:%S')}"]
        
        # Robust log truncation: ensure summary is always visible
        # Discord limit is 2000, we aim for ~1900 to be safe
        current_len = len(full_log[0]) + len(summary) + 10
        for entry in LOG_BUFFER:
            if current_len + len(entry) + 5 < 1900:
                full_log.append(entry)
                current_len += len(entry) + 1
            else:
                full_log.append("... (Log Truncated)")
                break
        
        full_log.append(summary)
        send_log_alert("\n".join(full_log))

if __name__ == "__main__":
    main()
