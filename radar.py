"""Run radar with canonical target comparison and Discord webhook fan-out.

The upstream scanner is kept intact; these wrappers adapt its boundaries so
feed formatting differences don't look like scope changes and each notification
channel can broadcast to its own list of webhook URLs.
"""

import ipaddress
import re
from urllib.parse import urlsplit

import radar

WEBHOOK_SEPARATOR = "|"

_primary_webhooks = radar.DISCORD_WEBHOOK_URL
_scope_webhooks = radar.SCOPE_WEBHOOK_URL
_log_webhooks = radar.LOG_WEBHOOK_URL
_original_extract_targets = radar.extract_targets
_original_load_state = radar.load_state
_original_save_state = radar.save_state


def _webhook_urls(value):
    if not value:
        return []
    return [part.strip() for part in str(value).split(WEBHOOK_SEPARATOR) if part.strip()]


def _canonical_target(value):
    """Normalize domain/URL formatting, but leave labels and IDs untouched."""
    raw = str(value).strip()
    if not raw:
        return raw

    candidate = raw if "://" in raw else f"//{raw}"
    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return raw

    if not host:
        return raw

    wildcard = host.startswith("*.")
    host_body = host[2:] if wildcard else host
    host_body = host_body.rstrip(".").lower()

    try:
        ipaddress.ip_address(host_body)
        valid_host = True
    except ValueError:
        valid_host = bool(re.fullmatch(
            r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?",
            host_body,
            re.IGNORECASE,
        ))

    if not valid_host:
        return raw

    normalized_host = ("*." if wildcard else "") + host_body
    if ":" in host_body and not host_body.startswith("["):
        normalized_host = f"[{normalized_host}]"
    netloc = normalized_host + (f":{port}" if port is not None else "")
    path = parsed.path.rstrip("/")
    suffix = ("?" + parsed.query if parsed.query else "") + ("#" + parsed.fragment if parsed.fragment else "")
    return netloc + path + suffix


def _normalize_target_list(targets):
    """Normalize target keys while preserving each target's other metadata."""
    normalized = []
    seen = set()
    for item in targets:
        target = dict(item)
        target["target"] = _canonical_target(target.get("target", ""))
        key = target["target"]
        if key and key not in seen:
            seen.add(key)
            normalized.append(target)
    return normalized


def _load_state():
    """Normalize URL/domain targets in memory, retaining original stored labels."""
    state = _original_load_state()
    originals = {}
    for uid, entry in state.get("programs", {}).items():
        if not isinstance(entry, dict):
            continue
        original_targets = entry.get("targets", [])
        originals[uid] = {}
        normalized = []
        seen = set()
        for item in original_targets:
            if isinstance(item, dict):
                target = dict(item)
                original = str(target.get("target", ""))
                key = _canonical_target(original)
                if key:
                    originals[uid].setdefault(key, original)
                    target["target"] = key
            else:
                original = str(item)
                key = _canonical_target(original)
                if key:
                    originals[uid].setdefault(key, original)
                target = {"target": key}
            key = target.get("target", "")
            if key and key not in seen:
                seen.add(key)
                normalized.append(target)
        entry["targets"] = normalized
    state["_radar_original_target_strings"] = originals
    return state


def _save_state(state):
    """Restore historical target spelling before persisting normalized state."""
    originals = state.pop("_radar_original_target_strings", {})
    try:
        for uid, entry in state.get("programs", {}).items():
            if not isinstance(entry, dict):
                continue
            known = originals.get(uid, {})
            for item in entry.get("targets", []):
                if isinstance(item, dict):
                    key = _canonical_target(item.get("target", ""))
                    if key in known:
                        item["target"] = known[key]
        _original_save_state(state)
    finally:
        state["_radar_original_target_strings"] = originals


def _extract_targets(program, platform):
    return _normalize_target_list(_original_extract_targets(program, platform))


def _send_to_urls(urls, payload):
    for url in urls:
        try:
            response = radar.requests.post(url, json=payload, timeout=10)
            if response.status_code >= 400:
                print(f"  [!] Discord Error {response.status_code}: {response.text}")
        except Exception as exc:
            print(f"Error sending Discord alert: {exc}")


def _send_discord_alert(message, webhook_url=None, title="BugBountyRadar Info", use_embed=True):
    raw_urls = webhook_url if webhook_url is not None else _primary_webhooks
    urls = _webhook_urls(raw_urls)
    if not urls:
        return

    if use_embed:
        description = message if len(message) <= 3900 else message[:3850] + "\n\n... (Truncated)"
        payload = {"embeds": [{
            "title": title,
            "description": description,
            "color": 0x5865F2,
            "timestamp": radar.time.strftime("%Y-%m-%dT%H:%M:%SZ", radar.time.gmtime()),
        }]}
    else:
        content = message if len(message) <= 1950 else message[:1900] + "\n\n... (Truncated)"
        payload = {"content": f"🚀 **{title}**\n{content}"}
    _send_to_urls(urls, payload)


def _send_log_alert(message):
    urls = _webhook_urls(_log_webhooks)
    if not urls:
        return
    content = message if len(message) <= 1950 else message[:1900] + "\n\n... (Log Truncated)"
    payload = {"content": f"📅 **{radar.time.strftime('%Y-%m-%d %H:%M:%S')}**\n{content}"}
    _send_to_urls(urls, payload)


# radar.main resolves these names from its module namespace at runtime.
radar.extract_targets = _extract_targets
radar.load_state = _load_state
radar.save_state = _save_state
radar.send_discord_alert = _send_discord_alert
radar.send_log_alert = _send_log_alert


if __name__ == "__main__":
    radar.main()
