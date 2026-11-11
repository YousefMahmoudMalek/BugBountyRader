# BugBountyRadar 🛰️

BugBountyRadar automatically scans major bug bounty platforms (HackerOne, Bugcrowd, Intigriti, YesWeHack, Federacy, and ProjectDiscovery Chaos) for new bug bounty programs and scope expansions. When the same asset appears with different formatting (for example `https://*.mybring.com` vs `*.mybring.com`), the scanner now compares canonicalized targets before alerting.

---

## ⚡ Features

- **Multi-Platform Support**: HackerOne, Bugcrowd, Intigriti, YesWeHack, Federacy, and Chaos.
- **Smart Scope Tracking**: Detects new targets added to existing programs as well as brand-new programs.
- **Stable target comparison**: Scope comparison is done on normalized target values so `http`/`https`, trailing slashes, uppercase/lowercase, and equivalent wildcard formatting do not trigger duplicate alerts.
- **Multiple Discord destinations**: Each Discord secret can contain more than one webhook URL separated by `|`.
- **Automated State Tracking**: Saves seen targets in `state.json` directly back to GitHub.

---

## 🤖 AI Provider Setup

Add any provider keys you use as GitHub Actions secrets: `OPENROUTER_API_KEY`, `GEMINI_API_KEYS`, `GROQ_API_KEY`, and/or `DEEPSEEK_API_KEY`.

---

## ⚙️ Configuration

Settings can be customized in [`config.py`](config.py):

- **`MIN_RATING`**: Minimum AI score (1–10) required to trigger an alert. Default is `1`.
- **`DATA_SOURCES`**: Toggle or configure bounty feeds.

### Scan Frequency

The check interval is defined in [`.github/workflows/check.yml`](.github/workflows/check.yml):

```yaml
on:
  schedule:
    - cron: '0 * * * *'    # Runs hourly
```

---

## 🔔 Notification Webhooks

Set the following in GitHub Repository Secrets:

- `DISCORD_WEBHOOK_URL`: Primary channel for new programs. You can list multiple webhook URLs in the same secret, separated by `|`.
- `SCOPE_WEBHOOK_URL`: Dedicated channel for scope expansion alerts. Same `|` format is supported.
- `LOG_WEBHOOK_URL`: Run summary logs. Leave blank if a destination should not receive logs.

Example:

```text
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/PRIMARY_A|https://discord.com/api/webhooks/PRIMARY_B
SCOPE_WEBHOOK_URL=https://discord.com/api/webhooks/SCOPE_A|https://discord.com/api/webhooks/SCOPE_B
LOG_WEBHOOK_URL=https://discord.com/api/webhooks/LOG_A
```

This makes it possible to send one server the full set of notifications while another server only receives selected channels. The `|` separator is only used inside the GitHub secret value; it is not part of the Discord webhook URL itself.
