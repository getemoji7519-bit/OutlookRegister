import http.server
import json
import os
import subprocess
import threading
import time
from urllib.parse import urlparse

PORT = int(os.environ.get("PORT", 8080))
os.environ["DISPLAY"] = ":99"

current_process = None
process_lock = threading.Lock()
logs_buffer = []

def append_log(line):
    timestamped = f"[{time.strftime('%H:%M:%S')}] {line}"
    logs_buffer.append(timestamped)
    if len(logs_buffer) > 1500:
        logs_buffer.pop(0)
    print(timestamped, flush=True)

def ensure_xvfb():
    try:
        res = subprocess.run(["pgrep", "-f", "Xvfb :99"], capture_output=True)
        if res.returncode == 0:
            return
    except Exception:
        pass

    for lock in ["/tmp/.X99-lock", "/tmp/.X11-unix/X99"]:
        try:
            if os.path.exists(lock):
                os.remove(lock)
        except Exception:
            pass

    append_log("[*] Starting native Xvfb on display :99...")
    try:
        subprocess.Popen([
            "Xvfb", ":99",
            "-screen", "0", "1920x1080x24",
            "-ac", "+extension", "GLX", "+render", "-noreset"
        ])
        time.sleep(1)
    except Exception as e:
        append_log(f"[!] Warning launching Xvfb: {e}")

