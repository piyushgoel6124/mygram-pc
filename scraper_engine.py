import re
import time
import os
import json
from playwright.sync_api import sync_playwright
from config import DOC_ID, APP_ID, ASBD_ID
from logger_utils import log_to_file
from session_manager import (
    get_next_session_with_status, get_pooled_browser, 
    release_pooled_browser, mark_session_sleep
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
    page = get_pooled_browser(current_session)
    
    if not page:
        log("No pooled browser available.")
        return [], None

    try:
        match = re.search(r'/(?:reels?|p)/([^/?#&]+)', reel_url)
        if not match: return [], None
        target_shortcode = match.group(1)

        # Step 2: Identify Author
        target_data = page.evaluate("""async ({shortcode, app_id, asbd_id, doc_id}) => {
            try {
                const csrf = document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || "";
                const res = await fetch("https://www.instagram.com/graphql/query", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-CSRFToken": csrf,
                        "X-IG-App-ID": app_id, "X-ASBD-ID": asbd_id
                    },
                    body: new URLSearchParams({ variables: JSON.stringify({ shortcode }), doc_id: doc_id }).toString(),
                });
                const json = await res.json();
                const m = json?.data?.xdt_shortcode_media;
                if (!m) return { error: "No media data" };
                return { 
                    username: m.owner?.username, 
                    timestamp: m.taken_at_timestamp,
                    data: {
                        shortcode: m.shortcode,
                        author: m.owner?.username,
                        likes: m.edge_media_preview_like?.count || m.edge_liked_by?.count || 0,
                        comments: m.edge_media_to_parent_comment?.count || 0,
                        views: m.video_view_count || 0,
                        plays: m.video_play_count || 0,
                        date: m.taken_at_timestamp ? new Date(m.taken_at_timestamp * 1000).toISOString() : ""
                    }
                };
            } catch (e) { return { error: e.message }; }
        }""", {"shortcode": target_shortcode, "app_id": APP_ID, "asbd_id": ASBD_ID, "doc_id": DOC_ID})

        if not target_data or "error" in target_data:
            err = target_data.get('error', 'Unknown error')
            log(f"Failed to identify author: {err}")
            
            # If blocked or feedback required, trigger hot-swap
            if any(x in str(err).lower() for x in ["feedback_required", "block", "login", "checkpoint"]):
                mark_session_sleep(current_session)
                
            return [], None

        username = target_data['username']
        target_ts = target_data['timestamp']
        all_reels = [target_data['data']]
        
        # Navigate and Scroll
        profile_url = f"https://www.instagram.com/{username}/reels/"
        page.goto(profile_url)
        page.wait_for_timeout(3000)
        
        log(f"Scrolling {username}'s profile...")
        found_target = False
        scanned_shortcodes = {target_shortcode}

        for _ in range(15): # Max 15 scrolls
            if is_cancelled(): break
            
            # Extract visible reels
            new_links = page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a[href*="/reel/"], a[href*="/p/"]'));
                return [...new Set(links.map(a => a.href.split('/').filter(Boolean).pop()))];
            }""")

            # BATCH ENRICHMENT: Process up to 12 new reels at once (High Performance)
            to_enrich = [sc for sc in new_links if sc not in scanned_shortcodes][:12]
            if not to_enrich:
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(1000)
                continue

            for sc in to_enrich: scanned_shortcodes.add(sc)
            
            log(f"Enriching batch of {len(to_enrich)} reels...")
            
            # This JS function fetches multiple reels in parallel inside the browser
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
                            shortcode: m.shortcode, author: m.owner?.username,
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

            if batch_results:
                for reel in batch_results:
                    if reel['timestamp'] < target_ts:
                        found_target = True
                        break
                    all_reels.append(reel)
                
                log(f"Progress: {len(all_reels)} reels collected.")

            if found_target: break
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(1000)
        
        return all_reels, username

    except Exception as e:
        log(f"Scrape Error: {e}")
        return [], None
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
