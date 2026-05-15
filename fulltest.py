import requests
import hashlib
import json
import time
import os
import sys

# Constants
BASE_URL = "http://localhost:5030"
USERS_FILE = "users.json"
API_SECRET = "MyGram_Security_Key_2026" # Hardcoded based on config.py

def get_sig(username, device_id):
    data = f"{username}{device_id}{API_SECRET}"
    return hashlib.md5(data.encode()).hexdigest()

def load_user(target_username):
    if not os.path.exists(USERS_FILE):
        print(f"Error: {USERS_FILE} not found.")
        return None
    
    with open(USERS_FILE, "r") as f:
        users = json.load(f)
    
    for user in users:
        if user["username"] == target_username:
            return user
    return None

def run_test():
    target_username = input("Enter username to test: ").strip()
    user_data = load_user(target_username)
    
    if not user_data:
        print(f"Error: User '{target_username}' not found in {USERS_FILE}.")
        return

    device_id = user_data.get("device_id")
    if not device_id:
        print(f"Error: User '{target_username}' has no device_id.")
        return

    sig = get_sig(target_username, device_id)
    reel_url = input("Enter Instagram Reel URL to scrape: ").strip()
    if not reel_url:
        reel_url = "https://www.instagram.com/reels/DA8Xyv-o5tF/" # Default test reel
        print(f"Using default reel: {reel_url}")

    # 1. Submit Task
    print(f"\n[1/3] Submitting task for user '{target_username}'...")
    submit_data = {
        "username": target_username,
        "device_id": device_id,
        "sig": sig,
        "url": reel_url
    }
    
    try:
        res = requests.post(f"{BASE_URL}/submit", json=submit_data)
        if res.status_code != 200:
            print(f"Submission failed ({res.status_code}): {res.text}")
            return
        
        req_id = res.json().get("request_id")
        print(f"Success! Request ID: {req_id}")
        
    except Exception as e:
        print(f"Connection Error during submission: {e}")
        return

    # 2. Wait and Poll Status
    print(f"\n[2/3] Waiting for task to complete...")
    status_params = {
        "id": req_id,
        "username": target_username,
        "device_id": device_id,
        "sig": sig
    }
    
    last_log_count = 0
    while True:
        try:
            res = requests.get(f"{BASE_URL}/status", params=status_params)
            if res.status_code != 200:
                print(f"Status check failed ({res.status_code}): {res.text}")
                break
            
            data = res.json()
            status = data.get("status")
            logs = data.get("logs", [])
            
            # Print new logs
            if len(logs) > last_log_count:
                for i in range(last_log_count, len(logs)):
                    print(f"  > {logs[i]}")
                last_log_count = len(logs)
            
            if status == "completed":
                print("\nTask Completed Successfully!")
                break
            elif status == "error":
                print("\nTask failed with error.")
                break
            
            time.sleep(2)
        except Exception as e:
            print(f"Connection Error during polling: {e}")
            break

    # 3. Download Result
    print(f"\n[3/3] Downloading result...")
    download_params = {
        "username": target_username,
        "device_id": device_id,
        "sig": sig
    }
    
    try:
        res = requests.get(f"{BASE_URL}/download/{req_id}", params=download_params)
        if res.status_code == 200:
            filename = f"result_{req_id}.csv"
            with open(filename, "wb") as f:
                f.write(res.content)
            print(f"Success! Downloaded results to: {filename}")
            print(f"Total size: {len(res.content)} bytes")
        else:
            print(f"Download failed ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"Connection Error during download: {e}")

if __name__ == "__main__":
    run_test()
