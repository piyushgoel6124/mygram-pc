import os
import time
import json
import threading
from config import SESSIONS_DIR
from playwright.sync_api import sync_playwright
from logger_utils import log_to_file

# Global session states
_session_stats = {} 
_session_stats_file = "session_status.json"

def login_and_save_session():
    """
    Launches a headed browser to let the user log in manually,
    then saves the session state to a file.
    """
    with sync_playwright() as p:
        print("Launching headed Chromium browser...")
        browser = p.chromium.launch(headless=False)
        
        context = browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        
        page = context.new_page()
        print("Navigating to Instagram login page...")
        page.goto("https://www.instagram.com/accounts/login/")
        
        print("\n" + "="*50)
        print("PLEASE LOG IN MANUALLY IN THE BROWSER WINDOW.")
        print("Once you are fully logged in and see your feed,")
        print("come back here and press ENTER to save the session.")
        print("="*50 + "\n")
        
        input("Press Enter here after you have logged in...")
        
        # Save storage state to a named file in sessions/
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        name = input("Enter a name for this session (e.g. account1): ").strip() or f"session_{timestamp}"
        if not name.endswith(".json"): name += ".json"
        
        session_path = os.path.join(SESSIONS_DIR, name)
        context.storage_state(path=session_path)
        
        print(f"\n[SUCCESS] Session saved to: {session_path}")
        browser.close()

# Browser Pool globals
_pool_playwright = None
_browser_pool = {} 
_pool_lock = threading.Lock()
_pool_initialized = False

def load_session_status():
    global _session_stats
    if os.path.exists(_session_stats_file):
        try:
            with open(_session_stats_file, 'r') as f:
                _session_stats = json.load(f)
        except: _session_stats = {}

def save_session_status():
    try:
        with open(_session_stats_file, 'w') as f:
            json.dump(_session_stats, f)
    except: pass

def get_all_sessions():
    return [os.path.join(SESSIONS_DIR, f) for f in os.listdir(SESSIONS_DIR) if f.endswith('.json')]

def mark_session_sleep(session_path):
    """Marks a session as blocked and rotates it."""
    log_to_file(f"[Session] BLOCKED: {os.path.basename(session_path)}. Sleeping for 6 hours.")
    _session_stats[session_path] = {
        "sleep_until": time.time() + (6 * 3600),
        "last_used": time.time()
    }
    save_session_status()


def get_next_session_with_status():
    all_sessions = get_all_sessions()
    now = time.time()
    load_session_status()
    
    available = [s for s in all_sessions if now >= _session_stats.get(s, {}).get("sleep_until", 0)]
    if available:
        return available[0], 0, False
    
def test_headless_session():
    """Simple test to see if a session can reach Instagram feed."""
    sessions = get_all_sessions()
    if not sessions:
        print("No sessions found.")
        return
    
    print("\nSelect a session to test:")
    for i, s in enumerate(sessions):
        print(f"{i+1}. {os.path.basename(s)}")
    
    idx = input("\nChoice: ").strip()
    try:
        sel = sessions[int(idx)-1]
        with sync_playwright() as p:
            print(f"Testing {os.path.basename(sel)}...")
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=sel)
            page = context.new_page()
            page.goto("https://www.instagram.com/", timeout=60000)
            page.wait_for_timeout(3000)
            
            if "login" in page.url:
                print("[FAIL] Session expired or invalid.")
            else:
                print(f"[SUCCESS] Reached: {page.title()}")
            browser.close()
    except Exception as e:
        print(f"Test Error: {e}")

def fix_session_headed():
    """Opens a session in headed mode for manual checkpoint clearing."""
    sessions = get_all_sessions()
    if not sessions:
        print("No sessions found.")
        return
    
    print("\nSelect session to fix (Headed):")
    for i, s in enumerate(sessions):
        print(f"{i+1}. {os.path.basename(s)}")
    
    idx = input("\nChoice: ").strip()
    try:
        sel = sessions[int(idx)-1]
        print(f"\n[FIX MODE] Opening {os.path.basename(sel)} in HEADED mode...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(storage_state=sel)
            page = context.new_page()
            page.goto("https://www.instagram.com/")
            
            print("\n>>> BROWSER OPEN. Clear checkpoints/notifications manually.")
            input(">>> Press ENTER here when done to SAVE and CLOSE...")
            context.storage_state(path=sel)
            browser.close()
            print("[DONE] Session updated.")
    except Exception as e:
        print(f"Error: {e}")
