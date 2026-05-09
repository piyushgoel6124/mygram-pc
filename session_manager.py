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
_pool_lock = threading.RLock()
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
    """Marks a session as blocked, kills its browser, and replaces it."""
    log_to_file(f"[Session] BLOCKED: {os.path.basename(session_path)}. Sleeping for 6 hours.")
    _session_stats[session_path] = {
        "sleep_until": time.time() + (6 * 3600),
        "last_used": time.time()
    }
    save_session_status()
    
    # KILL the blocked instance immediately
    close_pooled_browser(session_path)
    
    # REFILL the pool with a new session
    threading.Thread(target=initialize_browser_pool, daemon=True).start()

def get_pooled_browser(path):
    """Returns a page from the pool, checking if the browser is still alive."""
    with _pool_lock:
        if path in _browser_pool and not _browser_pool[path]["in_use"]:
            try:
                _browser_pool[path]["page"].url
                _browser_pool[path]["in_use"] = True
                return _browser_pool[path]["page"]
            except Exception:
                log_to_file(f"[Pool] Detected CRASHED browser for {os.path.basename(path)}. Cleaning up.")
                close_pooled_browser(path)
    
    # If not in pool, trigger priority launch
    threading.Thread(target=initialize_browser_pool, args=(path,), daemon=True).start()
    return None

def initialize_browser_pool(priority_session=None):
    """Maintains exactly ONE persistent browser instance. Cleans up zombies to save RAM."""
    global _pool_playwright, _browser_pool, _pool_initialized
    
    # 0. Zombie Cleanup: Kill orphaned chromium processes to free RAM on 1-core VPS
    try:
        if not _browser_pool:
            import subprocess
            subprocess.run(["taskkill", "/F", "/IM", "chrome.exe", "/T"], capture_output=True)
            subprocess.run(["taskkill", "/F", "/IM", "chromium.exe", "/T"], capture_output=True)
    except: pass

    with _pool_lock:
        if _pool_playwright is None:
            from playwright.sync_api import sync_playwright
            _pool_playwright = sync_playwright().start()

        all_sessions = get_all_sessions()
        now = time.time()
        load_session_status()
        
        # 1. Strict 1-Browser Limit
        if len(_browser_pool) >= 1 and not priority_session:
            return

        # 2. Determine target session
        ready = [s for s in all_sessions if s not in _browser_pool and now >= _session_stats.get(s, {}).get("sleep_until", 0)]
        
        target_session = None
        if priority_session and priority_session in ready:
            if len(_browser_pool) >= 1:
                old_path = list(_browser_pool.keys())[0]
                if old_path != priority_session:
                    close_pooled_browser(old_path)
            target_session = priority_session
        elif len(_browser_pool) == 0 and ready:
            target_session = ready[0]

        if target_session:
            try:
                log_to_file(f"[Pool] Warming up: {os.path.basename(target_session)}...")
                browser = _pool_playwright.chromium.launch(headless=True)
                context = browser.new_context(storage_state=target_session)
                page = context.new_page()
                page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_())
                
                # Full warm-up
                page.goto("https://www.instagram.com/", timeout=45000, wait_until="commit")
                
                # INSTANT HEARTBEAT: Ping tunnel immediately to keep it alive
                from config import PUBLIC_URL
                ping_url = f"{PUBLIC_URL.rstrip('/')}/ping?src=warmup_ping" if PUBLIC_URL else None
                if ping_url:
                    page.goto(ping_url, timeout=15000, wait_until="commit")

                _browser_pool[target_session] = {"browser": browser, "context": context, "page": page, "in_use": False}
                log_to_file(f"[Pool] SUCCESS: {os.path.basename(target_session)} is active and pinging.")
            except Exception as e:
                log_to_file(f"[Pool] Error launching {target_session}: {e}")

def close_pooled_browser(path):
    with _pool_lock:
        if path in _browser_pool:
            try:
                _browser_pool[path]["page"].close()
                _browser_pool[path]["context"].close()
                _browser_pool[path]["browser"].close()
            except: pass
            del _browser_pool[path]

def perform_heartbeat():
    """Pings the tunnel from each idle browser. Must be called from the worker thread."""
    from config import PUBLIC_URL
    base_ping = f"{PUBLIC_URL.rstrip('/')}/ping" if PUBLIC_URL else "http://127.0.0.1:5030/ping"
    
    with _pool_lock:
        for path, data in _browser_pool.items():
            if not data["in_use"]:
                try:
                    s_name = os.path.basename(path)
                    labeled_ping = f"{base_ping}?src=browser_{s_name}"
                    # Fast ping
                    data["page"].goto(labeled_ping, timeout=15000, wait_until="commit")
                except Exception as e:
                    log_to_file(f"[Heartbeat] {os.path.basename(path)} failed: {e}", to_console=False)

def get_pool_health():
    """Returns whether any browser recently succeeded in a ping."""
    # This is a placeholder - for now we'll rely on the logs to see the 'src=browser' pings
    return True

def release_pooled_browser(path):
    """Closes the browser and immediately triggers a launch for the NEXT session to keep the pool warm."""
    with _pool_lock:
        if path in _browser_pool:
            close_pooled_browser(path)
    
    # Proactively launch the next session to keep the tunnel alive
    threading.Thread(target=initialize_browser_pool, daemon=True).start()

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
