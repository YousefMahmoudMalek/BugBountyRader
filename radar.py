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

# ── Secrets ──────────────────────────────────────────────────────────────────
_raw_keys = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
GEMINI_API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]
GEMINI_KEY_INDEX = 0  # Internal rotation index for the Gemini key pool

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SCOPE_WEBHOOK_URL   = os.getenv("SCOPE_WEBHOOK_URL")
LOG_WEBHOOK_URL     = os.getenv("LOG_WEBHOOK_URL")
WHATSAPP_PHONE      = os.getenv("WHATSAPP_PHONE")
WHATSAPP_API_KEY    = os.getenv("WHATSAPP_API_KEY")
DEEPSEEK_API_KEY    = os.getenv("DEEPSEEK_API_KEY")
GROQ_API_KEY        = os.getenv("GROQ_API_KEY")
OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY")

# ── AI provider round-robin state ─────────────────────────────────────────────
# Built lazily in get_providers(); a flat list of provider names that have keys.
_PROVIDERS = None
PROVIDER_INDEX = 0  # Index into _PROVIDERS, advances after each successful call
DISABLED_PROVIDERS: set[str] = set()  # Providers that failed fatally during this scan

# Per-run provider usage counters  {provider_name: {"ok": int, "fail": int}}
PROVIDER_STATS: dict = {}

# Global log buffer for session summary
LOG_BUFFER = []


# ── Provider helpers ──────────────────────────────────────────────────────────

def get_providers() -> list[str]:
    """Return the ordered list of available AI providers that have keys and haven't failed fatally."""
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
    return [p for p in _PROVIDERS if p not in DISABLED_PROVIDERS]


