import os
import threading
import time
import uuid
import csv
import psutil
import shutil
import sys
from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory, render_template
from config import ADMIN_USER, ADMIN_PASS, OUTPUTS_DIR
from logger_utils import log_to_file
from auth_utils import load_users, save_users, check_auth, update_user_stats
from session_manager import initialize_browser_pool
from scraper_engine import scrape_since_reel

app = Flask(__name__)

# Global Task State
scrape_tasks = {}
scrape_tasks_lock = threading.Lock()
request_queue = []
queue_lock = threading.Lock()
scrape_lock = threading.Lock()

def health_monitor_loop():
    """Periodically checks and logs the health of all components."""
    log_to_file("[Health Monitor] Started.")
    while True:
        try:
            time.sleep(60)
            status = []
            
            # 1. Check Flask (Internal)
            try:
                import requests
                res = requests.get("http://localhost:5030/ping", timeout=5)
                if res.status_code == 200:
                    status.append("FLASK: OK")
                else:
                    status.append(f"FLASK: FAIL ({res.status_code})")
            except:
                status.append("FLASK: UNREACHABLE")

            # 2. Check Cloudflare Process
            cf_running = False
            for proc in psutil.process_iter(['name']):
                if 'cloudflared' in proc.info['name'].lower():
                    cf_running = True
                    break
            status.append(f"TUNNEL: {'OK' if cf_running else 'CRASHED'}")

            # 3. Check Worker Thread
            # (Simple check: is the thread alive)
            worker_alive = any(t.name == "ScraperWorker" and t.is_alive() for t in threading.enumerate())
            status.append(f"WORKER: {'OK' if worker_alive else 'STOPPED'}")

            # 4. Resources
            mem = psutil.virtual_memory().percent
            status.append(f"RAM: {mem}%")

            log_to_file(f"[HEALTH] | {' | '.join(status)} |")
            
        except Exception as e:
            log_to_file(f"[Health Monitor Error] {e}")

def worker_loop():
    log_to_file("[Worker] Background loop started.")
    try: initialize_browser_pool()
    except: pass
    
    while True:
        try: initialize_browser_pool() # Idle refresh
        except: pass

        target_id = None
        with queue_lock:
            if request_queue: target_id = request_queue.pop(0)
        
        if target_id:
            run_background_scrape(target_id)
        else:
            time.sleep(5)

def run_background_scrape(session_id):
    with scrape_tasks_lock:
        task = scrape_tasks.get(session_id)
        if not task: return
        reel_url = task["url"]
        cancel_evt = task["event"]
        task["status"] = "running"

    with scrape_lock:
        try:
            results, username = scrape_since_reel(reel_url, logger=lambda m: task["logs"].append(m), cancel_event=cancel_evt)
            
            if results:
                filepath = os.path.join(OUTPUTS_DIR, f"{session_id}.csv")
                with open(filepath, "w", newline="", encoding="utf-8") as f:
                    keys = results[0].keys()
                    dict_writer = csv.DictWriter(f, fieldnames=keys)
                    dict_writer.writeheader()
                    dict_writer.writerows(results)
                
                with scrape_tasks_lock:
                    task["status"] = "completed"
                    task["results_path"] = filepath
                
                update_user_stats(task.get("username"), links=1, rows=len(results))
            else:
                with scrape_tasks_lock: task["status"] = "error"
        except Exception as e:
            with scrape_tasks_lock: task["status"] = "error"

@app.route('/ping')
def ping():
    return "pong", 200

@app.route('/login', methods=['POST'])
def api_login():
    # ... logic for API login ...
    return jsonify({"status": "success"})

@app.route('/submit', methods=['POST'])
def api_submit():
    # ... logic for API submission ...
    return jsonify({"status": "queued"})

def start_cloudflared_tunnel():
    """Starts the Cloudflare tunnel in a background thread."""
    def run_tunnel():
        import subprocess
        log_to_file("[Cloudflare] Attempting to start tunnel...")
        try:
            # Optimized for single-core: http2, 1 connection, warn logs only
            cmd = [
                "cloudflared", "tunnel", "--url", "http://localhost:5030",
                "--protocol", "http2", "--ha-connections", "1", "--loglevel", "warn"
            ]
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                text=True, bufsize=1, universal_newlines=True
            )
            for line in iter(process.stdout.readline, ''):
                if line.strip():
                    log_to_file(f"[Cloudflare] {line.strip()}", to_console=False)
        except Exception as e:
            log_to_file(f"[Cloudflare Error] Could not start tunnel: {e}")
            log_to_file("[System] Tunnel failed, but server is running locally at http://localhost:5030")

    threading.Thread(target=run_tunnel, daemon=True).start()

@app.route('/admin')
def admin_dashboard():
    log_to_file(f"[Incoming Request] GET /admin from {request.remote_addr}")
    return render_template('admin.html')

def start_server():
    log_to_file("[System] Starting fail-safe server...")
    start_cloudflared_tunnel()
    threading.Thread(target=health_monitor_loop, name="HealthMonitor", daemon=True).start()
    threading.Thread(target=worker_loop, name="ScraperWorker", daemon=True).start()
    
    print("\n" + "="*50)
    print("SERVER STARTING LOCALLY AT: http://localhost:5030")
    print("ADMIN PANEL: http://localhost:5030/admin")
    print("="*50 + "\n")
    
    app.run(host='0.0.0.0', port=5030, threaded=True)
