"""
BugBountyRadar Configuration
---------------------------
Customize your AI prompt, platform sources, and filters here.

HOW TO CHANGE CHECK FREQUENCY:
To change how often the service runs (e.g., every 15 mins vs hourly):
1. Open '.github/workflows/check.yml'
2. Find the line: "- cron: '0 * * * *'"
3. Replace the cron value. 
   Examples: 
   '*/15 * * * *'  -> Every 15 minutes
   '*/30 * * * *'  -> Every 30 minutes
   '0 * * * *'     -> Every hour (default)
"""

# AI Analysis Settings
# Note: The entire program data is sent to the AI.
GEMINI_PROMPT = """
Analyze the following bug bounty program and provide a short summary.
Include:
1. A rating from 1 to 10 (based on potential rewards and program reputation).
2. Key highlights (e.g., target types, specific high-pay rewards).
3. A brief "Should I hunt?" advice.

IMPORTANT: You must include the rating at the very end of your response in this exact format:
RATING: X/10
(where X is the number)
"""

# Filter Settings
# Only notify if the AI rating is greater than or equal to this value.
# Set to 0 to receive all alerts.
MIN_RATING = 1

# Platform Sources
DATA_SOURCES = {
    "HackerOne": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/hackerone_data.json",
    "Bugcrowd": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/bugcrowd_data.json",
    "Intigriti": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/intigriti_data.json"
}

# State File (where notified programs are saved)
STATE_FILE = "state.json"
