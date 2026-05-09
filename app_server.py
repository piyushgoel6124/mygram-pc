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
    """Simplified health monitor (Browsers now ping via the Worker Thread)."""
    import requests
    from config import PUBLIC_URL
    log_to_file("[Health Monitor] Started.")
    while True:
        try:
            time.sleep(60)
            status = []
            
            flask_ok = False
            try:
                if requests.get("http://127.0.0.1:5030/ping?src=health_check", timeout=5).status_code == 200:
                    flask_ok = True
            except: pass
            
            tunnel_ok = False
            if PUBLIC_URL:
                try:
                    if requests.get(f"{PUBLIC_URL}/ping?src=health_check", timeout=5).status_code == 200:
                        tunnel_ok = True
                except: pass

            status.append(f"FLASK: {'OK' if flask_ok else 'ERR'}")
            status.append(f"TUNNEL: {'OK' if tunnel_ok else 'ERR'}")
            status.append(f"WORKER: {'OK' if any(t.name == 'ScraperWorker' and t.is_alive() for t in threading.enumerate()) else 'STOPPED'}")
            status.append(f"RAM: {psutil.virtual_memory().percent}%")

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
            # When idle, perform browser heartbeats to keep them warm
            from session_manager import perform_heartbeat
            try: perform_heartbeat()
            except: pass
            time.sleep(10)

def run_background_scrape(session_id):
    with scrape_tasks_lock:
        task = scrape_tasks.get(session_id)
        if not task: return
        reel_url = task["url"]
        task_type = task.get("type", "SINGLE")
        cancel_evt = task["event"]
        task["status"] = "running"

    with scrape_lock:
        try:
            all_results = []
            final_username = None
            
            if task_type == "BULK":
                # Handle Bulk Upload (Match old code lines 1650-1695)
                upload_path = os.path.join(OUTPUTS_DIR, f"upload_{session_id}.csv")
                if not os.path.exists(upload_path):
                    raise Exception("Uploaded file not found")
                
                with open(upload_path, "r", encoding="utf-8") as f:
                    # Support multiple formats (one per line, or CSV with 'url' column)
                    content = f.read().splitlines()
                    links = [line.strip() for line in content if "instagram.com" in line]
                
                task["logs"].append(f"[BULK] Found {len(links)} links. Starting...")
                
                for i, link in enumerate(links):
                    if cancel_evt.is_set(): break
                    task["logs"].append(f"[BULK] Processing {i+1}/{len(links)}: {link}")
                    res, uname = scrape_since_reel(link, logger=lambda m: task["logs"].append(m), cancel_event=cancel_evt)
                    if res:
                        all_results.extend(res)
                        final_username = uname
                    time.sleep(2) # Prevent rate limits between bulk items
                
                results = all_results
                username = final_username or "bulk_user"
            else:
                # Normal Single Scrape
                results, username = scrape_since_reel(reel_url, logger=lambda m: task["logs"].append(m), cancel_event=cancel_evt)
            
            if results and len(results) > 0:
                # SUCCESS
                with scrape_tasks_lock:
                    task["status"] = "completed"
                    task["logs"].append(f"[SYSTEM] Scrape successful! Collected {len(results)} reels.")

                try:
                    # Generate a pretty display name like the old code
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    display_name = f"{username}_{timestamp}.csv"
                    filepath = os.path.join(OUTPUTS_DIR, f"{session_id}.csv")
                    
                    with open(filepath, "w", newline="", encoding="utf-8") as f:
                        keys = results[0].keys()
                        dict_writer = csv.DictWriter(f, fieldnames=keys)
                        dict_writer.writeheader()
                        dict_writer.writerows(results)
                    
                    with scrape_tasks_lock:
                        task["results_path"] = filepath
                        task["display_name"] = display_name
                    
                    # LOG: Match old behavior (Lines 1705-1718)
                    msg = f"[SYSTEM] CSV Saved: {display_name} ({len(results)} reels)"
                    task["logs"].append(msg)
                    log_to_file(msg)

                except Exception as fe:
                    log_to_file(f"File Saving Error: {fe}")
                    task["logs"].append(f"Warning: Data collected but file save failed: {fe}")
                
                # Update Stats & Deduct Credits
                req_user = task.get("username")
                if req_user:
                    from auth_utils import update_user_stats
                    update_user_stats(req_user, links=1, files=1, rows=len(results))
            else:
                # No results found at all
                with scrape_tasks_lock:
                    task["status"] = "error"
                    task["logs"].append("[SYSTEM] No reels were found for this URL.")
                
        except Exception as e:
            log_to_file(f"Scrape Exception: {e}")
            # If the scraper itself crashed but we might have partial data (handled in engine)
            # we check if we still got results. If not, mark error.
            with scrape_tasks_lock:
                if task["status"] != "completed":
                    task["status"] = "error"
                    task["logs"].append(f"ERROR: {e}")

