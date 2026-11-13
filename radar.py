import os
import json
import requests
from google import genai
from google.genai import types
import sys
import re
import time
from dotenv import load_dotenv
from config import NEW_PROGRAM_PROMPT, SCOPE_UPDATE_PROMPT, DATA_SOURCES, CHAOS_URL, STATE_FILE, MIN_RATING

# Load environment variables
load_dotenv()

# ── Secrets ────────────────────────────────────────────────────────────
_raw_keys = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
GEMINI_API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]
GEMINI_KEY_INDEX = 0

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SCOPE_WEBHOOK_URL = os.getenv("SCOPE_WEBHOOK_URL")
LOG_WEBHOOK_URL = os.getenv("LOG_WEBHOOK_URL")
WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE")
WHATSAPP_API_KEY = os.getenv("WHATSAPP_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

_PROVIDERS = None
PROVIDER_INDEX = 0
DISABLED_PROVIDERS: set[str] = set()
PROVIDER_STATS: dict = {}
LOG_BUFFER = []


def get_providers() -> list[str]:
    global _PROVIDERS
    if _PROVIDERS is None:
        _PROVIDERS = []
        if GEMINI_API_KEYS:
            _PROVIDERS.append("gemini")
        if DEEPSEEK_API_KEY:
            _PROVIDERS.append("deepseek")
        if GROQ_API_KEY:
            _PROVIDERS.append("groq")
        if OPENROUTER_API_KEY:
            _PROVIDERS.append("openrouter")
    return [provider for provider in _PROVIDERS if provider not in DISABLED_PROVIDERS]


def _call_openai_compat(url: str, api_key: str, model: str, messages: list, timeout: int = 15) -> str | None:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.7, "max_tokens": 1000}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        data = response.json()
        if response.status_code != 200:
            error = data.get("error", {}).get("message", "Unknown Error")
            print(f"    [!] HTTP {response.status_code}: {error}")
            return None
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        print(f"    [!] Request failed: {exc}")
        return None


def _try_gemini(full_prompt: str) -> str | None:
    global GEMINI_KEY_INDEX
    if not GEMINI_API_KEYS:
        return None
    keys_ordered = GEMINI_API_KEYS[GEMINI_KEY_INDEX:] + GEMINI_API_KEYS[:GEMINI_KEY_INDEX]
    for api_key in keys_ordered:
        GEMINI_KEY_INDEX = GEMINI_API_KEYS.index(api_key)
        try:
            client = genai.Client(api_key=api_key)
            print(f"    Gemini/gemini-2.0-flash | key ...{api_key[-5:]}")
            response = client.models.generate_content(model="gemini-2.0-flash", contents=full_prompt)
            return response.text
        except Exception as exc:
            error = str(exc)
            upper_error = error.upper()
            if "401" in upper_error or "UNAUTHENTICATED" in upper_error or "ACCOUNT_STATE_INVALID" in upper_error:
                print(f"    [!] Gemini key ...{api_key[-5:]} invalid/disabled (401). Skipping key.")
            elif "429" in upper_error or "RESOURCE_EXHAUSTED" in upper_error:
                print(f"    [!] Gemini key ...{api_key[-5:]} rate-limited (429). Rotating key...")
            else:
                print(f"    [!] Gemini error (key ...{api_key[-5:]}): {error.split(chr(10))[0][:120]}")
    return None


def _try_deepseek(messages: list) -> str | None:
    if not DEEPSEEK_API_KEY:
        return None
    print("    DeepSeek/deepseek-chat")
    return _call_openai_compat("https://api.deepseek.com/chat/completions", DEEPSEEK_API_KEY, "deepseek-chat", messages)


def _try_groq(messages: list) -> str | None:
    if not GROQ_API_KEY:
        return None
    print("    Groq/llama-3.3-70b-versatile")
    return _call_openai_compat("https://api.groq.com/openai/v1/chat/completions", GROQ_API_KEY, "llama-3.3-70b-versatile", messages)


