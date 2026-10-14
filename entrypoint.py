import http.server
import os
import subprocess
import threading

port = int(os.environ.get("PORT", 8080))

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OutlookRegister is running on SnapDeploy\n")

def run_worker():
    print("[*] Starting OutlookRegister worker under Xvfb...")
    subprocess.run([
        "xvfb-run", "-a",
        "--server-args=-screen 0 1920x1080x24 -ac +extension GLX +render -noreset",
        "python3", "main.py"
    ])

# Start worker in background
worker_thread = threading.Thread(target=run_worker, daemon=True)
worker_thread.start()

# Keep HTTP health check open for SnapDeploy
print(f"[*] Health check server listening on 0.0.0.0:{port}")
server = http.server.HTTPServer(("0.0.0.0", port), Handler)
server.serve_forever()