@app.route('/ping')
def ping():
    return "pong", 200

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

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

@app.route('/submit', methods=['GET', 'POST'])
def app_submit():
    # Support both JSON body (POST) and URL parameters (GET)
    if request.method == 'POST':
        data = request.json or {}
    else:
        data = request.args

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

@app.route('/upload', methods=['POST'])
def app_upload():
    username = request.form.get('username')
    device_id = request.form.get('device_id')
    sig = request.form.get('sig')
    
    if not check_auth(username, device_id, sig)[0]:
        return jsonify({"error": "Unauthorized"}), 403
        
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    req_id = str(uuid.uuid4())
    filepath = os.path.join(OUTPUTS_DIR, f"upload_{req_id}.csv")
    file.save(filepath)
    
    # Create task (Matching old logic)
    cancel_event = threading.Event()
    with scrape_tasks_lock:
        scrape_tasks[req_id] = {
            "url": "Bulk Upload",
            "type": "BULK",
            "username": username,
            "status": "queued",
            "event": cancel_event,
            "logs": [f"[SYSTEM] CSV Uploaded: {file.filename}"]
        }
    
    with queue_lock:
        request_queue.append(req_id)
        
    return jsonify({"status": "success", "request_id": req_id})

@app.route('/status', methods=['GET'])
@app.route('/status/<req_id>', methods=['GET'])
def app_status(req_id=None):
    if not req_id: req_id = request.args.get('id')
    
    # Auth Check (Optional but good for security)
    username = request.args.get('username')
    device_id = request.args.get('device_id')
    sig = request.args.get('sig')
    if username and not check_auth(username, device_id, sig)[0]:
        return jsonify({"error": "Unauthorized"}), 403

    with scrape_tasks_lock:
        task = scrape_tasks.get(req_id)
        if not task: return jsonify({"error": "Task not found"}), 404
        
        response = {
            "status": task["status"],
            "logs": task["logs"]
        }
        
        if task["status"] == "completed":
            from config import PUBLIC_URL
            # Use the secure PUBLIC_URL from config (ensures https works for Android)
            base_url = PUBLIC_URL.rstrip('/') if PUBLIC_URL else f"http://{request.host}"
            response["download_url"] = f"{base_url}/download/{req_id}"
            response["filename"] = task.get("display_name", f"{req_id}.csv")
            
        return jsonify(response)

