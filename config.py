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
# New Program Deep-Dive Prompt
NEW_PROGRAM_PROMPT = """
You are a professional bug bounty scout. Analyze this NEW program.
BE EXTREMELY BRIEF AND CONCISE. Maximum 1-2 sentences per point. 

Structure:
1. Platform Type: (e.g., eCommerce, B2B. What is the business?)
2. Registration: (Does it require SMS/ID/Specific Email? Or "Open to all"?)
3. Bug Priorities: (Top 2-3 most common/valuable bugs for this specific tech stack)
4. Scope Regex: (ONE consolidated regex for the main domains)
5. Dorks: (1-2 useful dorks only)

IMPORTANT: You MUST include a rating at the very end in this exact format:
RATING: X/10
"""

# Scope Expansion Impact Prompt
SCOPE_UPDATE_PROMPT = """
You are a professional bug bounty scout. New assets were added to an existing program.
BE EXTREMELY BRIEF AND CONCISE. Maximum 1-2 sentences per point.

Structure:
1. Nature of Scope: (What are these new assets? API? New Product? Testing env?)
2. What to look for: (Bug types specifically relevant to THESE new assets)
3. Scope Regex: (ONE regex covering these new assets)
4. Quick Links: (Links to subdomains or documentation)

IMPORTANT: You MUST include a rating at the very end (use 5/10 if unsure) in this exact format:
RATING: X/10
"""

# Filter Settings
# Only notify if the AI rating is greater than or equal to this value.
# Set to 0 to receive all alerts.
MIN_RATING = 1

# Platform Sources
DATA_SOURCES = {
    "HackerOne": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/hackerone_data.json",
    "Bugcrowd": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/bugcrowd_data.json",
    "Intigriti": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/intigriti_data.json",
    "YesWeHack": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/yeswehack_data.json",
    "Federacy": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/federacy_data.json"
}

# State File (where notified programs are saved)
STATE_FILE = "state.json"