def worker_target():
    global current_process
    ensure_xvfb()
    append_log("[*] Starting registration worker...")
    env = os.environ.copy()
    env["DISPLAY"] = ":99"
    env["PYTHONUNBUFFERED"] = "1"

    try:
        current_process = subprocess.Popen(
            ["python3", "-u", "main.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env
        )
    except Exception as e:
        append_log(f"[!] Failed to launch worker process: {e}")
        return

    for line in iter(current_process.stdout.readline, ''):
        append_log(line.rstrip())

    current_process.stdout.close()
    code = current_process.wait()
    append_log(f"[*] Worker exited with code: {code}")

def start_worker():
    global current_process
    with process_lock:
        if current_process is not None and current_process.poll() is None:
            return False, "Worker is already running"
        t = threading.Thread(target=worker_target, daemon=True)
        t.start()
        return True, "Worker started"

def stop_worker():
    global current_process
    with process_lock:
        if current_process is not None and current_process.poll() is None:
            try:
                current_process.terminate()
                append_log("[!] Sent terminate signal to worker process")
                return True, "Worker terminated"
            except Exception as e:
                return False, f"Failed to terminate worker: {e}"
        return False, "Worker is not running"

def get_status():
    global current_process
    is_running = current_process is not None and current_process.poll() is None
    return {
        "running": is_running,
        "pid": current_process.pid if is_running else None
    }

def read_results():
    results_path = os.path.join(os.path.dirname(__file__), "Results", "hotmail_4part_cards.txt")
    if os.path.exists(results_path):
        with open(results_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def load_app_config():
    cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_app_config(new_cfg):
    cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        cur = load_app_config()
        cur.update(new_cfg)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=4, ensure_ascii=False)
        return True, "Saved configuration successfully"
    except Exception as e:
        return False, str(e)

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OutlookRegister Control Panel</title>
  <style>
    :root {
      --bg: #0f172a;
      --card: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --primary: #3b82f6;
      --success: #10b981;
      --danger: #ef4444;
      --warning: #f59e0b;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { background: var(--bg); color: var(--text); padding: 24px; min-height: 100vh; }
    .container { max-width: 1000px; margin: 0 auto; display: flex; flex-direction: column; gap: 20px; }
    .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
    .title { font-size: 24px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 10px; }
    .badge { font-size: 13px; font-weight: 600; padding: 4px 12px; border-radius: 9999px; text-transform: uppercase; }
    .badge.idle { background: #334155; color: #94a3b8; }
    .badge.running { background: #064e3b; color: #34d399; animation: pulse 2s infinite; }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
    .card-title { font-size: 16px; font-weight: 600; margin-bottom: 14px; color: var(--text); display: flex; justify-content: space-between; }
    .actions { display: flex; gap: 12px; }
    button { background: var(--primary); color: #fff; border: none; border-radius: 8px; padding: 10px 18px; font-size: 14px; font-weight: 600; cursor: pointer; transition: opacity 0.2s; }
    button:hover { opacity: 0.9; }
    button.danger { background: var(--danger); }
    button:disabled { opacity: 0.4; cursor: not-allowed; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .form-group { display: flex; flex-direction: column; gap: 6px; }
    .form-group.full { grid-column: span 2; }
    label { font-size: 13px; color: var(--muted); font-weight: 500; }
    input, select { background: #0f172a; border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; color: var(--text); font-size: 14px; outline: none; }
    input:focus, select:focus { border-color: var(--primary); }
    .console { background: #090d16; border: 1px solid #1e293b; border-radius: 8px; padding: 14px; font-family: monospace; font-size: 12px; height: 320px; overflow-y: auto; color: #a5b4fc; line-height: 1.6; white-space: pre-wrap; word-break: break-all; }
    .results-box { background: #090d16; border: 1px solid #1e293b; border-radius: 8px; padding: 14px; font-family: monospace; font-size: 12px; max-height: 200px; overflow-y: auto; color: #34d399; white-space: pre-wrap; word-break: break-all; }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="title">
        <span>⚡ OutlookRegister Control Panel</span>
      </div>
      <div>
        <span id="statusBadge" class="badge idle">IDLE</span>
      </div>
    </div>

    <!-- Actions -->
    <div class="card">
      <div class="card-title">Run Control</div>
      <div class="actions">
        <button id="startBtn" onclick="startWorker()">▶ Start Registration</button>
        <button id="stopBtn" class="danger" onclick="stopWorker()" disabled>⏹ Stop Worker</button>
        <button onclick="fetchLogs()" style="background:#475569;">🔄 Refresh Status</button>
      </div>
    </div>

    <!-- Config -->
    <div class="card">
      <div class="card-title">Configuration (config.json)</div>
      <form id="configForm" onsubmit="saveConfig(event)" class="form-grid">
        <div class="form-group full">
          <label>Residential / SOCKS5 / HTTP Proxy (Required on AWS)</label>
          <input type="text" id="proxyInput" placeholder="http://username:password@proxy-host:port">
        </div>
        <div class="form-group">
          <label>Email Domain</label>
          <select id="suffixInput">
            <option value="@hotmail.com">@hotmail.com</option>
            <option value="@outlook.com">@outlook.com</option>
          </select>
        </div>
        <div class="form-group">
          <label>Max Tasks (Accounts to Create)</label>
          <input type="number" id="maxTasksInput" min="1" max="100" value="5">
        </div>
        <div class="form-group">
          <label>Concurrent Flows (Keep at 1 for 512MB RAM)</label>
          <input type="number" id="flowsInput" min="1" max="3" value="1">
        </div>
        <div class="form-group">
          <label>Captcha Retries</label>
          <input type="number" id="retriesInput" min="1" max="5" value="3">
        </div>
        <div class="form-group full" style="margin-top: 8px;">
          <button type="submit" style="background:#2563eb; width: fit-content;">💾 Save Settings</button>
          <span id="saveNotice" style="margin-left: 12px; font-size: 13px; color: #10b981;"></span>
        </div>
      </form>
    </div>

    <!-- Live Logs -->
    <div class="card">
      <div class="card-title">
        <span>Live Console Log</span>
        <span id="autoScrollTag" style="font-size: 12px; color: var(--muted); cursor: pointer;" onclick="toggleAutoScroll()">Auto-scroll: ON</span>
      </div>
      <div id="consoleLog" class="console">Waiting for logs...</div>
    </div>

    <!-- Results -->
    <div class="card">
      <div class="card-title">
        <span>Generated Accounts (Results/hotmail_4part_cards.txt)</span>
        <button onclick="loadResults()" style="background:none; border:1px solid #475569; padding:4px 10px; font-size:12px;">Refresh</button>
      </div>
      <div id="resultsView" class="results-box">No accounts registered yet.</div>
    </div>
  </div>

  <script>
    let autoScroll = true;

    function toggleAutoScroll() {
      autoScroll = !autoScroll;
      document.getElementById('autoScrollTag').innerText = 'Auto-scroll: ' + (autoScroll ? 'ON' : 'OFF');
    }

    async function loadConfig() {
      try {
        const res = await fetch('/api/config');
        const data = await res.json();
        document.getElementById('proxyInput').value = data.proxy || '';
        document.getElementById('suffixInput').value = data.email_suffix || '@hotmail.com';
        document.getElementById('maxTasksInput').value = data.max_tasks || 5;
        document.getElementById('flowsInput').value = data.concurrent_flows || 1;
        document.getElementById('retriesInput').value = data.max_captcha_retries || 3;
      } catch (e) { console.error(e); }
    }

    async function saveConfig(e) {
      e.preventDefault();
      const payload = {
        proxy: document.getElementById('proxyInput').value.trim(),
        email_suffix: document.getElementById('suffixInput').value,
        max_tasks: parseInt(document.getElementById('maxTasksInput').value, 10),
        concurrent_flows: parseInt(document.getElementById('flowsInput').value, 10),
        max_captcha_retries: parseInt(document.getElementById('retriesInput').value, 10)
      };
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      const notice = document.getElementById('saveNotice');
      notice.innerText = data.message || 'Saved';
      setTimeout(() => { notice.innerText = ''; }, 3000);
    }

    async function startWorker() {
      document.getElementById('startBtn').disabled = true;
      try {
        await fetch('/api/start', { method: 'POST' });
      } catch (e) { alert(e.message); }
      fetchLogs();
    }

    async function stopWorker() {
      try {
        await fetch('/api/stop', { method: 'POST' });
      } catch (e) { alert(e.message); }
      fetchLogs();
    }

    async function fetchLogs() {
      try {
        const res = await fetch('/api/logs');
        const data = await res.json();
        const badge = document.getElementById('statusBadge');
        const startBtn = document.getElementById('startBtn');
        const stopBtn = document.getElementById('stopBtn');

        if (data.running) {
          badge.className = 'badge running';
          badge.innerText = 'RUNNING';
          startBtn.disabled = true;
          stopBtn.disabled = false;
        } else {
          badge.className = 'badge idle';
          badge.innerText = 'IDLE';
          startBtn.disabled = false;
          stopBtn.disabled = true;
        }

        const consoleEl = document.getElementById('consoleLog');
        if (data.logs && data.logs.length > 0) {
          consoleEl.innerText = data.logs.join('\\n');
          if (autoScroll) consoleEl.scrollTop = consoleEl.scrollHeight;
        }
      } catch (e) {}
    }

    async function loadResults() {
      try {
        const res = await fetch('/api/results');
        const text = await res.text();
        document.getElementById('resultsView').innerText = text.trim() || 'No accounts registered yet.';
      } catch (e) {}
    }

    loadConfig();
    fetchLogs();
    loadResults();
    setInterval(fetchLogs, 2500);
    setInterval(loadResults, 10000);
  </script>
</body>
</html>"""

class ControllerServer(http.server.SimpleHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        # Silence routine ALB health checks from polluting logs
        if len(args) > 0 and str(args[0]).startswith("GET /health"):
            return
        super().log_message(format, *args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(HTML_PAGE.encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(json.dumps({"status": "healthy"}).encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/api/logs":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            status = get_status()
            status["logs"] = logs_buffer
            try:
                self.wfile.write(json.dumps(status).encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/api/config":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(json.dumps(load_app_config()).encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/api/results":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(read_results().encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else ""

        if path == "/api/start":
            ok, msg = start_worker()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": ok, "message": msg}).encode("utf-8"))
            return

        if path == "/api/stop":
            ok, msg = stop_worker()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": ok, "message": msg}).encode("utf-8"))
            return

        if path == "/api/config":
            try:
                data = json.loads(body)
                ok, msg = save_app_config(data)
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": ok, "message": msg}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

class SilentHTTPServer(http.server.HTTPServer):
    def handle_error(self, request, client_address):
        # Gracefully suppress BrokenPipeError / ConnectionResetError from ALB health probes
        pass

if __name__ == "__main__":
    ensure_xvfb()
    print(f"[*] Starting OutlookRegister Control Panel on 0.0.0.0:{PORT}...", flush=True)
    server = SilentHTTPServer(("0.0.0.0", PORT), ControllerServer)
    server.serve_forever()
