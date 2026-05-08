import os
import sys
import time
import re
import json
import random
import threading
import psutil
import shutil
import uuid
import io
import csv
from playwright.sync_api import sync_playwright
from datetime import datetime
import hashlib
import requests
import base64

_oc = "Z2luZm9jL2lwYS9wcGEubGVjcmV2LmlwLWFoc2thcmR1ci9seTpvUHR0aGE="

# --- Global Logging Setup (Optimized for Single-Core) ---
import queue
_log_queue = queue.Queue()
_log_lock = threading.Lock()

def _logging_worker():
    """Background thread to handle file I/O for logging with batching (Optimized for Single-Core)."""
    while True:
        try:
            # Block until at least one message is available
            batch = [_log_queue.get()]
            # Collect all other currently available messages in the queue to process as a batch
            while not _log_queue.empty():
                try:
                    batch.append(_log_queue.get_nowait())
                except queue.Empty:
                    break
            
            new_lines = []
            for message, to_console in batch:
                timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
                full_msg = f"{timestamp} {message}"
                if to_console:
                    print(full_msg)
                new_lines.append(full_msg)
            
            # Perform a single file write for the entire batch (Optimized: Append mode)
            if new_lines:
                with _log_lock:
                    try:
                        with open("logggs.txt", "a", encoding="utf-8") as f:
                            f.write("\n".join(new_lines) + "\n")
                    except Exception as e:
                        print(f"Logging File Error: {e}")

            
            # Mark all items in the batch as done
            for _ in range(len(batch)):
                _log_queue.task_done()
                
        except Exception as e:
            # Avoid using log_to_file here to prevent recursion if queue is the issue
            sys.stderr.write(f"Logging Worker Error: {e}\n")
            time.sleep(2)



# Start background logger
threading.Thread(target=_logging_worker, daemon=True).start()

def log_to_file(message, to_console=True):
    """Adds a message to the logging queue."""
    _log_queue.put((message, to_console))




# --- Instagram API Constants ---
DOC_ID = "8845758582119845"
APP_ID = "936619743392459"
ASBD_ID = "129477"

SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

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

# --- Session Rotation & Tracking Logic ---
SESSION_STATUS_FILE = "session_status.json"
_session_cycle_idx = 0
# _session_stats: { session_path: {"last_used": 0, "sleep_until": 0} }
_session_stats = {}

def load_session_status():
    global _session_stats
    if os.path.exists(SESSION_STATUS_FILE):
        try:
            with open(SESSION_STATUS_FILE, "r") as f:
                _session_stats = json.load(f)
        except:
            _session_stats = {}

def save_session_status():
    try:
        with open(SESSION_STATUS_FILE, "w") as f:
            json.dump(_session_stats, f, indent=2)
    except Exception as e:
        print(f"Error saving session status: {e}")

# Initial load
import json
load_session_status()

def get_all_sessions():
    """Returns a list of all available session JSON files."""
    sessions = []
    if os.path.exists("sessions"):
        sessions = [os.path.join("sessions", f) for f in os.listdir("sessions") if f.endswith(".json")]
    return sorted(sessions)



def update_session_usage(session_path):
    """Updates the last usage timestamp for a session."""
    global _session_stats
    if session_path not in _session_stats:
        _session_stats[session_path] = {"last_used": 0, "sleep_until": 0}
    _session_stats[session_path]["last_used"] = time.time()
    save_session_status()

def mark_session_sleep(session_path):
    """Marks a session as sleeping for 6 hours due to rate limits."""
    global _session_stats
    if session_path not in _session_stats:
        _session_stats[session_path] = {"last_used": 0, "sleep_until": 0}
    _session_stats[session_path]["sleep_until"] = time.time() + (6 * 3600)
    log_to_file(f"[!] SESSION SLEEP: {os.path.basename(session_path)} marked as SLEEPING for 6 hours.")
    save_session_status()
    
    # Close and remove from pool if active
    close_pooled_browser(session_path)




def get_next_session_with_status():
    """
    Returns (session_path, wait_seconds, all_sleeping).
    - If a session is ready, wait_seconds is 0 and all_sleeping is False.
    - If sessions are only on cooldown, wait_seconds > 0.
    - If all sessions are in 6h sleep, all_sleeping is True.
    """
    global _session_cycle_idx, _session_stats
    sessions = get_all_sessions()
    if not sessions:
        return None, 0, True
    
    now = time.time()
    available = []
    min_cooldown_wait = float('inf')
    all_sleeping = True
    
    for s in sessions:
        stats = _session_stats.get(s, {"last_used": 0, "sleep_until": 0})
        
        # Check sleep
        if now < stats["sleep_until"]:
            continue
        
        all_sleeping = False # Found at least one that isn't sleeping
        
        # Check cooldown (2 min = 120s)
        wait = (stats["last_used"] + 120) - now
        if wait <= 0:
            available.append(s)
        else:
            if wait < min_cooldown_wait:
                min_cooldown_wait = wait
                
    if available:
        session = available[_session_cycle_idx % len(available)]
        _session_cycle_idx += 1
        return session, 0, False
    
    if all_sleeping:
        # Find earliest wake time
        min_sleep_wait = float('inf')
        for s in sessions:
            stats = _session_stats.get(s, {"last_used": 0, "sleep_until": 0})
            wait = stats["sleep_until"] - now
            if wait < min_sleep_wait:
                min_sleep_wait = wait
        return None, min_sleep_wait, True
        
    return None, min_cooldown_wait, False

def get_next_session():
    """Legacy wrapper for simple checks."""
    s, wait, sleeping = get_next_session_with_status()
    return s if wait <= 0 and not sleeping else None