@app.route('/stream', methods=['GET'])
def app_stream():
    req_id = request.args.get('id')
    
    def generate():
        last_idx = 0
        last_heartbeat = time.time()
        
        while True:
            # 1. Heartbeat to keep connection alive
            if time.time() - last_heartbeat > 15:
                yield ": keep-alive\n\n"
                last_heartbeat = time.time()

            with scrape_tasks_lock:
                task = scrape_tasks.get(req_id)
                if not task: 
                    yield "data: [SYSTEM] Task not found.\n\n"
                    break
                
                # 2. Send new logs
                if len(task["logs"]) > last_idx:
                    for i in range(last_idx, len(task["logs"])):
                        yield f"data: {task['logs'][i]}\n\n"
                    last_idx = len(task["logs"])
                
                # Update UI status in app
                yield f"event: status\ndata: {task['status']}\n\n"
                
                # 3. Break if finished (Match app logic line 291)
                if task["status"] in ["completed", "error", "cancelled"]:
                    if task["status"] == "completed":
                        yield f"event: complete\ndata: {req_id}\n\n"
                    break
            
            time.sleep(1)

    response = Response(stream_with_context(generate()), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response

@app.route('/download/<req_id>', methods=['GET'])
def get_results(req_id):
    with scrape_tasks_lock:
        task = scrape_tasks.get(req_id)
        if not task or not task.get("results_path"):
            return jsonify({"error": "Results not ready"}), 404
        
        # Get the pretty name we generated earlier
        display_name = task.get("display_name", f"{req_id}.csv")
        
        return send_from_directory(
            OUTPUTS_DIR, 
            f"{req_id}.csv", 
            as_attachment=True, 
            download_name=display_name
        )

@app.route('/cancel/<req_id>', methods=['GET', 'POST'])
def app_cancel(req_id):
    with scrape_tasks_lock:
        task = scrape_tasks.get(req_id)
        if task:
            task["event"].set()
            task["status"] = "cancelled"
            return jsonify({"status": "success", "message": "Task cancelled"})
    return jsonify({"error": "Task not found"}), 404

def start_cloudflared_tunnel():
    """Starts the Cloudflare tunnel in a background thread."""
    def run_tunnel():
        import subprocess
        import shutil
        
        # Check if cloudflared exists
        if not shutil.which("cloudflared"):
            log_to_file("[Cloudflare Error] 'cloudflared' command not found. Please install it or add it to PATH.")
            return

        log_to_file("[Cloudflare] Starting tunnel in quiet mode...")
        try:
            # Command for permanent named tunnel with HTTP2 protocol
            cmd = [
                "cloudflared", "tunnel", "--protocol", "http2", "--ha-connections", "1",
                "--loglevel", "warn", "--url", "http://localhost:5030",
                "run", "f637317c-9221-477c-ab6b-efadd6e8bf0a"
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
    """Entry point that coordinates the entire system startup sequence."""
    
    def coordinated_startup():
        log_to_file("[System] --- Phase 1: Starting Local Flask Server ---")
        # Flask will be started at the end of start_server() because it is blocking.
        # We wait here for it to be alive.
        import requests
        for _ in range(10):
            try:
                if requests.get("http://localhost:5030/ping", timeout=2).status_code == 200:
                    break
            except: pass
            time.sleep(1)
        
        log_to_file("[System] --- Phase 2: Launching Cloudflare Tunnel ---")
        start_cloudflared_tunnel()

        log_to_file("[System] --- Phase 3: Waiting for Public URL to go Online ---")
        from config import PUBLIC_URL
        online = False
        for i in range(30):
            try:
                print(f"\r[Tunnel] Checking public link... ({i+1}/30)", end="")
                if requests.get(f"{PUBLIC_URL}/ping", timeout=3).status_code == 200:
                    online = True
                    break
            except: pass
            time.sleep(1)
        
        print("") # New line
        if online:
            log_to_file(f"[System] SUCCESS: Tunnel is ONLINE at {PUBLIC_URL}")
        else:
            log_to_file("[System] WARNING: Tunnel is taking too long. Starting browsers anyway...")

        log_to_file("[System] --- Phase 4: Initializing Browser Pool & Worker ---")
        threading.Thread(target=worker_loop, name="ScraperWorker", daemon=True).start()

    # 1. Start Health Monitor
    threading.Thread(target=health_monitor_loop, name="HealthMonitor", daemon=True).start()

    # 2. Start the Coordination Thread
    threading.Thread(target=coordinated_startup, daemon=True).start()

    # 3. Start Flask (Blocking)
    print("\n" + "="*50)
    print("SERVER STARTING LOCALLY AT: http://localhost:5030")
    print("ADMIN PANEL: http://localhost:5030/admin")
    print("="*50 + "\n")
    
    app.run(host='0.0.0.0', port=5030, threaded=True)
