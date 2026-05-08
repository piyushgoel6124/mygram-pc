import os

# Instagram API Constants
DOC_ID = "8845758582119845"
APP_ID = "936619743392459"
ASBD_ID = "129477"

# Security
API_SECRET = "MyGram_Security_Key_2026"
ADMIN_USER = "admin"
ADMIN_PASS = "lollipop"

# Directories
SESSIONS_DIR = "sessions"
OUTPUTS_DIR = "outputs"
LOG_FILE = "logggs.txt"
PUBLIC_URL = "https://mygram.techlive.in"

# Ensure directories exist
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)