def _try_openrouter(messages: list) -> str | None:
    if not OPENROUTER_API_KEY:
        return None
    for model in ("openrouter/free", "meta-llama/llama-3.3-70b-instruct:free"):
        print(f"    OpenRouter/{model}")
        result = _call_openai_compat("https://openrouter.ai/api/v1/chat/completions", OPENROUTER_API_KEY, model, messages, timeout=20)
        if result:
            return result
    return None


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"programs": {}}
    with open(STATE_FILE, "r") as state_file:
        try:
            data = json.load(state_file)
        except json.JSONDecodeError:
            return {"programs": {}}
    if isinstance(data.get("notified_handles"), list):
        print("Migrating legacy state format...")
        now = time.time()
        return {"programs": {entry: {"targets": [], "last_seen": now} for entry in data["notified_handles"]}}
    if "programs" in data:
        modified = False
        now = time.time()
        for uid, value in data["programs"].items():
            if isinstance(value, list):
                data["programs"][uid] = {"targets": value, "first_seen": now, "last_seen": now}
                modified = True
            elif isinstance(value, dict) and "first_seen" not in value:
                value["first_seen"] = value.get("last_seen", now)
                modified = True
        if modified:
            print("Migrated program entries to include 'first_seen' timestamp.")
    return data


def save_state(state):
    with open(STATE_FILE, "w") as state_file:
        json.dump(state, state_file, indent=2)


def _normalize_domain(value):
    """Normalize domain/URL representations used by feed deduplication."""
    raw = str(value).strip().lower()
    raw = re.sub(r"^[a-z][a-z0-9+.-]*://", "", raw)
    raw = raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    raw = raw.lstrip("*.").rstrip(".")
    return raw


def build_known_domains(state: dict) -> set[str]:
    known = set()
    for entry in state.get("programs", {}).values():
        for target in entry.get("targets", []):
            raw = target.get("target", "") if isinstance(target, dict) else target
            normalized = _normalize_domain(raw)
            if normalized:
                known.add(normalized)
    return known


def fetch_programs(platform, url):
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            if attempt < 2:
                print(f"  Warning: Fetching {platform} failed (Attempt {attempt + 1}/3). Retrying in 5s...")
                time.sleep(5)
            else:
                print(f"Error fetching {platform} data: {exc}")
                return []


def fetch_chaos_programs(state: dict) -> list[dict]:
    print("Checking Chaos (ProjectDiscovery)...")
    raw = fetch_programs("Chaos", CHAOS_URL)
    if not isinstance(raw, dict):
        return []
    programs = raw.get("programs", [])
    known_domains = build_known_domains(state)
    known_names = {uid.split(":", 1)[1].lower() for uid in state.get("programs", {}) if ":" in uid}
    new_programs = []
    for program in programs:
        name = str(program.get("name", "")).strip()
        domains = [_normalize_domain(domain) for domain in program.get("domains", []) if domain]
        domains = [domain for domain in domains if domain]
        if not name or not domains or name.lower() in known_names:
            continue
        # Domain equality and parent/child overlap prevent duplicate Chaos alerts.
        if any(domain in known_domains or any(known == domain or known.endswith("." + domain) or domain.endswith("." + known) for known in known_domains) for domain in domains):
            continue
        new_programs.append(program)
    print(f"  Chaos: {len(programs)} total, {len(new_programs)} genuinely new (post-dedup)")
    return new_programs


def get_program_handle_and_url(program: dict, platform: str) -> tuple[str, str]:
    if platform == "Intigriti":
        company = program.get("company_handle")
        raw_handle = program.get("handle") or program.get("name") or ""
        handle = f"{company}/{raw_handle}" if company else raw_handle
    else:
        handle = program.get("handle") or program.get("name") or ""

    prog_url = program.get("url") or "Check platform link"
    if platform == "Intigriti" and prog_url.startswith("https://www.intigriti.com/"):
        prog_url = prog_url.replace("https://www.intigriti.com/", "https://app.intigriti.com/")

    return handle, prog_url