def test_headless_session():
    """
    Tests the saved session in headless mode.
    """
    sessions = get_all_sessions()
    if not sessions:
        print(f"Error: No sessions found. Please run with --login first.")
        return

    current_session = get_next_session()
    if not current_session:
        print("Error: No available sessions (all sleeping or none found).")
        return
    print(f"Testing session: {os.path.basename(current_session)}")

    with sync_playwright() as p:
        print("Launching headless Chromium browser to test session...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=current_session)

        
        # Abort all media, image, and font requests
        def block_media(route):
            if route.request.resource_type in ["image", "media", "font"]:
                route.abort()
            else:
                route.continue_()

        page = context.new_page()
        page.route("**/*", block_media)
        
        print("Navigating to Instagram...")
        page.goto("https://www.instagram.com/")
        
        # Give it a moment to load
        time.sleep(3)
        
        # Check if we are logged in (e.g., check for profile icon or absence of login button)
        # Usually, if we see 'reels' or 'direct' in the URL or page, we are in.
        title = page.title()
        url = page.url
        
        print(f"Page Title: {title}")
        print(f"Current URL: {url}")
        
        if "instagram.com" in url and "login" not in url:
            print("SUCCESS: Session is valid and working headlessly!")
        else:
            print("FAILURE: Session seems invalid or expired.")
            
        browser.close()

def scrape_reel(reel_url):
    """
    Experimental: Scrapes data from an Instagram Reel URL using the saved session.
    """
    if not get_all_sessions():
        print(f"Error: No sessions found. Please run with --login first.")
        return


    # Extract shortcode
    match = re.search(r'/(?:reels?|p)/([^/?#&]+)', reel_url)
    if not match:
        print("Invalid Instagram Reel URL.")
        return
    shortcode = match.group(1)
    print(f"Detected shortcode: {shortcode}")

    current_session = get_next_session()
    if not current_session:
        print("Error: No available sessions.")
        return
    with sync_playwright() as p:
        print(f"Launching headless Chromium browser (using {os.path.basename(current_session)}) to scrape {shortcode}...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=current_session)

        
        # Abort all media, image, and font requests
        def block_media(route):
            if route.request.resource_type in ["image", "media", "font"]:
                route.abort()
            else:
                route.continue_()

        page = context.new_page()
        page.route("**/*", block_media)
        
        print(f"Navigating to Instagram to ensure session is active...")
        page.goto("https://www.instagram.com/")
        page.wait_for_timeout(2000)

        print(f"Fetching data via GraphQL for {shortcode}...")
        
        # Use simple evaluate to make the fetch request from the page context
        # This will automatically use the browser's cookies and session
        data = page.evaluate("""
            async (shortcode) => {
                try {
                    // 1. Get CSRF Token from cookies
                    const csrf = document.cookie.split('; ')
                        .find(row => row.startsWith('csrftoken='))
                        ?.split('=')[1] || "";
                    
                    const variables = {
                        shortcode: shortcode,
                        fetch_tagged_user_count: null,
                        hoisted_comment_id: null,
                        hoisted_reply_id: null,
                    };

                    const form = new URLSearchParams();
                    form.append("variables", JSON.stringify(variables));
                    form.append("doc_id", "8845758582119845");

                    const res = await fetch("https://www.instagram.com/graphql/query", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/x-www-form-urlencoded",
                            "X-CSRFToken": csrf,
                            "X-Instagram-AJAX": "1",
                            "X-Requested-With": "XMLHttpRequest",
                        },
                        body: form.toString(),
                    });

                    if (!res.ok) return { error: `HTTP ${res.status}` };
                    const json = await res.json();
                    const m = json?.data?.xdt_shortcode_media;

                    if (!m) return { error: "No media data found" };

                    const views = m.video_view_count || 0;
                    const plays = m.video_play_count || 0;
                    const hook_percentage = plays > 0 ? ((views / plays) * 100).toFixed(2) : "0.00";

                    return {
                        shortcode: m.shortcode,
                        author: m.owner?.username,
                        likes: m.edge_media_preview_like?.count || m.edge_liked_by?.count || 0,
                        comments: m.edge_media_to_parent_comment?.count || 0,
                        views: views,
                        plays: plays,
                        hook_percentage: hook_percentage,
                        text: m.edge_media_to_caption?.edges?.[0]?.node?.text || "",
                        date: m.taken_at_timestamp ? new Date(m.taken_at_timestamp * 1000).toISOString() : "",
                    };
                } catch (e) {
                    return { error: e.toString() };
                }
            }
        """, shortcode)
        
        if "error" in data:
            print(f"Extraction failed: {data['error']}")
        else:
            print("\nScraped Data (via GraphQL):")
            import json
            print(json.dumps(data, indent=2))
        
        browser.close()
        return data

# --- Global Scrape Task Manager ---
# { id: {"status": "running/queued/done", "event": Event, "logs": [], "filename": "", "results_path": ""} }
scrape_tasks = {}
scrape_tasks_lock = threading.Lock()
scrape_lock = threading.Lock()
queue_lock = threading.Lock()
request_queue = []
scrapes = {} # Legacy/Internal storage for finished results

# --- Browser Pool Manager ---
# Keeps up to 5 browsers running in RAM to avoid CPU overhead of launching
_pool_playwright = None
_browser_pool = {} # { session_path: {"browser": ..., "context": ..., "page": ...} }
_pool_lock = threading.Lock()
_pool_initialized = False # Track initial warmup


def initialize_browser_pool():
    """Launches browsers for up to 5 available, non-sleeping sessions."""
    global _pool_playwright, _browser_pool
    
    if _pool_playwright is None:
        from playwright.sync_api import sync_playwright
        _pool_playwright = sync_playwright().start()

    all_sessions = get_all_sessions()
    now = time.time()
    
    # 1. Clean up pool: Remove sleeping sessions
    to_close = []
    for s in _browser_pool:
        stats = _session_stats.get(s, {"sleep_until": 0})
        if now < stats["sleep_until"]:
            to_close.append(s)
    
    for s in to_close:
        log_to_file(f"[Pool] Session {os.path.basename(s)} is now sleeping. Removing from pool.")
        close_pooled_browser(s)

    # 2. Add new sessions up to 5
    ready_sessions = []
    for s in all_sessions:
        stats = _session_stats.get(s, {"sleep_until": 0})
        if now >= stats["sleep_until"]:
            ready_sessions.append(s)
    
    # Limit to top 5 ready sessions
    target_sessions = ready_sessions[:5]
    
    launched_any = False
    for s in target_sessions:
        if s not in _browser_pool and len(_browser_pool) < 5:
            try:
                launched_any = True
                log_to_file(f"[Pool] Launching browser for {os.path.basename(s)}...")
                browser = _pool_playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    storage_state=s,
                    viewport={'width': 1280, 'height': 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
                )
                page = context.new_page()
                def block_media(route):
                    if route.request.resource_type in ["image", "media", "font"]:
                        route.abort()
                    else: route.continue_()
                page.route("**/*", block_media)
                
                # Use robots.txt for a lightweight warmup
                page.goto("https://www.instagram.com/robots.txt", timeout=60000)
                
                _browser_pool[s] = {
                    "browser": browser,
                    "context": context,
                    "page": page,
                    "in_use": False
                }
                log_to_file(f"[Pool] SUCCESS: Launched browser for {os.path.basename(s)}")
                
                # Small sleep to let the CPU breathe on single-core VPS
                time.sleep(3) 

            except Exception as e:
                log_to_file(f"[Pool] Error warming up {s}: {e}")

    global _pool_initialized
    if not _pool_initialized and len(_browser_pool) > 0:
        log_to_file("==================================================")
        log_to_file("Browser Pool Warmed Up. Waiting for submissions...")
        log_to_file("==================================================")
        _pool_initialized = True


    # 3. Handle cases where we have more than 5 in pool (shouldn't happen with current logic but good for safety)
    while len(_browser_pool) > 5:
        # Close the last one added that isn't in use
        for s in reversed(list(_browser_pool.keys())):
            if not _browser_pool[s]["in_use"]:
                close_pooled_browser(s)
                break

def close_pooled_browser(session_path):
    """Closes and removes a specific browser from the pool."""
    global _browser_pool
    with _pool_lock:
        if session_path in _browser_pool:
            try:
                _browser_pool[session_path]["page"].close()
                _browser_pool[session_path]["context"].close()
                _browser_pool[session_path]["browser"].close()
            except: pass
            del _browser_pool[session_path]

def get_pooled_browser(session_path):

    """Returns a page from the pool for a given session."""
    with _pool_lock:
        if session_path in _browser_pool:
            if not _browser_pool[session_path]["in_use"]:
                _browser_pool[session_path]["in_use"] = True
                return _browser_pool[session_path]["page"]
    return None

def release_pooled_browser(session_path):
    """Marks a pooled browser as available."""
    with _pool_lock:
        if session_path in _browser_pool:
            _browser_pool[session_path]["in_use"] = False



def fetch_remote_config(username, device_id, sig):
    
    try:
        # Decode first, then reverse the resulting string to get the URL
        rcu = base64.b64decode(_oc).decode()[::-1]
        params = {"username": username, "device_id": device_id, "sig": sig}
        res = requests.get(rcu, params=params, timeout=10)
        if res.status_code == 200:
            return res.json()
        print(f"[Remote Config] Error: Server returned {res.status_code}")
    except Exception as e:
        print(f"[Remote Config] Connection Failed: {e}")
    return None

def scrape_since_reel(reel_url, logger=None, on_data=None, cancel_event=None, auth_info=None):
    """
    Scrapes all reels from a profile starting from a specific reel URL.
    auth_info: {"username": ..., "device_id": ..., "sig": ...} for remote license check.
    """
    def log(msg):
        if logger: logger(msg)
        log_to_file(f"[Scraper] {msg}")


    def is_cancelled():
        if cancel_event and cancel_event.is_set():
            return True
        return False

    # 1. Using Hardcoded Config (Master server verification removed)
    doc_id = DOC_ID
    app_id = APP_ID
    asbd_id = ASBD_ID
    
    # The session wait logic inside scrape_since_reel will handle picking one
    # from the sessions/ folder.
    current_session = None
    last_wait_log = 0
    while not current_session:
        if is_cancelled(): break
        s, wait, all_sleeping = get_next_session_with_status()
        
        now = time.time()
        if all_sleeping:
            h, m = int(wait // 3600), int((wait % 3600) // 60)
            msg = f"Waiting: All accounts are sleeping. Next available in {h}h {m}m."
            if now - last_wait_log > 30:
                log(msg)
                last_wait_log = now
            time.sleep(30)
            continue

        if s:
            current_session = s
            break
        
        # Cooldown wait
        msg = f"Account Cooldown: Waiting {int(wait)}s for the next session to be ready..."
        if now - last_wait_log > 10:
            log(msg)
            last_wait_log = now
        
        time.sleep(min(5, wait))
    
    if not current_session:
        log("No session available and wait was interrupted.")
        return [], None

    log(f"Using session (Pooled): {os.path.basename(current_session)}")
    
    # Use the pre-warmed browser from the pool instead of launching a new one
    page = get_pooled_browser(current_session)

    if not page:
        log(f"Error: Could not get pooled browser for {current_session}. Launching fallback...")
        # Fallback if pool initialization failed for this session
        browser_fb = _pool_playwright.chromium.launch(headless=True)
        context_fb = browser_fb.new_context(storage_state=current_session)
        page = context_fb.new_page()
    else:
        browser_fb = None # We are using pooled
    
    try:
        # Step 1: Get Target Reel Info via DIRECT GraphQL
        match = re.search(r'/(?:reels?|p)/([^/?#&]+)', reel_url)
        if not match:
            log("Invalid Instagram Reel URL.")
            if browser_fb: browser_fb.close()
            return [], None
        target_shortcode = match.group(1)
        
        log(f"Target Reel: {target_shortcode}. Fetching details directly...")
        page.goto("https://www.instagram.com/") # Ensure full session/cookies are loaded
        page.wait_for_timeout(3000) 
        
        target_data = page.evaluate("""

        async ({shortcode, app_id, asbd_id, doc_id}) => {
            try {
                const csrf = document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || "";
                const res = await fetch("https://www.instagram.com/graphql/query", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-CSRFToken": csrf,
                        "X-Instagram-AJAX": "1",
                        "X-Requested-With": "XMLHttpRequest",
                        "X-IG-App-ID": app_id,
                        "X-ASBD-ID": asbd_id
                    },
                    body: new URLSearchParams({ 
                        variables: JSON.stringify({ shortcode }), 
                        doc_id: doc_id
                    }).toString(),
                });
                const json = await res.json();
                const m = json?.data?.xdt_shortcode_media;
                if (!m) return { error: "No media data in response", json };
                return {
                    username: m.owner?.username,
                    timestamp: m.taken_at_timestamp,
                    shortcode: m.shortcode
                };
            } catch (e) {
                return { error: e.message };
            }
        }
    """, {"shortcode": target_shortcode, "app_id": app_id, "asbd_id": asbd_id, "doc_id": doc_id})

        if not target_data or "error" in target_data:
            err_msg = target_data.get('error', 'Unknown Error')
            log(f"Failed to identify reel author: {err_msg}")
        
            raw_json = target_data.get('json', {})
            if raw_json.get('message') == 'feedback_required' or raw_json.get('spam'):
                mark_session_sleep(current_session)
            elif "json" in target_data:
                log(f"GraphQL Response: {target_data['json']}")
            
            browser.close()
            update_session_usage(current_session)
            return [], None


        username = target_data['username']
        target_ts = target_data['timestamp']
        log(f"Author identified: {username}, Target Timestamp: {target_ts}")

        # Step 3: Go to Profile
        profile_url = f"https://www.instagram.com/{username}/reels/"
        log(f"Navigating to profile: {profile_url}")
        page.goto(profile_url)
        page.wait_for_timeout(2000)

        # Step 4: Scroll and Fetch in Real-time
        log(f"Scrolling and fetching stats in real-time...")
        all_reels_enriched = [] 
        seen_scs = set()
        found_target = False
    
        scroll_count = 0
        last_new_reel_time = time.time()
    
        # Helper for fetching stats inside the current page context
        def fetch_in_page(scs):
            return page.evaluate("""
                async ({shortcodes, app_id, asbd_id, doc_id}) => {
                    const csrf = document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || "";
                    const fetchOne = async (sc) => {
                        try {
                            const res = await fetch("https://www.instagram.com/graphql/query", {
                                method: "POST",
                                headers: {
                                    "Content-Type": "application/x-www-form-urlencoded",
                                    "X-CSRFToken": csrf,
                                    "X-Instagram-AJAX": "1",
                                    "X-Requested-With": "XMLHttpRequest",
                                    "X-IG-App-ID": app_id,
                                    "X-ASBD-ID": asbd_id
                                },
                                body: new URLSearchParams({ 
                                    variables: JSON.stringify({ shortcode: sc }), 
                                    doc_id: doc_id
                                }).toString(),
                            });
                            const json = await res.json();
                            const m = json?.data?.xdt_shortcode_media;
                            if (!m) return { shortcode: sc, error: "No data", raw: json };
                            const views = m.video_view_count || 0;
                            const plays = m.video_play_count || 0;
                            const hook_percentage = plays > 0 ? ((views / plays) * 100).toFixed(2) : "0.00";

                            return {
                                shortcode: m.shortcode,
                                likes: m.edge_media_preview_like?.count || m.edge_liked_by?.count || 0,
                                comments: m.edge_media_to_comment?.count || m.edge_media_to_parent_comment?.count || 0,
                                views: views,
                                plays: plays,
                                hook_percentage: hook_percentage,
                                text: m.edge_media_to_caption?.edges?.[0]?.node?.text || "",
                                timestamp: m.taken_at_timestamp,
                                date: m.taken_at_timestamp ? new Date(m.taken_at_timestamp * 1000).toISOString() : "",
                                url: `https://www.instagram.com/reels/${m.shortcode}/`
                            };
                        } catch(e) { return { shortcode: sc, error: e.message }; }
                    };
                    return await Promise.all(shortcodes.map(sc => fetchOne(sc)));
                }
            """, {"shortcodes": scs, "app_id": app_id, "asbd_id": asbd_id, "doc_id": doc_id})

        while not found_target:
            if is_cancelled():
                log("CANCELLATION: Stop requested by user. Aborting scroll loop.")
                browser.close()
                update_session_usage(current_session)
                return [], None

            # Capture shortcodes in view
            batch_scs = page.evaluate("""

                () => Array.from(document.querySelectorAll('a[href*="/reel/"], a[href*="/p/"]'))
                           .map(a => a.href.split('/').filter(Boolean).pop())
            """)
        
            new_scs_to_fetch = [sc for sc in batch_scs if sc not in seen_scs]
        
            if new_scs_to_fetch:
                last_new_reel_time = time.time()
                # Fetch details for these new ones immediately
                batch_results = fetch_in_page(new_scs_to_fetch)
            
                for item in batch_results:
                    if item['shortcode'] in seen_scs: continue
                    seen_scs.add(item['shortcode'])
                
                    # Check for rate limits
                    if "raw" in item:
                        raw = item["raw"]
                        if raw.get('message') == 'feedback_required' or raw.get('spam'):
                            mark_session_sleep(current_session)
                            found_target = True # Break loop
                            break
                
                    if "error" not in item:
                        all_reels_enriched.append(item)
                        if on_data: on_data(item) # Stream it back!
                
                    if item['shortcode'] == target_shortcode:
                        found_target = True

            if len(all_reels_enriched) >= 100000:
                log(f"Reached safety limit of 100,000 reels. Stopping.")
                break

            if found_target: 
                log(f"Target reel reached and processed.")
                break
        
            if time.time() - last_new_reel_time > 30:
                log("Instagram blocked loading of new reels or reached end of profile. (30s timeout)")
                break
        
            # Direct scrolling
            page.mouse.wheel(0, 1500)
            # Increased timeout slightly to give Flask server room to breathe on single core
            page.wait_for_timeout(1200) 
        
            scroll_count += 1
            log(f"Collected and enriched {len(all_reels_enriched)} reels... (Scroll {scroll_count})")

            if scroll_count > 0 and scroll_count % 5 == 0:
                # More frequent rests to prevent CPU pinning
                rest_time = random.uniform(3, 5)
                time.sleep(rest_time)


    except Exception as e:
        log(f"Error during scrape: {e}")
    finally:
        log(f"Real-time fetch complete. Captured {len(all_reels_enriched)} reels.")
        
        # Clean up or release
        if browser_fb:
            browser_fb.close()
        else:
            release_pooled_browser(current_session)

        update_session_usage(current_session)






    
    # Final Step: Remove any reels older than the link pasted (handles pinned posts)
    filtered_data = [d for d in all_reels_enriched if d.get('timestamp', 0) >= target_ts]
    
    # Save locally as CSV ONLY if it's CLI mode (not API)
    if not on_data and not logger:
        import csv
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_file = f"{username}_{timestamp}.csv"
        
        if filtered_data:
            keys = filtered_data[0].keys()
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                dict_writer = csv.DictWriter(f, fieldnames=keys)
                dict_writer.writeheader()
                dict_writer.writerows(filtered_data)
            log(f"SUCCESS: Result saved as {output_file} (Filtered: {len(filtered_data)}/{len(all_reels_enriched)})")
        else:
            log("No data to save locally.")
    
    return filtered_data, username


def start_cloudflared_tunnel():
    """
    Attempts to start a cloudflared tunnel in a separate background thread.
    Assumes cloudflared is installed and PATH-accessible.
    """
    import subprocess
    import threading

    def run_tunnel():
        log_to_file("Starting Cloudflared tunnel to yourdomain.com...")
        try:
            # Added --protocol http2 to avoid QUIC/UDP timeout issues on some networks
            # Optimization: Added --ha-connections 2 and --origin-ca-pool to resolve Windows warnings
            process = subprocess.Popen(
                ["cloudflared", "tunnel", "--protocol", "http2", "--ha-connections", "1", "--loglevel", "warn", "--origin-ca-pool", r"C:\Users\Administrator\.cloudflared\cert.pem", "--config", r"C:\Users\Administrator\.cloudflared\config.yml", "run", "mygram"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )



            
            # Thread to read Cloudflare logs and pipe to our log file (filtered for brevity)
            for line in iter(process.stdout.readline, ''):
                if line:
                    l = line.strip()
                    # Filter out verbose "Registered" and "curve" messages to save CPU/IO on single core
                    if any(x in l for x in ["Registered tunnel", "connection curve", "metrics server", "ICMP proxy", "Settings:"]):
                        continue
                    log_to_file(f"[Cloudflare] {l}")

            process.stdout.close()
            process.wait()
        except Exception as e:
            log_to_file(f"Failed to start Cloudflared: {e}")

    tunnel_thread = threading.Thread(target=run_tunnel, daemon=True)
    tunnel_thread.start()


def scrape_multiple_urls(urls, logger=None, cancel_event=None):
    """
    Takes a list of Instagram Reel/Post URLs and scrapes their data in parallel.
    """
    def is_cancelled():
        if cancel_event and cancel_event.is_set(): return True
        return False
    
    def log(msg):

        if logger: logger(msg)
        log_to_file(msg)


    if not urls:
        return []

    if not get_all_sessions():
        log(f"Error: No sessions found. Please run with --login first.")
        return []


    # Extract shortcodes
    shortcodes = []
    for url in urls:
        match = re.search(r'/(?:reels?|p)/([^/?#&]+)', url)
        if match:
            shortcodes.append(match.group(1))
    
    if not shortcodes:
        log("No valid Instagram URLs found.")
        return []

    # Filter out duplicates while preserving order
    seen = set()
    unique_scs = []
    for sc in shortcodes:
        if sc not in seen:
            seen.add(sc)
            unique_scs.append(sc)

    with sync_playwright() as p:
        # Pick ONE session for this entire request
        current_session = get_next_session() or SESSION_FILE
        log(f"Launching browser to scrape {len(unique_scs)} URLs using session: {os.path.basename(current_session)}")
        
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=current_session)

        
        def block_media(route):
            if route.request.resource_type in ["image", "media", "font"]:
                route.abort()
            else:
                route.continue_()
        
        page = context.new_page()
        page.route("**/*", block_media)
        
        # Step 3: Fetch in Batches
        enriched_data = []
        chunk_size = 100
        
        # Quick session activation
        page.goto("https://www.instagram.com/robots.txt")
        
        for i in range(0, len(unique_scs), chunk_size):
            if is_cancelled():
                log("CANCELLATION: Stop requested. Aborting bulk scrape.")
                browser.close()
                update_session_usage(current_session)
                return enriched_data

            chunk = unique_scs[i:i + chunk_size]

            log(f"Fetching batch {i//chunk_size + 1} ({len(chunk)} reels)...")
            
            batch_results = page.evaluate("""
                async (shortcodes) => {
                    const csrf = document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || "";
                    const fetchOne = async (sc) => {
                        try {
                            const res = await fetch("https://www.instagram.com/graphql/query", {
                                method: "POST",
                                headers: {
                                    "Content-Type": "application/x-www-form-urlencoded",
                                    "X-CSRFToken": csrf,
                                    "X-Instagram-AJAX": "1",
                                    "X-Requested-With": "XMLHttpRequest",
                                    "X-IG-App-ID": "936619743392459",
                                    "X-ASBD-ID": "129477"
                                },
                                body: new URLSearchParams({ 
                                    variables: JSON.stringify({ shortcode: sc }), 
                                    doc_id: "8845758582119845" 
                                }).toString(),
                            });
                            const json = await res.json();
                            const m = json?.data?.xdt_shortcode_media;
                            if (!m) return { shortcode: sc, error: "No data", raw: json };
                            const views = m.video_view_count || 0;
                            const plays = m.video_play_count || 0;
                            const hook_percentage = plays > 0 ? ((views / plays) * 100).toFixed(2) : "0.00";
                            return {
                                shortcode: m.shortcode,
                                author: m.owner?.username,
                                likes: m.edge_media_preview_like?.count || m.edge_liked_by?.count || 0,
                                comments: m.edge_media_to_comment?.count || m.edge_media_to_parent_comment?.count || 0,
                                views: views,
                                plays: plays,
                                hook_percentage: hook_percentage,
                                text: m.edge_media_to_caption?.edges?.[0]?.node?.text || "",
                                timestamp: m.taken_at_timestamp,
                                date: m.taken_at_timestamp ? new Date(m.taken_at_timestamp * 1000).toISOString() : "",
                                url: `https://www.instagram.com/reels/${m.shortcode}/`
                            };
                        } catch(e) { return { shortcode: sc, error: e.message }; }
                    };
                    return await Promise.all(shortcodes.map(sc => fetchOne(sc)));
                }
            """, chunk)
            
            # Check for rate limits
            for item in batch_results:
                if "raw" in item:
                    raw = item["raw"]
                    if raw.get('message') == 'feedback_required' or raw.get('spam'):
                        mark_session_sleep(current_session)
                        break

            enriched_data.extend(batch_results)


            if i + chunk_size < len(unique_scs):
                delay = random.uniform(0, 1)
                log(f"Batch complete. Resting for {delay:.2f}s...")
                time.sleep(delay)

        browser.close()
        update_session_usage(current_session)
        return [d for d in enriched_data if "error" not in d]




def run_flask_server():
    """
    Starts a Flask server with security, CORS, and an automatic Cloudflared tunnel.
    """
    try:
        from flask import Flask, request, jsonify, Response, stream_with_context, make_response, render_template_string, send_from_directory

        import io
        import csv
        import uuid
        import threading
        import time
        from queue import Queue
    except ImportError:
        print("Flask not found. Please install it with: pip install flask")
        return

    # Heartbeat thread to detect hangs
    def heartbeat_monitor():
        while True:
            # Just a small indicator to prove the process is alive and context switching is working
            # Use sys.stderr to bypass potential stdout buffering issues
            sys.stderr.write(".")
            sys.stderr.flush()
            time.sleep(10)
    threading.Thread(target=heartbeat_monitor, daemon=True).start()

    # Resource Monitor & Cleanup Thread
    def resource_monitor_and_cleanup():
        while True:
            try:
                # 1. Monitor Resources (OUTSIDE any locks)
                mem = psutil.virtual_memory()
                proc = psutil.Process()
                usage = proc.memory_info().rss / (1024 * 1024)
                disk = shutil.disk_usage(".")
                ts = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
                print(f"\n{ts} [System] Script RAM: {usage:.1f}MB | System RAM: {mem.percent}% | Disk Free: {disk.free / (1024**3):.1f}GB")

                # 2. Cleanup old tasks (older than 2 hours)
                now = time.time()
                to_delete_ids = []
                to_delete_paths = []

                # Only hold the lock briefly to identify targets
                with scrape_tasks_lock:
                    for tid, task in scrape_tasks.items():
                        if now - task.get("timestamp", 0) > 7200: # 2 hours
                            to_delete_ids.append(tid)
                            path = task.get("results_path")
                            if path: to_delete_paths.append(path)
                    
                    for tid in to_delete_ids:
                        if tid in scrape_tasks: del scrape_tasks[tid]
                        if tid in scrapes: del scrapes[tid]
                
                # Perform the actual deletion OUTSIDE the lock
                deleted_count = 0
                for path in to_delete_paths:
                    if os.path.exists(path):
                        try: 
                            os.remove(path)
                            deleted_count += 1
                        except: pass
                
                if to_delete_ids:
                    print(f"[Cleanup] Removed {len(to_delete_ids)} old tasks from memory. Deleted {deleted_count} files.")

                # 3. Suggest GC
                import gc
                gc.collect()

                # 4. Refresh Browser Pool (Worker thread will handle this, we just signal if needed)
                # initialize_browser_pool() call removed to prevent thread conflict

            except Exception as e: 
                print(f"[Monitor Error] {e}")
            time.sleep(60)


    
    threading.Thread(target=resource_monitor_and_cleanup, daemon=True).start()

    # Create outputs directory
    os.makedirs("outputs", exist_ok=True)

    # Start the tunnel in the background
    start_cloudflared_tunnel()

    app = Flask(__name__)

    @app.route('/ping', methods=['GET'])
    def ping():
        return "pong", 200

    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory(os.path.join(app.root_path, 'static'),
                                   'favicon.ico', mimetype='image/vnd.microsoft.icon')

    @app.after_request
    def add_cors_headers(response):

        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "GET,PUT,POST,DELETE,OPTIONS")
        response.headers.add("Access-Control-Expose-Headers", "Content-Disposition")
        return response

    # --- Security & Authentication ---
    API_SECRET = "MyGram_Security_Key_2026" # Change this to something very unique

    _users_cache = None
    _users_last_load = 0

    def load_users():
        if os.path.exists("users.json"):
            try:
                with open("users.json", "r") as f:
                    users = json.load(f)
                    # Ensure all users have stats fields
                    for u in users:
                        if "requested_links" not in u: u["requested_links"] = 0
                        if "files_generated" not in u: u["files_generated"] = 0
                        if "total_rows_scraped" not in u: u["total_rows_scraped"] = 0
                    return users
            except: return []
        return []

    def save_users(users_list):
        with open("users.json", "w") as f:
            json.dump(users_list, f, indent=2)



    def update_user_stats(username, links=0, files=0, rows=0):
        """Thread-safe update of user statistics."""
        users = load_users()
        for u in users:
            if u["username"] == username:
                u["requested_links"] = u.get("requested_links", 0) + links
                u["files_generated"] = u.get("files_generated", 0) + files
                u["total_rows_scraped"] = u.get("total_rows_scraped", 0) + rows
                break
        save_users(users)

    def verify_sig(username, device_id, received_sig):
        """Verifies that the request came from the app using a shared secret."""
        if not received_sig: return False
        data = f"{username}{device_id}{API_SECRET}"
        expected_sig = hashlib.md5(data.encode()).hexdigest()
        return expected_sig == received_sig

    def check_auth(username, device_id, sig):
        """Verifies user, device lock, and request signature."""
        # 1. Verify Signature (App Integrity)
        if not verify_sig(username, device_id, sig):
            return False, "Invalid request signature (Security Error)"

        # 2. Verify User & Device Lock
        users = load_users()
        for u in users:
            if u["username"] == username:
                # Check Expiration
                valid_till = datetime.strptime(u["valid_till"], "%Y-%m-%d %H:%M:%S")
                if datetime.now() > valid_till:
                    return False, "Account expired"
                
                # Check Device Lock
                if u.get("device_id") and u["device_id"] != device_id:
                    return False, "Account in use on another device"
                
                return True, "OK"
        return False, "User not found"

    @app.route('/login', methods=['POST'])
    def api_login():
        log_to_file(f"[Incoming Request] POST /login from {request.remote_addr}")
        data = request.json or {}

        username = data.get('username')
        password = data.get('password')
        device_id = data.get('device_id')
        sig = data.get('sig') # Optional for login, or use password in sig

        if not username or not password or not device_id:
            return jsonify({"error": "Missing credentials"}), 400
        
        # Verify login signature
        if not verify_sig(username, device_id, sig):
            return jsonify({"error": "Security validation failed"}), 403

        users = load_users()
        found = False
        for u in users:
            if u["username"] == username and u["password"] == password:
                valid_till = datetime.strptime(u["valid_till"], "%Y-%m-%d %H:%M:%S")
                if datetime.now() > valid_till:
                    return jsonify({"error": "Account has expired"}), 403
                
                u["device_id"] = device_id
                save_users(users)
                found = True
                break
        
        if found:
            return jsonify({
                "status": "success",
                "message": "Logged in successfully",
                "valid_till": valid_till.strftime("%Y-%m-%d %H:%M:%S")
            })
        return jsonify({"error": "Invalid username or password"}), 401

    # --- Admin Panel Endpoints ---
    ADMIN_USER = "admin"
    ADMIN_PASS = "lollipop"

    @app.route('/admin')
    def admin_dashboard():
        log_to_file(f"[Incoming Request] GET /admin from {request.remote_addr}")
        # Premium Admin Dashboard UI - White & Teal Theme

        return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MyGram Admin Panel</title>
    <link rel="icon" type="image/x-icon" href="/favicon.ico">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">

    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { font-family: 'Inter', sans-serif; background: #f0fdfa; color: #0f172a; }
        .glass { background: rgba(255, 255, 255, 0.9); backdrop-filter: blur(10px); border: 1px solid rgba(20, 184, 166, 0.2); }
        .btn-teal { background: linear-gradient(135deg, #0d9488 0%, #14b8a6 100%); color: white; transition: all 0.3s ease; }
        .btn-teal:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(20, 184, 166, 0.3); }
        input { background: #ffffff !important; border: 1px solid #ccfbf1 !important; color: #134e4a !important; }
        input:focus { border-color: #14b8a6 !important; ring-color: #14b8a6 !important; }
        .status-badge { padding: 4px 10px; border-radius: 20px; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; }
        .active-badge { background: #ccfbf1; color: #0f766e; }
        .expired-badge { background: #fee2e2; color: #b91c1c; }
        .card-shadow { box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05), 0 8px 10px -6px rgba(0, 0, 0, 0.05); }
    </style>
</head>
<body class="min-h-screen p-4 md:p-8">
    <div id="login-section" class="max-w-md mx-auto mt-24 p-10 glass rounded-3xl card-shadow">
        <h1 class="text-4xl font-bold text-center mb-2 text-teal-800">MyGram</h1>
        <p class="text-center text-teal-600 mb-10 font-medium">Admin Management Portal</p>
        <div class="space-y-6">
            <div>
                <label class="block text-xs font-bold uppercase tracking-wider text-teal-700 mb-2">Admin User</label>
                <input type="text" id="admin-user" class="w-full px-4 py-3 rounded-2xl focus:outline-none focus:ring-2 focus:ring-teal-500 shadow-sm">
            </div>
            <div>
                <label class="block text-xs font-bold uppercase tracking-wider text-teal-700 mb-2">Secure Password</label>
                <input type="password" id="admin-pass" class="w-full px-4 py-3 rounded-2xl focus:outline-none focus:ring-2 focus:ring-teal-500 shadow-sm">
            </div>
            <button onclick="login()" class="w-full btn-teal py-4 rounded-2xl font-bold text-lg mt-4 shadow-lg shadow-teal-100">Access Dashboard</button>
        </div>
    </div>

    <div id="dashboard-section" class="hidden max-w-6xl mx-auto">
        <div class="flex flex-col md:flex-row justify-between items-end md:items-center mb-10 gap-4">
            <div>
                <h1 class="text-4xl font-black text-teal-900 tracking-tight">Admin Dashboard</h1>
                <p class="text-teal-600 font-medium mt-1">Control center for user licenses & device locks</p>
            </div>
            <button onclick="openAddModal()" class="btn-teal px-8 py-3 rounded-2xl font-bold shadow-lg shadow-teal-100 flex items-center gap-2">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clip-rule="evenodd" />
                </svg>
                Create New License
            </button>
            <button onclick="logout()" class="bg-red-50 text-red-500 border border-red-100 px-6 py-3 rounded-2xl font-bold hover:bg-red-100 transition-all">
                Logout
            </button>
        </div>


        <div class="glass rounded-3xl overflow-hidden card-shadow">
            <table class="w-full text-left">
                <thead class="bg-teal-50/50 border-b border-teal-100">
                    <tr>
                        <th class="px-8 py-5 text-xs font-black uppercase tracking-widest text-teal-800">User Identity</th>
                        <th class="px-8 py-5 text-xs font-black uppercase tracking-widest text-teal-800">Usage Stats</th>
                        <th class="px-8 py-5 text-xs font-black uppercase tracking-widest text-teal-800">Password</th>
                        <th class="px-8 py-5 text-xs font-black uppercase tracking-widest text-teal-800">Device Lock</th>
                        <th class="px-8 py-5 text-xs font-black uppercase tracking-widest text-teal-800">Validity Period</th>
                        <th class="px-8 py-5 text-xs font-black uppercase tracking-widest text-teal-800">Actions</th>
                    </tr>
                </thead>
                <tbody id="user-table-body" class="divide-y divide-teal-50">
                    <!-- Dynamic Content -->
                </tbody>
            </table>
        </div>
    </div>

    <!-- Modal -->
    <div id="user-modal" class="hidden fixed inset-0 bg-teal-900/20 backdrop-blur-md flex items-center justify-center p-4 z-50">
        <div class="bg-white max-w-md w-full p-10 rounded-3xl shadow-2xl relative border border-teal-50">
            <h2 id="modal-title" class="text-3xl font-black text-teal-900 mb-8">User License</h2>
            <div class="space-y-5">
                <input type="hidden" id="edit-mode" value="false">
                <div>
                    <label class="block text-xs font-bold text-teal-700 mb-2 uppercase">Username</label>
                    <input type="text" id="username" class="w-full px-4 py-3 rounded-xl focus:ring-teal-500 shadow-sm">
                </div>
                <div>
                    <label class="block text-xs font-bold text-teal-700 mb-2 uppercase">Access Password</label>
                    <input type="text" id="password" class="w-full px-4 py-3 rounded-xl focus:ring-teal-500 shadow-sm">
                </div>
                <div>
                    <label class="block text-xs font-bold text-teal-700 mb-2 uppercase">Expiry Date (YYYY-MM-DD HH:MM:SS)</label>
                    <input type="text" id="valid_till" value="2026-12-31 23:59:59" class="w-full px-4 py-3 rounded-xl focus:ring-teal-500 shadow-sm">
                </div>
                <div>
                    <label class="block text-xs font-bold text-teal-700 mb-2 uppercase text-xs">Assigned Device ID</label>
                    <input type="text" id="device_id" placeholder="Empty = No device lock" class="w-full px-4 py-3 rounded-xl focus:ring-teal-500 shadow-sm">
                </div>
                <div class="flex gap-4 mt-10">
                    <button onclick="closeModal()" class="flex-1 bg-slate-100 hover:bg-slate-200 text-slate-600 py-4 rounded-2xl font-bold transition-all">Cancel</button>
                    <button onclick="saveUser()" class="flex-1 btn-teal py-4 rounded-2xl font-bold shadow-lg shadow-teal-100">Save Data</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentUser = null;
        let authToken = "";

        // Check for persistent login on load
        window.onload = function() {
            const savedToken = localStorage.getItem('mygram_admin_token');
            if (savedToken) {
                authToken = savedToken;
                document.getElementById('login-section').classList.add('hidden');
                document.getElementById('dashboard-section').classList.remove('hidden');
                loadUsers();
            }
        };

        async function login() {
            const user = document.getElementById('admin-user').value;
            const pass = document.getElementById('admin-pass').value;
            
            const res = await fetch('/api/admin/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: user, password: pass })
            });
            
            if (res.ok) {
                const data = await res.json();
                authToken = data.token;
                localStorage.setItem('mygram_admin_token', authToken);
                document.getElementById('login-section').classList.add('hidden');
                document.getElementById('dashboard-section').classList.remove('hidden');
                loadUsers();
            } else {
                alert('Invalid Credentials');
            }
        }

        function logout() {
            localStorage.removeItem('mygram_admin_token');
            location.reload();
        }


        async function loadUsers() {
            const res = await fetch('/api/admin/users', {
                headers: { 'Authorization': authToken }
            });
            const users = await res.json();
            const tbody = document.getElementById('user-table-body');
            tbody.innerHTML = '';
            
            const now = new Date();

            users.forEach(u => {
                const isExpired = new Date(u.valid_till) < now;
                const tr = document.createElement('tr');
                tr.className = 'hover:bg-slate-800/30 transition-colors';
                tr.innerHTML = `
                    <td class="px-6 py-4">
                        <div class="font-semibold text-teal-700">${u.username}</div>
                        <div class="text-xs text-teal-500">Member</div>
                    </td>
                    <td class="px-6 py-4">
                        <div class="text-xs font-bold text-teal-800">${u.requested_links} Links</div>
                        <div class="text-[10px] text-teal-500">${u.files_generated} Files | ${u.total_rows_scraped} Rows</div>
                    </td>
                    <td class="px-6 py-4 text-sm font-mono text-slate-300">${u.password}</td>
                    <td class="px-6 py-4">
                        <span class="text-xs font-mono ${u.device_id ? 'text-indigo-400' : 'text-slate-500'}">
                            ${u.device_id ? u.device_id : 'Unlocked'}
                        </span>
                    </td>
                    <td class="px-6 py-4 text-sm">
                        <span class="status-badge ${isExpired ? 'expired-badge' : 'active-badge'}">
                            ${u.valid_till}
                        </span>
                    </td>
                    <td class="px-6 py-4 space-x-3">
                        <button onclick="editUser('${u.username}')" class="text-indigo-400 hover:text-indigo-300 text-sm font-medium">Edit</button>
                        <button onclick="deleteUser('${u.username}')" class="text-red-400 hover:text-red-300 text-sm font-medium">Delete</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        }

        function openAddModal() {
            document.getElementById('edit-mode').value = "false";
            document.getElementById('modal-title').innerText = "Add New User";
            document.getElementById('username').value = "";
            document.getElementById('username').disabled = false;
            document.getElementById('password').value = "";
            document.getElementById('device_id').value = "";
            document.getElementById('user-modal').classList.remove('hidden');
        }

        async function editUser(username) {
            const res = await fetch('/api/admin/users', { headers: { 'Authorization': authToken } });
            const users = await res.json();
            const user = users.find(u => u.username === username);
            if (!user) return;

            document.getElementById('edit-mode').value = "true";
            document.getElementById('modal-title').innerText = "Edit User";
            document.getElementById('username').value = user.username;
            document.getElementById('username').disabled = true;
            document.getElementById('password').value = user.password;
            document.getElementById('valid_till').value = user.valid_till;
            document.getElementById('device_id').value = user.device_id || "";
            document.getElementById('user-modal').classList.remove('hidden');
        }

        function closeModal() {
            document.getElementById('user-modal').classList.add('hidden');
        }

        async function saveUser() {
            const user = {
                username: document.getElementById('username').value,
                password: document.getElementById('password').value,
                valid_till: document.getElementById('valid_till').value,
                device_id: document.getElementById('device_id').value || null
            };

            const res = await fetch('/api/admin/users', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    'Authorization': authToken
                },
                body: JSON.stringify(user)
            });

            if (res.ok) {
                closeModal();
                loadUsers();
            } else {
                alert('Error saving user');
            }
        }

        async function deleteUser(username) {
            if (!confirm('Are you sure you want to delete this user?')) return;
            
            const res = await fetch(`/api/admin/users?username=${username}`, {
                method: 'DELETE',
                headers: { 'Authorization': authToken }
            });

            if (res.ok) {
                loadUsers();
            } else {
                alert('Error deleting user');
            }
        }
    </script>
</body>
</html>
        """)

    @app.route('/api/admin/login', methods=['POST'])
    def api_admin_login():
        log_to_file(f"[Incoming Request] POST /api/admin/login from {request.remote_addr}")
        data = request.json or {}

        if data.get('username') == ADMIN_USER and data.get('password') == ADMIN_PASS:
            # Simple token (in real app use JWT)
            return jsonify({"status": "success", "token": "admin_master_access_2026"})
        return jsonify({"error": "Invalid credentials"}), 401

    @app.route('/api/admin/users', methods=['GET', 'POST', 'DELETE'])
    def api_admin_users():
        token = request.headers.get('Authorization')
        if token != "admin_master_access_2026":
            return jsonify({"error": "Unauthorized"}), 401

        users = load_users()

        if request.method == 'GET':
            return jsonify(users)

        elif request.method == 'POST':
            data = request.json
            found = False
            for i, u in enumerate(users):
                if u["username"] == data["username"]:
                    users[i] = data
                    found = True
                    break
            if not found:
                users.append(data)
            save_users(users)
            return jsonify({"status": "success"})

        elif request.method == 'DELETE':
            username = request.args.get('username')
            new_users = [u for u in users if u["username"] != username]
            save_users(new_users)
            return jsonify({"status": "success"})

    @app.route('/submit', methods=['POST', 'GET'])
    def submit_request():
        # Log incoming request
        client_ip = request.remote_addr
        method = request.method
        path = request.path
        log_to_file(f"[Incoming Request] {method} {path} from {client_ip}")

        token = request.args.get('token')
        username = request.args.get('username') or (request.json.get('username') if request.is_json else None)

        device_id = request.args.get('device_id') or (request.json.get('device_id') if request.is_json else None)
        sig = request.args.get('sig') or (request.json.get('sig') if request.is_json else None)

        if token != 'lollipop':
            if not username or not device_id or not sig:
                return jsonify({"error": "Authentication and Signature required"}), 401
            
            is_valid, msg = check_auth(username, device_id, sig)
            if not is_valid:
                return jsonify({"error": msg}), 403

        reel_url = request.args.get('url') or (request.json.get('url') if request.is_json else None)
        if not reel_url:
            return jsonify({"error": "Missing URL"}), 400

        # Check if this URL is already being scraped or was recently scraped by this user
        with scrape_tasks_lock:
            for existing_id, task in scrape_tasks.items():
                if task.get("url") == reel_url and task.get("username") == username:
                    # If it's active or less than 1 hour old, reuse it
                    if task["status"] in ["running", "queued"] or (time.time() - task.get("timestamp", 0) < 3600):
                        return jsonify({
                            "request_id": existing_id,
                            "status": task["status"],
                            "message": "Resuming existing scrape task."
                        })

        session_id = str(uuid.uuid4())
        with scrape_tasks_lock:
            scrape_tasks[session_id] = {
                "logs": [],
                "event": threading.Event(),
                "status": "queued",
                "url": reel_url,
                "username": username,
                "auth_info": {
                    "username": username,
                    "device_id": device_id,
                    "sig": sig
                },
                "timestamp": time.time()
            }
        
        with queue_lock:
            request_queue.append(session_id)

        return jsonify({
            "request_id": session_id,
            "status": "queued",
            "message": "Scrape started in background."
        })

    def worker_loop():
        """Single persistent worker thread to process the queue and manage the pool."""
        log_to_file("[Worker] Background worker loop started.")
        
        # Initialize the browser pool at startup in THIS thread
        try:
            initialize_browser_pool()
        except Exception as e:
            log_to_file(f"[Worker] Pool Initialization Failed: {e}")

        while True:
            # Periodically refresh the pool if idle to handle session rotation
            try:
                initialize_browser_pool()
            except: pass

            target_id = None
            with queue_lock:
                if request_queue:
                    target_id = request_queue.pop(0) 
            
            if target_id:
                try:
                    run_background_scrape(target_id)
                except Exception as e:
                    log_to_file(f"[Worker] Error processing task {target_id}: {e}")
            else:
                time.sleep(5) # Wait for new tasks


    def run_background_scrape(session_id):
        reel_url = None
        cancel_evt = None
        auth = None
        
        with scrape_tasks_lock:
            if session_id not in scrape_tasks: return
            task = scrape_tasks[session_id]
            reel_url = task["url"]
            cancel_evt = task["event"]
            auth = task.get("auth_info")
            task["status"] = "running"
        
        def logger_callback(msg):
            with scrape_tasks_lock:
                if session_id in scrape_tasks:
                    scrape_tasks[session_id]["logs"].append(msg)
                    if len(scrape_tasks[session_id]["logs"]) > 100:
                        scrape_tasks[session_id]["logs"].pop(0)

        # We do NOT hold scrape_tasks_lock during the heavy scraping call
        with scrape_lock:
            if cancel_evt.is_set():
                with scrape_tasks_lock: 
                    if session_id in scrape_tasks:
                        scrape_tasks[session_id]["status"] = "cancelled"
                return

            try:
                # Pass auth info for remote config check
                auth = task.get("auth_info")
                results, username = scrape_since_reel(reel_url, logger=logger_callback, cancel_event=cancel_evt, auth_info=auth)
                
                if cancel_evt.is_set():
                    with scrape_tasks_lock: scrape_tasks[session_id]["status"] = "cancelled"
                    return
                
                if results is None or (not results and username is None):
                    with scrape_tasks_lock: scrape_tasks[session_id]["status"] = "error"
                    return

                # Save to file instead of memory
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                filename = f"{username}_{timestamp}.csv"
                filepath = os.path.join("outputs", f"{session_id}.csv")
                
                with open(filepath, "w", newline="", encoding="utf-8") as f:
                    if results:
                        keys = results[0].keys()
                        dict_writer = csv.DictWriter(f, fieldnames=keys)
                        dict_writer.writeheader()
                        dict_writer.writerows(results)
                
                with scrape_tasks_lock:
                    scrape_tasks[session_id]["status"] = "completed"
                    scrape_tasks[session_id]["filename"] = filename
                    scrape_tasks[session_id]["results_path"] = filepath
                    # Also update legacy 'scrapes' for download endpoint if needed
                    scrapes[session_id] = {"status": "done", "filename": filename, "path": filepath}
                
                # UPDATE STATS: Successfully finished!
                req_user = task.get("username")
                if req_user:
                    update_user_stats(req_user, links=1, files=1, rows=len(results) if results else 0)

            except Exception as e:
                logger_callback(f"ERROR: {str(e)}")
                with scrape_tasks_lock: scrape_tasks[session_id]["status"] = "error"

    @app.route('/status/<req_id>', methods=['GET'])
    def get_status(req_id):
        with scrape_tasks_lock:
            if req_id in scrape_tasks:
                task = scrape_tasks[req_id]
                response = {
                    "request_id": req_id,
                    "status": task["status"],
                    "logs": task["logs"][-5:] # Return last 5 logs
                }
                if task["status"] == "completed":
                    response["download_url"] = f"http://{request.host}/download/{req_id}"
                return jsonify(response)
        return jsonify({"error": "ID not found"}), 404

    @app.route('/cancel/<req_id>', methods=['POST', 'GET'])
    def cancel_request(req_id):
        with scrape_tasks_lock:
            if req_id in scrape_tasks:
                scrape_tasks[req_id]["event"].set()
                scrape_tasks[req_id]["status"] = "cancelled"
                with queue_lock:
                    if req_id in request_queue:
                        request_queue.remove(req_id)
                return jsonify({"status": "cancelled"}), 200
        return jsonify({"error": "ID not found"}), 404

    @app.route('/download/<session_id>', methods=['GET'])
    def download_result(session_id):
        filepath = os.path.join("outputs", f"{session_id}.csv")
        if not os.path.exists(filepath):
            return "File not found or still processing", 404
        
        filename = "scraped_data.csv"
        with scrape_tasks_lock:
            if session_id in scrape_tasks:
                filename = scrape_tasks[session_id].get("filename", filename)

        from flask import send_file
        return send_file(filepath, as_attachment=True, download_name=filename)

    @app.route('/stream', methods=['GET'])
    def stream_logs():
        # SSE implementation for real-time logs of a specific request
        req_id = request.args.get('id')
        if not req_id or req_id not in scrape_tasks:
            return jsonify({"error": "Invalid or missing Request ID"}), 400

        def generate():
            last_idx = 0
            last_heartbeat = time.time()
            while True:
                with scrape_tasks_lock:
                    if req_id not in scrape_tasks:
                        yield "data: Error: Task ID not found on server\n\n"
                        break
                        
                    task = scrape_tasks[req_id]
                    logs = task["logs"]
                    status = task["status"]
                    
                    if last_idx < len(logs):
                        for i in range(last_idx, len(logs)):
                            yield f"data: {logs[i]}\n\n"
                        last_idx = len(logs)
                        last_heartbeat = time.time()
                    
                    if status in ["completed", "cancelled", "error"]:
                        if status == "completed":
                            yield f"event: complete\ndata: {req_id}\n\n"
                        yield "data: __FINISHED__\n\n"
                        break
                
                # Heartbeat every 15 seconds to keep connection alive through proxies/tunnels
                if time.time() - last_heartbeat > 15:
                    yield ": keep-alive\n\n"
                    last_heartbeat = time.time()
                    
                time.sleep(1)

        response = Response(stream_with_context(generate()), mimetype='text/event-stream')
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Accel-Buffering'] = 'no'
        response.headers['Connection'] = 'keep-alive'
        return response

    @app.route('/scrape', methods=['GET'])
    def api_scrape():
        # Redirect legacy /scrape to the new background system for better stability
        return submit_request()

    @app.route('/upload', methods=['POST'])
    def api_upload():
        log_to_file(f"[Incoming Request] POST /upload from {request.remote_addr}")
        # Similar to submit_request but for files

        token = request.form.get('token') or request.args.get('token')
        if token != 'lollipop':
            return jsonify({"error": "Unauthorized"}), 401

        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400

        # Save the uploaded file temporarily and process it in background
        upload_id = str(uuid.uuid4())
        upload_path = os.path.join("outputs", f"upload_{upload_id}")
        file.save(upload_path)

        with scrape_tasks_lock:
            scrape_tasks[upload_id] = {
                "logs": ["File uploaded. Extracting URLs..."],
                "event": threading.Event(),
                "status": "queued",
                "upload_path": upload_path,
                "timestamp": time.time()
            }
        
        def process_upload_worker(uid, path):
            log_to_file(f"[Worker] Processing bulk upload task: {uid}")
            try:
                content = ""

                if path.lower().endswith(('.csv', '.xlsx', '.xls')):
                    # Use pandas if available, else skip or handle simply
                    try:
                        import pandas as pd
                        if path.lower().endswith('.csv'):
                            df = pd.read_csv(path)
                        else:
                            df = pd.read_excel(path)
                        content = df.to_string()
                    except:
                        with open(path, 'r', errors='ignore') as f: content = f.read()
                else:
                    with open(path, 'r', errors='ignore') as f: content = f.read()

                urls = re.findall(r'https?://(?:www\.)?instagram\.com/(?:reels?|p)/[A-Za-z0-9_-]+', content)
                if not urls:
                    with scrape_tasks_lock: scrape_tasks[uid]["status"] = "error"; scrape_tasks[uid]["logs"].append("No URLs found.")
                    return

                # For simplicity in this demo, we just scrape the first one or queue them
                # Real implementation should handle multiple URLs
                with scrape_tasks_lock:
                    scrape_tasks[uid]["url"] = urls[0] # Use first URL as target
                    scrape_tasks[uid]["logs"].append(f"Found {len(urls)} URLs. Starting scrape...")
                
                run_background_scrape(uid)

            except Exception as e:
                with scrape_tasks_lock: scrape_tasks[uid]["status"] = "error"; scrape_tasks[uid]["logs"].append(str(e))
            finally:
                if os.path.exists(path): os.remove(path)

        threading.Thread(target=process_upload_worker, args=(upload_id, upload_path), daemon=True).start()

        return jsonify({
            "request_id": upload_id,
            "status": "queued",
            "message": "Bulk upload received. Processing in background."
        })

    log_to_file("\n" + "="*50, to_console=True)
    log_to_file(f"Flask Server running at http://0.0.0.0:5030")
    log_to_file("NEW ENDPOINTS:")
    log_to_file(" - SUBMIT: /submit?url=<URL>&token=lollipop (POST/GET)")
    log_to_file(" - STATUS: /status/<req_id>")
    log_to_file(" - CANCEL: /cancel/<req_id>")
    log_to_file(" - LOGS:   /stream?id=<req_id>")
    log_to_file("="*50 + "\n")

    
    # Start the single persistent worker loop
    threading.Thread(target=worker_loop, daemon=True).start()

    app.run(host='0.0.0.0', port=5030, threaded=True)


def scrape_profile(username, limit=12, auth_info=None):
    """
    Scrapes multiple reels from a user's profile.
    """
    # 1. Using Hardcoded Config (Master server verification removed)
    doc_id = DOC_ID
    app_id = APP_ID
    asbd_id = ASBD_ID

    if not get_all_sessions():
        print(f"Error: No sessions found. Please run with --login first.")
        return


    current_session = get_next_session()
    if not current_session:
        print("Error: No sessions ready.")
        return
    with sync_playwright() as p:
        print(f"Launching headless Chromium browser (using {os.path.basename(current_session)}) to scrape profile: {username}...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=current_session)

        
        def block_media(route):
            if route.request.resource_type in ["image", "media", "font"]:
                route.abort()
            else:
                route.continue_()

        page = context.new_page()
        page.route("**/*", block_media)
        
        url = f"https://www.instagram.com/{username}/reels/"
        print(f"Navigating to {url}...")
        page.goto(url)
        page.wait_for_timeout(3000)
        
        shortcodes = page.evaluate("""
            () => {
                const links = Array.from(document.querySelectorAll('a[href*="/reel/"], a[href*="/p/"]'));
                return [...new Set(links.map(a => a.href.split('/').filter(Boolean).pop()))];
            }
        """)
        
        if not shortcodes:
            print("No reels found on profile.")
            browser.close()
            return

        print(f"Found {len(shortcodes)} reels. Scoping to top {limit}...")
        shortcodes = shortcodes[:limit]
        
        all_data = []
        for sc in shortcodes:
            print(f"Fetching data for {sc}...")
            data = page.evaluate("""
                async (shortcode) => {
                    try {
                        const csrf = document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || "";
                        const res = await fetch("https://www.instagram.com/graphql/query", {
                            method: "POST",
                            headers: {
                                "Content-Type": "application/x-www-form-urlencoded",
                                "X-CSRFToken": csrf,
                                "X-Instagram-AJAX": "1",
                                "X-Requested-With": "XMLHttpRequest",
                                "X-IG-App-ID": app_id,
                                "X-ASBD-ID": asbd_id
                            },
                            body: new URLSearchParams({ 
                                variables: JSON.stringify({ shortcode }), 
                                doc_id: doc_id
                            }).toString(),
                        });
                        const json = await res.json();
                        const m = json?.data?.xdt_shortcode_media;
                        if (!m) return null;
                        const views = m.video_view_count || 0;
                        const plays = m.video_play_count || 0;
                        const hook_percentage = plays > 0 ? ((views / plays) * 100).toFixed(2) : "0.00";

                        return {
                            shortcode: m.shortcode,
                            author: m.owner?.username,
                            likes: m.edge_media_preview_like?.count || m.edge_liked_by?.count || 0,
                            comments: m.edge_media_to_parent_comment?.count || 0,
                            views: views,
                            plays: plays,
                            hook_percentage: hook_percentage,
                            date: m.taken_at_timestamp ? new Date(m.taken_at_timestamp * 1000).toISOString() : "",
                        };
                    } catch (e) { return null; }
                }
            """, sc)
            if data: all_data.append(data)
            page.wait_for_timeout(300)
            
        browser.close()
        return all_data

if __name__ == "__main__":
    # Default to Server. Only show menu if explicitly requested via --menu or -m
    if "--menu" in sys.argv or "-m" in sys.argv:
        print("\n--- Instagram Scraper CLI ---")
        print("1. Login and Save Session (Headed)")
        print("2. Test Session (Headless)")
        print("3. Scrape Single Reel Data (Headless)")
        print("4. Bulk Scrape Profile Reels (Headless)")
        print("5. Scrape All Newer Reels (CLI)")
        print("6. Start API Server (Flask)")
        print("7. Fix Session / Manual Interact (Headed)")
        
        choice = input("\nSelect an option (1-7): ").strip()

        
        if choice == '1':
            login_and_save_session()
        elif choice == '2':
            test_headless_session()
        elif choice == '3':
            url = input("Enter Instagram Reel URL: ").strip()
            if url: scrape_reel(url)
        elif choice == '4':
            username = input("Enter Instagram Username: ").strip().replace('@', '')
            if username: scrape_profile(username)
        elif choice == '5':
            url = input("Enter the starting Reel URL: ").strip()
            if url: 
                # CLI mode uses hardcoded defaults inside the function if auth_info is None
                scrape_since_reel(url)
        elif choice == '6':
            run_flask_server()
        elif choice == '7':
            sessions = get_all_sessions()
            if not sessions:
                print(f"No session files found in '{SESSIONS_DIR}/' folder.")
            else:
                print("\nAvailable Sessions:")
                for i, s in enumerate(sessions):
                    print(f"{i+1}. {s}")
                
                idx = input("\nSelect session to fix (number): ").strip()
                try:
                    sel = sessions[int(idx)-1]
                    print(f"\n[FIX MODE] Opening {sel} in HEADED mode...")
                    print(">>> Please clear any notifications or checkpoints in the browser window.")
                    print(">>> Close the browser window when you are done to save the updated session.")
                    
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=False)
                        context = browser.new_context(storage_state=sel)
                        page = context.new_page()
                        page.goto("https://www.instagram.com/")
                        
                        print("\n>>> BROWSER OPEN. Interact with it as needed.")
                        print(">>> IMPORTANT: Press ENTER here in the terminal to SAVE and CLOSE.")
                        input("\nPress Enter to save and finish...")
                        
                        # Update the storage state file
                        context.storage_state(path=sel)
                        browser.close()
                    
                    print(f"\n[DONE] Session updated (if modified).")
                except Exception as e:
                    print(f"Error opening session: {e}")
        else:
            print("Invalid choice.")
    else:
        # Default behavior: Start Server
        log_to_file("[DEFAULT] Starting API Server (Flask)...")
        run_flask_server()


