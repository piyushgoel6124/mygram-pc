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

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    if data.get('username') == ADMIN_USER and data.get('password') == ADMIN_PASS:
        return jsonify({"token": "mock-session-token-2026"})
    return jsonify({"error": "Invalid Credentials"}), 401

@app.route('/api/admin/users', methods=['GET', 'POST', 'DELETE'])
def admin_users():
    # Simple token check
    if request.headers.get('Authorization') != "mock-session-token-2026":
        return jsonify({"error": "Unauthorized"}), 403

    if request.method == 'GET':
        return jsonify(load_users())
    
    elif request.method == 'POST':
        new_user = request.json
        users = load_users()
        # Update if exists, else append
        found = False
        for i, u in enumerate(users):
            if u["username"] == new_user["username"]:
                users[i] = new_user
                found = True; break
        if not found: users.append(new_user)
        save_users(users)
        return jsonify({"status": "success"})
    
    elif request.method == 'DELETE':
        username = request.args.get('username')
        users = [u for u in load_users() if u["username"] != username]
        save_users(users)
        return jsonify({"status": "success"})

@app.route('/login', methods=['POST'])
def app_login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    device_id = data.get('device_id')
    sig = data.get('sig')

    if not username or not password or not device_id:
        return jsonify({"error": "Missing credentials"}), 400
    
    # Check Auth Logic
    success, msg = check_auth(username, device_id, sig)
    if not success:
        return jsonify({"error": msg}), 403

    # (Simplified login check)
    users = load_users()
    for u in users:
        if u["username"] == username and u["password"] == password:
            return jsonify({"status": "success", "message": "Logged in"})
    
    return jsonify({"error": "Invalid username or password"}), 401

@app.route('/submit', methods=['POST'])
def app_submit():
    data = request.json
    username = data.get('username')
    device_id = data.get('device_id')
    sig = data.get('sig')
    reel_url = data.get('url')

    if not reel_url:
        return jsonify({"error": "Missing Reel URL"}), 400

    # Auth Check
    success, msg = check_auth(username, device_id, sig)
    if not success:
        return jsonify({"error": msg}), 403

    # Queue the task
    req_id = str(uuid.uuid4())
    cancel_evt = threading.Event()
    
    with scrape_tasks_lock:
        scrape_tasks[req_id] = {
            "id": req_id,
            "username": username,
            "url": reel_url,
            "status": "queued",
            "timestamp": time.time(),
            "logs": [],
            "event": cancel_evt
        }
    
    with queue_lock:
        request_queue.append(req_id)
    
    return jsonify({"status": "success", "request_id": req_id})

@app.route('/status', methods=['GET'])
def app_status():
    req_id = request.args.get('id')
    with scrape_tasks_lock:
        task = scrape_tasks.get(req_id)
        if not task: return jsonify({"error": "Task not found"}), 404
        return jsonify({
            "status": task["status"],
            "logs": task["logs"][-5:] # Send last 5 log lines
        })

@app.route('/results/<req_id>', methods=['GET'])
def get_results(req_id):
    with scrape_tasks_lock:
        task = scrape_tasks.get(req_id)
        if not task or not task.get("results_path"):
            return jsonify({"error": "Results not ready"}), 404
        return send_from_directory(OUTPUTS_DIR, f"{req_id}.csv", as_attachment=True)

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
    
    # --- Route Debugger ---
    print("\n--- Registered Routes ---")
    for rule in app.url_map.iter_rules():
        print(f"Route: {rule.rule} | Methods: {rule.methods}")
    print("------------------------\n")

    start_cloudflared_tunnel()
    threading.Thread(target=health_monitor_loop, name="HealthMonitor", daemon=True).start()
    threading.Thread(target=worker_loop, name="ScraperWorker", daemon=True).start()
    
    print("\n" + "="*50)
    print("SERVER STARTING LOCALLY AT: http://localhost:5030")
    print("ADMIN PANEL: http://localhost:5030/admin")
    print("="*50 + "\n")
    
    app.run(host='0.0.0.0', port=5030, threaded=True)
