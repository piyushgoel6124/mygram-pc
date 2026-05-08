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
    log_to_file(f"[Session] Rate limited: {os.path.basename(session_path)}. Sleeping for 6 hours.")
    _session_stats[session_path] = {
        "sleep_until": time.time() + (6 * 3600),
        "last_used": time.time()
    }
    save_session_status()
    close_pooled_browser(session_path)

def initialize_browser_pool():
    global _pool_playwright, _browser_pool, _pool_initialized
    
    if _pool_playwright is None:
        from playwright.sync_api import sync_playwright
        _pool_playwright = sync_playwright().start()

    all_sessions = get_all_sessions()
    now = time.time()
    load_session_status()
    
    # 1. Clean up pool
    to_close = [s for s in _browser_pool if now < _session_stats.get(s, {}).get("sleep_until", 0)]
    for s in to_close:
        log_to_file(f"[Pool] Session {os.path.basename(s)} is now sleeping. Removing.")
        close_pooled_browser(s)

    # 2. Sequential launch
    ready = [s for s in all_sessions if now >= _session_stats.get(s, {}).get("sleep_until", 0)]
    target = ready[:5]
    
    for s in target:
        if s not in _browser_pool and len(_browser_pool) < 5:
            try:
                log_to_file(f"[Pool] Launching browser for {os.path.basename(s)}...")
                browser = _pool_playwright.chromium.launch(headless=True)
                context = browser.new_context(storage_state=s)
                page = context.new_page()
                page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_())
                page.goto("https://www.instagram.com/robots.txt", timeout=60000)
                
                _browser_pool[s] = {"browser": browser, "context": context, "page": page, "in_use": False}
                log_to_file(f"[Pool] SUCCESS: Launched {os.path.basename(s)}")
                time.sleep(3)
            except Exception as e:
                log_to_file(f"[Pool] Error warming up {s}: {e}")

    if not _pool_initialized and _browser_pool:
        log_to_file("==================================================")
        log_to_file("Browser Pool Ready. Waiting for submissions...")
        log_to_file("==================================================")
        _pool_initialized = True

def close_pooled_browser(path):
    with _pool_lock:
        if path in _browser_pool:
            try:
                _browser_pool[path]["page"].close()
                _browser_pool[path]["context"].close()
                _browser_pool[path]["browser"].close()
            except: pass
            del _browser_pool[path]

def get_pooled_browser(path):
    with _pool_lock:
        if path in _browser_pool and not _browser_pool[path]["in_use"]:
            _browser_pool[path]["in_use"] = True
            return _browser_pool[path]["page"]
    return None

def release_pooled_browser(path):
    with _pool_lock:
        if path in _browser_pool:
            _browser_pool[path]["in_use"] = False

def get_next_session_with_status():
    all_sessions = get_all_sessions()
    now = time.time()
    load_session_status()
    
    available = [s for s in all_sessions if now >= _session_stats.get(s, {}).get("sleep_until", 0)]
    if available:
        return available[0], 0, False
    
    # Return shortest wait
    waits = [stats["sleep_until"] - now for stats in _session_stats.values() if stats.get("sleep_until", 0) > now]
    return None, min(waits) if waits else 60, True
