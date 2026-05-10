import re
import time
import os
import json
from playwright.sync_api import sync_playwright
from config import DOC_ID, APP_ID, ASBD_ID
from logger_utils import log_to_file
from session_manager import (
    get_next_session_with_status, mark_session_sleep, get_all_sessions
)

def scrape_since_reel(reel_url, logger=None, cancel_event=None, auth_info=None):
    def log(msg):
        if logger: logger(msg)
        log_to_file(f"[Scraper] {msg}")

    def is_cancelled():
        return cancel_event and cancel_event.is_set()

    current_session = None
    while not current_session:
        if is_cancelled(): return [], None
        s, wait, all_sleeping = get_next_session_with_status()
        
        if all_sleeping:
            log(f"Waiting: All accounts sleeping. Next in {int(wait//60)}m.")
            time.sleep(30); continue
        if s: 
            current_session = s; break
        time.sleep(min(5, wait))

    log(f"Using session: {os.path.basename(current_session)}")
    
    playwright = None
    browser = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=current_session)
        page = context.new_page()
        
        # Block heavy assets to save bandwidth/RAM
        page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_())

        # Step 1: Navigate to Instagram first so we have access to cookies/document
        log("Warming up session context...")
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=60000)

        match = re.search(r'/(?:reels?|p)/([^/?#&]+)', reel_url)
        if not match: return [], None
        target_shortcode = match.group(1)

        # Step 2: Identify Author (Lightweight - No data enrichment yet)
        target_data = page.evaluate("""async ({shortcode, app_id, asbd_id, doc_id}) => {
            try {
                const csrf = document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || "";
                const res = await fetch("https://www.instagram.com/graphql/query", {
                    method: "POST",
                    headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": csrf, "X-IG-App-ID": app_id, "X-ASBD-ID": asbd_id },
                    body: new URLSearchParams({ variables: JSON.stringify({ shortcode }), doc_id: doc_id }).toString(),
                });
                const json = await res.json();
                const m = json?.data?.xdt_shortcode_media;
                if (!m) return { error: "No media data" };
                return { username: m.owner?.username, timestamp: m.taken_at_timestamp };
            } catch (e) { return { error: e.message }; }
        }""", {"shortcode": target_shortcode, "app_id": APP_ID, "asbd_id": ASBD_ID, "doc_id": DOC_ID})

        if not target_data or "error" in target_data:
            err = target_data.get('error', 'Unknown error')
            log(f"Failed to identify author: {err}")
            if any(x in str(err).lower() for x in ["feedback_required", "block", "login", "checkpoint"]):
                mark_session_sleep(current_session)
            return [], None

        username = target_data['username']
        target_ts = target_data['timestamp']
        all_reels = [] # Start empty, exactly like old code
        
        # Navigate and Scroll (With Retry to prevent '1 reel' timeout issues)
        profile_url = f"https://www.instagram.com/{username}/reels/"
        for attempt in range(1, 3):
            try:
                log(f"Navigating to profile (Attempt {attempt})...")
                page.goto(profile_url, timeout=60000)
                page.wait_for_timeout(3000)
                break
            except Exception as e:
                if attempt == 2: raise e
                log(f"Profile navigation failed, retrying... ({e})")
                page.wait_for_timeout(5000)
        
        log(f"Scrolling {username}'s profile...")
        found_target = False
        scanned_shortcodes = set() # Start empty so we don't ignore the target reel
        last_new_reel_time = time.time()

        for scroll_idx in range(1, 500): 
            if is_cancelled(): break
            
            # Extract visible reels
            new_links = page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a[href*="/reel/"], a[href*="/p/"]'));
                return [...new Set(links.map(a => a.href.split('/').filter(Boolean).pop()))];
            }""")

            # Filter for reels we haven't enriched yet
            to_enrich = [sc for sc in new_links if sc not in scanned_shortcodes][:12]
            
            if not to_enrich:
                # Stall Protection: If no new reels for 30s, stop.
                if time.time() - last_new_reel_time > 30:
                    log("Loading paused or end of profile reached. Finishing scrape.")
                    break
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(1600)
                continue
            
            # Reset stall timer
            last_new_reel_time = time.time()
            for sc in to_enrich: scanned_shortcodes.add(sc)
            
            # High-performance parallel fetch with RETRY logic
            batch_results = None
            for attempt in range(1, 4):
                try:
                    batch_results = page.evaluate("""async ({shortcodes, app_id, asbd_id, doc_id}) => {
                        const results = await Promise.all(shortcodes.map(async (shortcode) => {
                            try {
                                const csrf = document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || "";
                                const res = await fetch("https://www.instagram.com/graphql/query", {
                                    method: "POST",
                                    headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": csrf, "X-IG-App-ID": app_id, "X-ASBD-ID": asbd_id },
                                    body: new URLSearchParams({ variables: JSON.stringify({ shortcode }), doc_id: doc_id }).toString(),
                                });
                                const json = await res.json();
                                const m = json?.data?.xdt_shortcode_media;
                                if (!m) return null;
                                const v = m.video_view_count || 0;
                                const p = m.video_play_count || 0;
                                return {
                                    shortcode: m.shortcode,
                                    url: `https://www.instagram.com/reel/${m.shortcode}/`,
                                    author: m.owner?.username,
                                    likes: m.edge_media_preview_like?.count || m.edge_liked_by?.count || 0,
                                    comments: m.edge_media_to_parent_comment?.count || 0,
                                    views: v, plays: p,
                                    hook_percentage: p > 0 ? ((v / p) * 100).toFixed(2) : "0.00",
                                    date: m.taken_at_timestamp ? new Date(m.taken_at_timestamp * 1000).toISOString() : "",
                                    timestamp: m.taken_at_timestamp
                                };
                            } catch (e) { return null; }
                        }));
                        return results.filter(r => r !== null);
                    }""", {"shortcodes": to_enrich, "app_id": APP_ID, "asbd_id": ASBD_ID, "doc_id": DOC_ID})
                    if batch_results: break # Success!
                except Exception as e:
                    log(f"Batch attempt {attempt} failed: {e}")
                    page.wait_for_timeout(2000)

            if batch_results:
                for reel in batch_results:
                    # Include the target reel in the results before finishing
                    all_reels.append(reel)
                    
                    if reel['shortcode'] == target_shortcode:
                        found_target = True
                        log("Target reel reached and included in results.")
                        break
                
                log(f"collected and enriched {len(all_reels)} reels (scroll {scroll_idx})")
            else:
                log(f"Warning: Batch at scroll {scroll_idx} failed after 3 retries. Skipping...")

            if found_target: break
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(1600)
        
        log(f"Scrape finished. Total reels: {len(all_reels)}")
        return all_reels, username

    except Exception as e:
        # If we have data, we don't call it an "Error" to the user
        if 'all_reels' in locals() and all_reels:
            log(f"Scrape completed with {len(all_reels)} reels (Note: {e})")
            return all_reels, username
        
        log(f"Scrape failed: {e}")
        return [], None
    finally:
        try:
            if browser: browser.close()
            if playwright: playwright.stop()
        except: pass
def scrape_reel(url):
    """CLI helper to scrape a single reel."""
    all_reels, username = scrape_since_reel(url)
    if all_reels:
        print(f"\n[SUCCESS] Scraped data for author: {username}")
        import json
        print(json.dumps(all_reels[0], indent=2))
    else:
        print("[FAIL] Could not scrape reel.")

def scrape_profile(username):
    """CLI helper to scrape all reels from a profile."""
    # This uses a simple placeholder for now or can call the core logic
    print(f"Scraping profile: {username} (Starting CLI mode)...")
    url = f"https://www.instagram.com/{username}/reels/"
    results, found_user = scrape_since_reel(url)
    if results:
        print(f"[DONE] Collected {len(results)} reels.")
    else:
        print("[FAIL] No results found.")

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

    current_session = None
    while not current_session:
        if is_cancelled(): return []
        s, wait, all_sleeping = get_next_session_with_status()
        if all_sleeping:
            log(f"Waiting: All accounts sleeping. Next in {int(wait//60)}m.")
            time.sleep(30); continue
        if s: 
            current_session = s; break
        time.sleep(min(5, wait))

    log(f"Launching browser to scrape {len(unique_scs)} URLs using session: {os.path.basename(current_session)}")
    
    playwright = None
    browser = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=current_session)
        page = context.new_page()
        
        # Block heavy assets
        page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_())
        
        # Step 3: Fetch in Batches
        enriched_data = []
        chunk_size = 50 # Matching single-scrape batch size
        
        # Warm up session
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=60000)
        
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
                                url: `https://www.instagram.com/reel/${m.shortcode}/`,
                                author: m.owner?.username,
                                likes: m.edge_media_preview_like?.count || m.edge_liked_by?.count || 0,
                                comments: m.edge_media_to_parent_comment?.count || 0,
                                views: views,
                                plays: plays,
                                hook_percentage: hook_percentage,
                                date: m.taken_at_timestamp ? new Date(m.taken_at_timestamp * 1000).toISOString() : "",
                                timestamp: m.taken_at_timestamp
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
                        from session_manager import mark_session_sleep
                        mark_session_sleep(current_session)
                        break

            enriched_data.extend([item for item in batch_results if "error" not in item])

            if i + chunk_size < len(unique_scs):
                time.sleep(1) # Small batch delay
        
        log(f"Bulk scrape finished. Total collected: {len(enriched_data)}")
        return enriched_data

    except Exception as e:
        log(f"Bulk Scrape Failed: {e}")
        return []
    finally:
        try:
            if browser: browser.close()
            if playwright: playwright.stop()
        except: pass