def _call_openai_compat(url: str, api_key: str, model: str, messages: list, timeout: int = 15) -> str | None:
    """Generic caller for OpenAI-compatible endpoints (DeepSeek, Groq, OpenRouter)."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1000,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        data = r.json()
        if r.status_code != 200:
            err = data.get("error", {}).get("message", "Unknown Error")
            print(f"    [!] HTTP {r.status_code}: {err}")
            return None
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"    [!] Request failed: {e}")
        return None


def _try_gemini(full_prompt: str) -> str | None:
    """Try the Gemini key pool. Returns text or None."""
    global GEMINI_KEY_INDEX
    if not GEMINI_API_KEYS:
        return None

    # Start from current key, rotate through all keys once
    keys_ordered = GEMINI_API_KEYS[GEMINI_KEY_INDEX:] + GEMINI_API_KEYS[:GEMINI_KEY_INDEX]

    for api_key in keys_ordered:
        GEMINI_KEY_INDEX = GEMINI_API_KEYS.index(api_key)
        try:
            client = genai.Client(api_key=api_key)
            print(f"    Gemini/gemini-2.0-flash | key ...{api_key[-5:]}")
            response = client.models.generate_content(
                model="gemini-2.0-flash", contents=full_prompt
            )
            return response.text
        except Exception as e:
            err_msg = str(e)
            upper_msg = err_msg.upper()
            if "401" in upper_msg or "UNAUTHENTICATED" in upper_msg or "ACCOUNT_STATE_INVALID" in upper_msg:
                print(f"    [!] Gemini key ...{api_key[-5:]} invalid/disabled (401). Skipping key.")
                continue
            elif "429" in upper_msg or "RESOURCE_EXHAUSTED" in upper_msg:
                print(f"    [!] Gemini key ...{api_key[-5:]} rate-limited (429). Rotating key...")
                continue
            else:
                first_line = err_msg.split("\n")[0][:120]
                print(f"    [!] Gemini error (key ...{api_key[-5:]}): {first_line}")
                continue
    return None


def _try_deepseek(messages: list) -> str | None:
    """DeepSeek chat API."""
    if not DEEPSEEK_API_KEY:
        return None
    print("    DeepSeek/deepseek-chat")
    return _call_openai_compat(
        url="https://api.deepseek.com/chat/completions",
        api_key=DEEPSEEK_API_KEY,
        model="deepseek-chat",
        messages=messages,
    )


def _try_groq(messages: list) -> str | None:
    """Groq API."""
    if not GROQ_API_KEY:
        return None
    print("    Groq/llama-3.3-70b-versatile")
    return _call_openai_compat(
        url="https://api.groq.com/openai/v1/chat/completions",
        api_key=GROQ_API_KEY,
        model="llama-3.3-70b-versatile",
        messages=messages,
    )


def _try_openrouter(messages: list) -> str | None:
    """OpenRouter — uses free-tier models."""
    if not OPENROUTER_API_KEY:
        return None
    openrouter_models = [
        "openrouter/free",
        "meta-llama/llama-3.3-70b-instruct:free",
    ]
    for model in openrouter_models:
        print(f"    OpenRouter/{model}")
        res = _call_openai_compat(
            url="https://openrouter.ai/api/v1/chat/completions",
            api_key=OPENROUTER_API_KEY,
            model=model,
            messages=messages,
            timeout=20,
        )
        if res:
            return res
    return None


# ── State helpers ─────────────────────────────────────────────────────────────

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

            # 2. Schema Migration: ensure first_seen and last_seen exist
            if "programs" in data:
                modified = False
                now = time.time()
                for uid, val in data["programs"].items():
                    if isinstance(val, list):
                        data["programs"][uid] = {"targets": val, "first_seen": now, "last_seen": now}
                        modified = True
                    elif isinstance(val, dict):
                        if "first_seen" not in val:
                            val["first_seen"] = val.get("last_seen", now)
                            modified = True
                if modified:
                    print("Migrated program entries to include 'first_seen' timestamp.")

            return data
    return {"programs": {}}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def build_known_domains(state: dict) -> set[str]:
    """
    Return a flat set of all domain/target strings currently tracked in state.
    Used for Chaos dedup — any program whose domains overlap this set is already
    being tracked via one of the 5 main platforms.
    """
    known: set[str] = set()
    for entry in state.get("programs", {}).values():
        targets = entry.get("targets", [])
        for t in targets:
            raw = t["target"] if isinstance(t, dict) else str(t)
            # Strip leading wildcards and protocols so "*.foo.com" → "foo.com"
            raw = raw.lstrip("*.").lower()
            raw = re.sub(r"^https?://", "", raw).rstrip("/")
            if raw:
                known.add(raw)
    return known


# ── Data fetching ─────────────────────────────────────────────────────────────

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


def fetch_chaos_programs(state: dict) -> list[dict]:
    """
    Fetch the Chaos feed and return only programs that are genuinely new —
    i.e. their domains don't overlap with anything already in state, AND
    they have at least one domain listed (empty-domain entries are skipped).
    """
    print("Checking Chaos (ProjectDiscovery)...")
    raw = fetch_programs("Chaos", CHAOS_URL)
    if not raw or not isinstance(raw, dict):
        return []

    programs = raw.get("programs", [])
    known_domains = build_known_domains(state)
    known_names = {uid.split(":", 1)[1].lower() for uid in state.get("programs", {})}

    new_programs = []
    for prog in programs:
        name = prog.get("name", "").strip()
        domains = [d.lower().strip() for d in prog.get("domains", []) if d.strip()]

        if not name:
            continue

        # Skip if program name is already known (case-insensitive)
        if name.lower() in known_names:
            continue

        # Skip entries with no domains — nothing to hunt and nothing to dedup against
        if not domains:
            continue

        # Skip if any domain already appears in our known-domains set
        overlap = any(d in known_domains for d in domains)
        if overlap:
            continue

        new_programs.append(prog)

    print(f"  Chaos: {len(programs)} total, {len(new_programs)} genuinely new (post-dedup)")
    return new_programs


# ── Target extraction ─────────────────────────────────────────────────────────

def extract_targets(program, platform):
    """Extracts a list of target dicts with metadata (type, bounty, etc)."""
    targets = []
    try:
        # Chaos format: flat list of domain strings under "domains"
        if platform == "Chaos":
            domains = program.get("domains", [])
            bounty = bool(program.get("bounty", False))
            for d in domains:
                if d:
                    targets.append({
                        "target": str(d).strip(),
                        "type": "Domain",
                        "bounty": bounty,
                        "severity": "Unknown",
                    })
            return targets

        # Standard platforms: targets -> in_scope
        raw_list = []
        if isinstance(program.get("targets"), dict):
            raw_list = program["targets"].get("in_scope", [])
        elif isinstance(program.get("targets"), list):
            raw_list = program["targets"]

        for t in raw_list:
            if not isinstance(t, dict):
                targets.append({"target": str(t), "type": "Other", "bounty": True})
                continue

            target_str = t.get("asset_identifier") or t.get("target") or t.get("endpoint") or ""
            if not target_str:
                continue

            asset_type = t.get("asset_type") or t.get("type") or "Other"
            bounty = t.get("eligible_for_bounty")
            if bounty is None:
                bounty = t.get("offers_awards")
            if bounty is None:
                bounty = True

            severity = t.get("max_severity") or t.get("impact") or "Unknown"

            targets.append({
                "target": str(target_str).strip(),
                "type": str(asset_type).title(),
                "bounty": bool(bounty),
                "severity": str(severity),
            })
    except Exception as e:
        print(f"  Warning: Target extraction failed for {platform}: {e}")

    # Deduplicate by target string
    seen = set()
    unique = []
    for t in targets:
        if t["target"] not in seen:
            unique.append(t)
            seen.add(t["target"])
    return unique


# ── AI analysis ───────────────────────────────────────────────────────────────

def extract_rating(ai_text):
    """Extracts X from 'RATING: X/10' format."""
    match = re.search(r"RATING:\s*(\d+)/10", ai_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


def analyze_with_ai(program, platform, prompt_type="new_program"):
    """
    Round-robin across all configured AI providers.
    Tries each provider once (with a 15s timeout); rotates to the next on failure.
    Only returns failure text if every provider was tried and none responded.
    Returns (ai_text, rating, provider_used).
    """
    global PROVIDER_INDEX, PROVIDER_STATS

    # Build prompt
    if prompt_type == "scope_update":
        base_prompt = SCOPE_UPDATE_PROMPT
        ctx = program.get("_scope_update_context", "New assets added.")
        context = f"Analyze these SPECIFIC new assets: {ctx}"
    else:
        base_prompt = NEW_PROGRAM_PROMPT
        context = "New bug bounty program. Perform surface level research."

    full_prompt = (
        f"{base_prompt}\n\nCONTEXT: {context}\n\n"
        f"Program Data from {platform}:\n{json.dumps(program, indent=2)}"
    )
    messages = [
        {"role": "system", "content": "You are a professional bug bounty scout. Return concise, actionable intel."},
        {"role": "user",   "content": full_prompt},
    ]

    providers = get_providers()
    if not providers:
        return "AI analysis failed (no API keys configured).", 0, "none"

    n = len(providers)
    # Start from the current round-robin position
    order = [(PROVIDER_INDEX + i) % n for i in range(n)]

    for idx in order:
        provider = providers[idx]
        print(f"  [AI] Trying provider: {provider} | Platform: {platform}")

        result = None
        if provider == "gemini":
            result = _try_gemini(full_prompt)
        elif provider == "deepseek":
            result = _try_deepseek(messages)
        elif provider == "groq":
            result = _try_groq(messages)
        elif provider == "openrouter":
            result = _try_openrouter(messages)

        if result:
            # Advance the global pointer so the next call starts on the next provider
            PROVIDER_INDEX = (idx + 1) % n
            # Track stats
            PROVIDER_STATS.setdefault(provider, {"ok": 0, "fail": 0})
            PROVIDER_STATS[provider]["ok"] += 1
            return result, extract_rating(result), provider
        else:
            PROVIDER_STATS.setdefault(provider, {"ok": 0, "fail": 0})
            PROVIDER_STATS[provider]["fail"] += 1
            DISABLED_PROVIDERS.add(provider)
            print(f"  [!] {provider} failed. Disabled for remaining targets in this run. Rotating...")

    return "AI analysis failed (all providers exhausted).", 0, "none"


# ── Alerting ──────────────────────────────────────────────────────────────────

def send_discord_alert(message, webhook_url=None, title="BugBountyRadar Info", use_embed=True):
    url = webhook_url or DISCORD_WEBHOOK_URL
    if not url:
        return

    if use_embed:
        desc = message
        if len(desc) > 3900:
            desc = desc[:3850] + "\n\n... (Truncated)"
        payload = {
            "embeds": [{
                "title": title,
                "description": desc,
                "color": 0x5865F2,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }]
        }
    else:
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
        "apikey": WHATSAPP_API_KEY,
    }
    try:
        requests.get(url, params=params, timeout=10)
    except Exception as e:
        print(f"Error sending WhatsApp alert: {e}")


# ── Test run ──────────────────────────────────────────────────────────────────

def run_test():
    print("Running LIVE notification test for all 3 webhooks...")

    print(f"  [Test 1/3] Sending Log alert to Webhook #3...")
    send_log_alert("🚀 **BugBountyRadar TEST RUN**\nStatus: Healthy\nPlatforms: 5 + Chaos\nAI Status: Round-Robin Active")

    for platform, url in DATA_SOURCES.items():
        print(f"  Testing {platform}...")
        programs = fetch_programs(platform, url)
        if not programs:
            continue

        latest = programs[0]
        handle = latest.get("handle") or latest.get("name")
        prog_url = latest.get("url") or "Check platform for link"

        print(f"  [Test 2/3] Sending New Program alert to Webhook #1...")
        ai_summary, rating, provider = analyze_with_ai(latest, platform)

        test_msg = f"🧪 **BugBountyRadar LIVE TEST (New Program)**\n"
        test_msg += f"**Platform:** {platform}\n"
        test_msg += f"**Program:** {handle} (Rating: {rating}/10) | AI: {provider}\n"
        test_msg += f"**Link:** {prog_url}\n"
        test_msg += f"\n--- AI SUMMARY ---\n{ai_summary}\n"
        send_discord_alert(test_msg, DISCORD_WEBHOOK_URL)

        print(f"  [Test 3/3] Sending Scope Expansion alert to Webhook #2...")
        update_msg = f"🧪 **BugBountyRadar LIVE TEST (Scope Expansion)**\n"
        update_msg += f"**Program:** {handle} ({platform})\n"
        update_msg += f"**New Assets:** `[WEB] test-domain.com`, `[APK] com.test.app` \n"
        update_msg += f"**Link:** {prog_url}\n"
        send_discord_alert(update_msg, SCOPE_WEBHOOK_URL)
        break

    print("\nTest finished. Check all 3 Discord channels!")


# ── Main ──────────────────────────────────────────────────────────────────────

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
        providers = get_providers()
        print(f"  AI providers active: {', '.join(providers) if providers else 'NONE'}")

    now = time.time()
    REOPEN_THRESHOLD = 3600 * 6       # 6 hours
    ALERT_THRESHOLD  = 3600 * 24 * 7  # 7 days

    # ── 5 Main Platform Loop ──────────────────────────────────────────────────
    for platform, url in DATA_SOURCES.items():
        print(f"Checking {platform}...")
        programs = fetch_programs(platform, url)

        for program in programs:
            handle   = program.get("handle") or program.get("name")
            prog_url = program.get("url") or "Check platform link"
            if not handle:
                continue

            unique_id       = f"{platform}:{handle}"
            current_targets = extract_targets(program, platform)

            # Initial Run / Seed Mode
            if is_initial_run or is_seed_run:
                old_last_seen = state.get("programs", {}).get(unique_id, {}).get("last_seen", now)
                old_first_seen = state.get("programs", {}).get(unique_id, {}).get("first_seen", old_last_seen)
                state["programs"][unique_id] = {"targets": current_targets, "first_seen": old_first_seen, "last_seen": old_last_seen}
                continue

            # Case: New Program or Reopened after long time
            if unique_id not in state["programs"]:
                print(f"New program found: {handle} on {platform}")
                is_new         = True
                should_alert_new = True
                old_data       = []
                prev_last_seen = now
                first_seen_ts  = now
            else:
                is_new         = False
                entry          = state["programs"][unique_id]
                first_seen_ts  = entry.get("first_seen", entry.get("last_seen", now))
                prev_last_seen = entry.get("last_seen", 0)
                time_since     = now - prev_last_seen
                old_data       = entry.get("targets", [])

                if time_since > REOPEN_THRESHOLD:
                    days_since = int(time_since // (3600 * 24))
                    time_desc  = f"{days_since} days" if days_since > 0 else f"{int(time_since // 3600)} hours"

                    if time_since > ALERT_THRESHOLD:
                        print(f"Program REOPENED (Long absence: {time_desc}): {handle}")
                        should_alert_new = True
                    else:
                        print(f"Program reopened ({time_desc}): {handle}")
                        LOG_BUFFER.append(f"🔄 **Reopened**: `{handle}` ({platform}) - Back after {time_desc}")
                        should_alert_new = False
                else:
                    should_alert_new = False

                state["programs"][unique_id]["first_seen"] = first_seen_ts
                state["programs"][unique_id]["last_seen"] = now

            # Trigger "New Program" alert
            if should_alert_new:
                ai_summary, rating, provider = analyze_with_ai(program, platform)

                if "FAILED" in ai_summary.upper() or "SKIPPED" in ai_summary.upper():
                    rating = 10
                    ai_fail += 1
                    LOG_BUFFER.append(f"❌ **AI FAIL**: `{handle}` ({platform})")
                else:
                    ai_success += 1

                if rating >= MIN_RATING:
                    alert_title = "New Bug Bounty Program!"
                    if not is_new:
                        days = int((now - prev_last_seen) // (3600 * 24))
                        alert_title = f"🛰️ Program Reopened! (After {days} days)"

                    alert_msg  = f"**Platform:** {platform}\n"
                    alert_msg += f"**Program:** {handle}\n"
                    alert_msg += f"**Link:** {prog_url}\n"
                    alert_msg += f"**AI:** {provider} | Rating: {rating}/10\n"
                    alert_msg += f"\n--- AI SUMMARY ---\n{ai_summary}\n"
                    send_discord_alert(alert_msg, DISCORD_WEBHOOK_URL, title=alert_title, use_embed=False)
                    new_programs_found += 1

                state["programs"][unique_id] = {"targets": current_targets, "first_seen": first_seen_ts, "last_seen": now}
                continue

            # Case: Scope Update
            old_targets_strs = [t["target"] if isinstance(t, dict) else t for t in old_data]
            new_targets = [t for t in current_targets if t["target"] not in old_targets_strs]

            if new_targets:
                print(f"Scope update for {handle}: {len(new_targets)} new targets. Analyzing...")
                target_summary = ", ".join([f"[{t['type']}] {t['target']}" for t in new_targets])
                program["_scope_update_context"] = target_summary
                ai_summary, rating, provider = analyze_with_ai(program, platform, prompt_type="scope_update")

                if "FAILED" in ai_summary.upper() or "SKIPPED" in ai_summary.upper():
                    ai_fail += 1
                    LOG_BUFFER.append(f"❌ **AI FAIL (Scope)**: `{handle}`")
                else:
                    ai_success += 1

                scope_url = prog_url
                if platform == "HackerOne":
                    scope_url = f"https://hackerone.com/{handle}/policy_scopes"
                elif platform == "Intigriti":
                    scope_url = f"{prog_url}/scope"

                asset_list = []
                for nt in new_targets:
                    b_label = "💰" if nt["bounty"] else "📋"
                    asset_list.append(f"{b_label} `[{nt['type']}]` {nt['target']}")

                alert_msg  = f"**Program:** {handle} ({platform})\n"
                alert_msg += f"**New Assets:**\n" + "\n".join(asset_list) + f"\n\n**Scope Link:** {scope_url}\n"
                alert_msg += f"\n--- AI IMPACT ANALYSIS ---\n{ai_summary}\n"

                send_discord_alert(alert_msg, SCOPE_WEBHOOK_URL, title="🛰️ Scope Expansion Detected!")
                state["programs"][unique_id]["targets"] = current_targets
                scope_updates_found += 1

    # ── Chaos Processing Block ────────────────────────────────────────────────
    if not (is_initial_run or is_seed_run):
        # Update last_seen for all currently active Chaos programs
        chaos_raw = fetch_programs("Chaos", CHAOS_URL)
        if chaos_raw and isinstance(chaos_raw, dict):
            for p in chaos_raw.get("programs", []):
                name = p.get("name", "").strip()
                if name:
                    uid = f"Chaos:{name}"
                    if uid in state["programs"]:
                        state["programs"][uid]["last_seen"] = now

        chaos_new = fetch_chaos_programs(state)
        for program in chaos_new:
            name     = program.get("name", "").strip()
            prog_url = program.get("url") or "Check program page"
            bounty   = program.get("bounty", False)

            unique_id       = f"Chaos:{name}"
            current_targets = extract_targets(program, "Chaos")

            print(f"New Chaos program (external): {name}")
            ai_summary, rating, provider = analyze_with_ai(program, "Chaos (External)")

            if "FAILED" in ai_summary.upper() or "SKIPPED" in ai_summary.upper():
                rating = 10
                ai_fail += 1
                LOG_BUFFER.append(f"❌ **AI FAIL**: `{name}` (Chaos)")
            else:
                ai_success += 1

            if rating >= MIN_RATING:
                bounty_label = "💰 Paid" if bounty else "📋 VDP"
                alert_msg  = f"**Source:** Chaos (External / Self-hosted)\n"
                alert_msg += f"**Program:** {name} | {bounty_label}\n"
                alert_msg += f"**Link:** {prog_url}\n"
                alert_msg += f"**AI:** {provider} | Rating: {rating}/10\n"
                alert_msg += f"\n--- AI SUMMARY ---\n{ai_summary}\n"
                send_discord_alert(alert_msg, DISCORD_WEBHOOK_URL, title="New Bug Bounty Program!", use_embed=False)
                new_programs_found += 1

            old_first_seen = state.get("programs", {}).get(unique_id, {}).get("first_seen", now)
            state["programs"][unique_id] = {"targets": current_targets, "first_seen": old_first_seen, "last_seen": now}

    elif is_seed_run:
        # Seed Chaos too — record all so they're not re-alerted on first live run
        chaos_raw = fetch_programs("Chaos", CHAOS_URL)
        chaos_programs = chaos_raw.get("programs", []) if isinstance(chaos_raw, dict) else []
        for prog in chaos_programs:
            name    = prog.get("name", "").strip()
            domains = [d for d in prog.get("domains", []) if d]
            if not name or not domains:
                continue
            unique_id = f"Chaos:{name}"
            current_targets = extract_targets(prog, "Chaos")
            old_last_seen = state.get("programs", {}).get(unique_id, {}).get("last_seen", now)
            old_first_seen = state.get("programs", {}).get(unique_id, {}).get("first_seen", old_last_seen)
            state["programs"][unique_id] = {"targets": current_targets, "first_seen": old_first_seen, "last_seen": old_last_seen}
        print(f"  Chaos: seeded {len(chaos_programs)} programs.")

    # ── Wrap-up ───────────────────────────────────────────────────────────────
    if is_initial_run or is_seed_run:
        save_state(state)
        print("State seeded successfully.")
    else:
        summary = (
            f"✅ **Scan Finished** | New: {new_programs_found} | Scope: {scope_updates_found} | "
            f"AI: {ai_success} OK, {ai_fail} FAIL"
        )
        print(summary)
        save_state(state)

        # Build provider stats line
        providers = get_providers()
        if PROVIDER_STATS:
            parts = []
            for p in providers:
                s = PROVIDER_STATS.get(p, {"ok": 0, "fail": 0})
                parts.append(f"{p}: {s['ok']}✓ {s['fail']}✗")
            provider_line = "🤖 **AI Providers:** " + " | ".join(parts)
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

        full_log.append(provider_line)
        full_log.append(summary)
        send_log_alert("\n".join(full_log))


if __name__ == "__main__":
    main()