def extract_targets(program, platform):
    targets = []
    try:
        if platform == "Chaos":
            for domain in program.get("domains", []):
                if domain:
                    targets.append({"target": str(domain).strip(), "type": "Domain", "bounty": bool(program.get("bounty", False)), "severity": "Unknown"})
            return targets
        raw_list = []
        if isinstance(program.get("targets"), dict):
            raw_list = program["targets"].get("in_scope", [])
        elif isinstance(program.get("targets"), list):
            raw_list = program["targets"]
        for item in raw_list:
            if not isinstance(item, dict):
                targets.append({"target": str(item), "type": "Other", "bounty": True})
                continue
            target_str = item.get("asset_identifier") or item.get("target") or item.get("endpoint") or ""
            if not target_str:
                continue
            asset_type = item.get("asset_type") or item.get("type") or "Other"
            bounty = item.get("eligible_for_bounty")
            if bounty is None:
                bounty = item.get("offers_awards")
            if bounty is None:
                bounty = True
            severity = item.get("max_severity") or item.get("impact") or "Unknown"
            targets.append({"target": str(target_str).strip(), "type": str(asset_type).title(), "bounty": bool(bounty), "severity": str(severity)})
    except Exception as exc:
        print(f"  Warning: Target extraction failed for {platform}: {exc}")
    unique = []
    seen = set()
    for target in targets:
        key = _normalize_domain(target["target"])
        if key and key not in seen:
            target["target"] = key if target.get("type") == "Domain" else target["target"]
            unique.append(target)
            seen.add(key)
    return unique


def stamp_targets(current_targets, old_targets, ts_new, ts_existing=None):
    old_map = {}
    for target in old_targets:
        if isinstance(target, dict):
            key = target.get("target", "")
            if key:
                old_map[key] = target.get("first_added")
    stamped = []
    for target in current_targets:
        target = dict(target)
        key = target.get("target", "")
        if key in old_map:
            existing_ts = old_map[key]
            target["first_added"] = existing_ts if existing_ts is not None else (ts_existing or ts_new)
        else:
            target["first_added"] = ts_new
        stamped.append(target)
    return stamped


