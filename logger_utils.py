import threading
import queue
import time
import sys
from datetime import datetime
from config import LOG_FILE

_log_queue = queue.Queue()
_log_lock = threading.Lock()

import builtins

# Keep original streams to avoid infinite loops in the logger
_original_stdout = sys.stdout
_original_stderr = sys.stderr

class StreamInterceptor:
    def __init__(self, original_stream, to_console=True):
        self.original_stream = original_stream
        self.to_console = to_console

    def write(self, data):
        if data.strip():
            # Send to queue for background logging
            log_to_file(data.strip(), to_console=False)
        self.original_stream.write(data)

    def flush(self):
        self.original_stream.flush()

def _logging_worker():
    """Background thread to handle file I/O for logging."""
    while True:
        try:
            batch = [_log_queue.get()]
            while not _log_queue.empty():
                try:
                    batch.append(_log_queue.get_nowait())
                except queue.Empty:
                    break
            
            new_lines = []
            for message, to_console in batch:
                timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
                full_msg = f"{timestamp} {message}"
                
                # IMPORTANT: Use original stdout here to prevent recursion
                if to_console:
                    _original_stdout.write(full_msg + "\n")
                new_lines.append(full_msg)
            
            if new_lines:
                with _log_lock:
                    with open(LOG_FILE, "a", encoding="utf-8") as f:
                        f.write("\n".join(new_lines) + "\n")
            
            for _ in range(len(batch)):
                _log_queue.task_done()
                
        except Exception as e:
            _original_stderr.write(f"Logging Worker Error: {e}\n")
            time.sleep(2)

# Intercept Print and Errors
sys.stdout = StreamInterceptor(sys.stdout)
sys.stderr = StreamInterceptor(sys.stderr)

# Intercept Input
_original_input = builtins.input
def logged_input(prompt=""):
    response = _original_input(prompt)
    log_to_file(f"[Input Prompt] {prompt}", to_console=False)
    log_to_file(f"[User Input] {response}", to_console=False)
    return response
builtins.input = logged_input

# Start background logger
threading.Thread(target=_logging_worker, daemon=True).start()

def log_to_file(message, to_console=True):
    """Adds a message to the logging queue."""
    _log_queue.put((message, to_console))
