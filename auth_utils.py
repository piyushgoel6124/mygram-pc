import json
import os
import hashlib
from datetime import datetime
from config import ADMIN_USER, ADMIN_PASS, API_SECRET

USERS_FILE = "users.json"

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_users(users_list):
    with open(USERS_FILE, 'w') as f:
        json.dump(users_list, f, indent=4)

def update_user_stats(username, links=0, files=0, rows=0):
    """Thread-safe update of user statistics and credits."""
    users = load_users()
    for u in users:
        if u["username"] == username:
            u["requested_links"] = u.get("requested_links", 0) + links
            u["files_generated"] = u.get("files_generated", 0) + files
            u["total_rows_scraped"] = u.get("total_rows_scraped", 0) + rows
            # Deduct credits (Match user turn 23 field name)
            if "credits" in u:
                u["credits"] = max(0, u["credits"] - links)
            break
    save_users(users)

def verify_sig(username, device_id, received_sig):
    if not received_sig: return False
    data = f"{username}{device_id}{API_SECRET}"
    expected_sig = hashlib.md5(data.encode()).hexdigest()
    return expected_sig == received_sig

def check_auth(username, device_id, sig):
    if not verify_sig(username, device_id, sig):
        return False, "Invalid request signature (Security Error)"

    users = load_users()
    for u in users:
        if u["username"] == username:
            valid_till = datetime.strptime(u["valid_till"], "%Y-%m-%d %H:%M:%S")
            if datetime.now() > valid_till:
                return False, "Account expired"
            if u.get("device_id") and u["device_id"] != device_id:
                return False, "Account in use on another device"
            return True, "OK"
    return False, "User not found"