def extract_rating(ai_text):
    match = re.search(r"RATING:\s*(\d+)/10", ai_text, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def analyze_with_ai(program, platform, prompt_type="new_program"):
    global PROVIDER_INDEX, PROVIDER_STATS
    if prompt_type == "scope_update":
        base_prompt = SCOPE_UPDATE_PROMPT
        context = f"Analyze these SPECIFIC new assets: {program.get('_scope_update_context', 'New assets added.')}"
    else:
        base_prompt = NEW_PROGRAM_PROMPT
        context = "New bug bounty program. Perform surface level research."
    full_prompt = f"{base_prompt}\n\nCONTEXT: {context}\n\nProgram Data from {platform}:\n{json.dumps(program, indent=2)}"
    messages = [
        {"role": "system", "content": "You are a professional bug bounty scout. Return concise, actionable intel."},
        {"role": "user", "content": full_prompt},
    ]
    providers = get_providers()
    if not providers:
        return "AI analysis failed (no API keys configured).", 0, "none"
    count = len(providers)
    for index in [(PROVIDER_INDEX + i) % count for i in range(count)]:
        provider = providers[index]
        print(f"  [AI] Trying provider: {provider} | Platform: {platform}")
        if provider == "gemini":
            result = _try_gemini(full_prompt)
        elif provider == "deepseek":
            result = _try_deepseek(messages)
        elif provider == "groq":
            result = _try_groq(messages)
        else:
            result = _try_openrouter(messages)
        stats = PROVIDER_STATS.setdefault(provider, {"ok": 0, "fail": 0})
        if result:
            PROVIDER_INDEX = (index + 1) % count
            stats["ok"] += 1
            return result, extract_rating(result), provider
        stats["fail"] += 1
        DISABLED_PROVIDERS.add(provider)
        print(f"  [!] {provider} failed. Disabled for remaining targets in this run. Rotating...")
    return "AI analysis failed (all providers exhausted).", 0, "none"


def _webhook_urls(value):
    if not value:
        return []
    raw = str(value).replace(",", "|")
    return [url.strip() for url in raw.split("|") if url.strip()]


def send_discord_alert(message, webhook_url=None, title="BugBountyRadar Info", use_embed=True):
    urls = _webhook_urls(webhook_url if webhook_url is not None else DISCORD_WEBHOOK_URL)
    if not urls:
        return
    if use_embed:
        description = message if len(message) <= 3900 else message[:3850] + "\n\n... (Truncated)"
        payload = {"embeds": [{"title": title, "description": description, "color": 0x5865F2, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}]}
    else:
        content = message if len(message) <= 1950 else message[:1900] + "\n\n... (Truncated)"
        payload = {"content": f"🚀 **{title}**\n{content}"}
    for url in urls:
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code >= 400:
                print(f"  [!] Discord Error {response.status_code}: {response.text}")
        except Exception as exc:
            print(f"Error sending Discord alert: {exc}")


def send_log_alert(message):
    content = message if len(message) <= 1950 else message[:1900] + "\n\n... (Log Truncated)"
    payload = {"content": f"📅 **{time.strftime('%Y-%m-%d %H:%M:%S')}**\n{content}"}
    for url in _webhook_urls(LOG_WEBHOOK_URL):
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as exc:
            print(f"Error sending log alert: {exc}")


def send_whatsapp_alert(message):
    if not (WHATSAPP_PHONE and WHATSAPP_API_KEY):
        if "--test" in sys.argv:
            print("  [!] WhatsApp skipped (No WHATSAPP_PHONE or WHATSAPP_API_KEY found)")
        return
    try:
        requests.get("https://api.callmebot.com/whatsapp.php", params={"phone": WHATSAPP_PHONE, "text": message, "apikey": WHATSAPP_API_KEY}, timeout=10)
    except Exception as exc:
        print(f"Error sending WhatsApp alert: {exc}")


def run_test():
    print("Running LIVE notification test for all 3 webhooks...")
    send_log_alert("🚀 **BugBountyRadar TEST RUN**\nStatus: Healthy\nPlatforms: 5 + Chaos\nAI Status: Round-Robin Active")
    for platform, url in DATA_SOURCES.items():
        print(f"  Testing {platform}...")
        programs = fetch_programs(platform, url)
        if not programs:
            continue
        latest = programs[0]
        handle, prog_url = get_program_handle_and_url(latest, platform)
        if not handle:
            continue
        ai_summary, rating, provider = analyze_with_ai(latest, platform)
        test_msg = f"🧪 **BugBountyRadar LIVE TEST (New Program)**\n**Platform:** {platform}\n**Program:** {handle} (Rating: {rating}/10) | AI: {provider}\n**Link:** {prog_url}\n\n--- AI SUMMARY ---\n{ai_summary}\n"
        send_discord_alert(test_msg, DISCORD_WEBHOOK_URL)
        update_msg = f"🧪 **BugBountyRadar LIVE TEST (Scope Expansion)**\n**Program:** {handle} ({platform})\n**New Assets:** `[WEB] test-domain.com`, `[APK] com.test.app`\n**Link:** {prog_url}\n"
        send_discord_alert(update_msg, SCOPE_WEBHOOK_URL)
        break
    print("\nTest finished. Check all 3 Discord channels!")


def main():
    if "--test" in sys.argv:
        run_test()
        return
    is_seed_run = "--seed" in sys.argv
    state = load_state()
    programs_state = state.setdefault("programs", {})
    new_programs_found = 0
    scope_updates_found = 0
    ai_success = 0
    ai_fail = 0
    is_initial_run = len(programs_state) == 0
    if is_seed_run:
        print("\n--- BugBountyRadar SEED MODE (Silent) ---")
    else:
        print(f"\n--- BugBountyRadar Check Started: {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
        providers = get_providers()
        print(f"  AI providers active: {', '.join(providers) if providers else 'NONE'}")
    now = time.time()
    reopen_threshold = 3600 * 24
    alert_threshold = 3600 * 24 * 7

    for platform, url in DATA_SOURCES.items():
        print(f"Checking {platform}...")
        programs = fetch_programs(platform, url)
        for program in programs:
            handle, prog_url = get_program_handle_and_url(program, platform)
            if not handle:
                continue
            unique_id = f"{platform}:{handle}"
            # Backward-compatibility fallback for Intigriti if stored under legacy un-scoped handle
            if platform == "Intigriti" and unique_id not in programs_state:
                raw_handle = program.get("handle") or program.get("name")
                legacy_id = f"{platform}:{raw_handle}"
                if legacy_id in programs_state:
                    if raw_handle != "vdp":
                        programs_state[unique_id] = programs_state.pop(legacy_id)
                    elif program.get("company_handle") == "showpad":
                        programs_state[unique_id] = programs_state.pop(legacy_id)
            current_targets = extract_targets(program, platform)
            if is_initial_run or is_seed_run:
                old_entry = programs_state.get(unique_id, {})
                old_last_seen = old_entry.get("last_seen", now)
                old_first_seen = old_entry.get("first_seen", old_last_seen)
                stamped = stamp_targets(current_targets, old_entry.get("targets", []), old_first_seen, old_first_seen)
                programs_state[unique_id] = {"targets": stamped, "first_seen": old_first_seen, "last_seen": old_last_seen}
                continue
            if unique_id not in programs_state:
                print(f"New program found: {handle} on {platform}")
                is_new = True
                should_alert_new = True
                old_data = []
                prev_last_seen = now
                first_seen_ts = now
            else:
                is_new = False
                entry = programs_state[unique_id]
                first_seen_ts = entry.get("first_seen", entry.get("last_seen", now))
                prev_last_seen = entry.get("last_seen", 0)
                time_since = now - prev_last_seen
                old_data = entry.get("targets", [])
                if time_since > reopen_threshold:
                    days_since = int(time_since // 86400)
                    time_desc = f"{days_since} days" if days_since > 0 else f"{int(time_since // 3600)} hours"
                    entry.setdefault("reopen_history", []).append({"closed_at": int(prev_last_seen), "reopened_at": int(now)})
                    if time_since > alert_threshold:
                        print(f"Program REOPENED (Long absence: {time_desc}): {handle}")
                        should_alert_new = True
                    else:
                        print(f"Program reopened ({time_desc}): {handle}")
                        LOG_BUFFER.append(f"🔄 **Reopened**: `{handle}` ({platform}) - Back after {time_desc}")
                        should_alert_new = False
                else:
                    should_alert_new = False
                entry["first_seen"] = first_seen_ts
                entry["last_seen"] = now
            if should_alert_new:
                alert_title = "New Bug Bounty Program!" if is_new else f"🛰️ Program Reopened! (After {int((now - prev_last_seen) // 86400)} days)"
                alert_msg = f"**Platform:** {platform}\n**Program:** {handle}\n**Link:** {prog_url}\n"
                send_discord_alert(alert_msg, DISCORD_WEBHOOK_URL, title=alert_title, use_embed=False)
                new_programs_found += 1
                old_entry = programs_state.get(unique_id, {})
                stamped = stamp_targets(current_targets, old_entry.get("targets", []), first_seen_ts)
                replacement = {"targets": stamped, "first_seen": first_seen_ts, "last_seen": now}
                if old_entry.get("reopen_history"):
                    replacement["reopen_history"] = old_entry["reopen_history"]
                programs_state[unique_id] = replacement
                continue
            old_keys = {_normalize_domain(t.get("target", "") if isinstance(t, dict) else t) for t in old_data}
            new_targets = [target for target in current_targets if _normalize_domain(target["target"]) not in old_keys]
            if new_targets:
                print(f"Scope update for {handle}: {len(new_targets)} new targets.")
                target_summary = ", ".join(f"[{target.get('type', 'Other')}] {target['target']}" for target in new_targets)
                program["_scope_update_context"] = target_summary
                scope_url = prog_url
                if platform == "HackerOne":
                    scope_url = f"https://hackerone.com/{handle}/policy_scopes"
                elif platform == "Intigriti":
                    scope_url = f"{prog_url}/scope"
                asset_list = [f"{'💰' if target.get('bounty') else '📋'} `[{target.get('type', 'Other')}]` {target['target']}" for target in new_targets]
                alert_msg = f"**Program:** {handle} ({platform})\n**New Assets:**\n" + "\n".join(asset_list) + f"\n\n**Scope Link:** {scope_url}\n"
                send_discord_alert(alert_msg, SCOPE_WEBHOOK_URL, title="🛰️ Scope Expansion Detected!")
                old_entry = programs_state[unique_id]
                old_entry["targets"] = stamp_targets(current_targets, old_entry.get("targets", []), now)
                scope_updates_found += 1

    if not (is_initial_run or is_seed_run):
        chaos_raw = fetch_programs("Chaos", CHAOS_URL)
        if isinstance(chaos_raw, dict):
            for program in chaos_raw.get("programs", []):
                name = str(program.get("name", "")).strip()
                uid = f"Chaos:{name}"
                if name and uid in programs_state:
                    programs_state[uid]["last_seen"] = now
        for program in fetch_chaos_programs(state):
            name = str(program.get("name", "")).strip()
            if not name:
                continue
            uid = f"Chaos:{name}"
            current_targets = extract_targets(program, "Chaos")
            bounty_label = "💰 Paid" if program.get("bounty", False) else "📋 VDP"
            alert_msg = f"**Source:** Chaos (External / Self-hosted)\n**Program:** {name} | {bounty_label}\n**Link:** {program.get('url') or 'Check program page'}\n"
            send_discord_alert(alert_msg, DISCORD_WEBHOOK_URL, title="New Bug Bounty Program!", use_embed=False)
            new_programs_found += 1
            old_entry = programs_state.get(uid, {})
            first_seen = old_entry.get("first_seen", now)
            programs_state[uid] = {"targets": stamp_targets(current_targets, old_entry.get("targets", []), first_seen), "first_seen": first_seen, "last_seen": now}
    elif is_seed_run:
        chaos_raw = fetch_programs("Chaos", CHAOS_URL)
        chaos_programs = chaos_raw.get("programs", []) if isinstance(chaos_raw, dict) else []
        for program in chaos_programs:
            name = str(program.get("name", "")).strip()
            if not name or not any(program.get("domains", [])):
                continue
            uid = f"Chaos:{name}"
            old_entry = programs_state.get(uid, {})
            old_last_seen = old_entry.get("last_seen", now)
            first_seen = old_entry.get("first_seen", old_last_seen)
            programs_state[uid] = {"targets": stamp_targets(extract_targets(program, "Chaos"), old_entry.get("targets", []), first_seen, first_seen), "first_seen": first_seen, "last_seen": old_last_seen}
        print(f"  Chaos: seeded {len(chaos_programs)} programs.")

    if is_initial_run or is_seed_run:
        save_state(state)
        print("State seeded successfully.")
    else:
        summary = f"✅ **Scan Finished** | New: {new_programs_found} | Scope: {scope_updates_found} | AI: {ai_success} OK, {ai_fail} FAIL"
        print(summary)
        save_state(state)
        providers = get_providers()
        if PROVIDER_STATS:
            provider_line = "🤖 **AI Providers:** " + " | ".join(f"{provider}: {PROVIDER_STATS.get(provider, {'ok': 0, 'fail': 0})['ok']}✓ {PROVIDER_STATS.get(provider, {'ok': 0, 'fail': 0})['fail']}✗" for provider in providers)
        else:
            provider_line = f"🤖 **AI Providers:** {', '.join(providers)} (none used)"
        full_log = [f"📡 **Scan Summary** - {time.strftime('%H:%M:%S')}"]
        current_len = len(full_log[0]) + len(summary) + len(provider_line) + 15
        for entry in LOG_BUFFER:
            if current_len + len(entry) + 5 < 1900:
                full_log.append(entry)
                current_len += len(entry) + 1
            else:
                full_log.append("... (Log Truncated)")
                break
        full_log.extend((provider_line, summary))
        send_log_alert("\n".join(full_log))


if __name__ == "__main__":
    main()
