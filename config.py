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
You are an expert bug bounty mentor helping a complete beginner. 
Analyze the following bug bounty program and provide a friendly, clear summary.

Include:
1. A Rating from 1 to 10: Explain WHY you gave this rating (e.g., "High rewards but hard for beginners" or "Great for learning because it has many easy targets").
2. What to test: List the main websites, apps, or servers in plain English.
3. Rewards: Mention if they pay real money (bounties) or just "points" (VDP/Hall of Fame).
4. Beginner Advice: A 1-2 sentence tip on where to start with this specific program.

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
