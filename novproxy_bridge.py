import asyncio
import json
import os
import signal
import socket
import struct
import subprocess
import threading
import time
from urllib.parse import urlsplit


def cleanup_leftover_browsers():
    """清理本机可能残留的旧 Xvfb / chromium / ssh -D 进程（跳过当前进程自身）。"""
    my_pid = os.getpid()
    for pid_s in os.listdir("/proc"):
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        if pid == my_pid:
            continue
        try:
            cmd = (
                open(f"/proc/{pid}/cmdline", "rb")
                .read()
                .replace(b"\x00", b" ")
                .decode("utf-8", "ignore")
                .strip()
            )
            if any(k in cmd for k in ["ms-playwright/chromium", "ssh -N -o ExitOnForwardFailure"]):
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


class NovProxyBridge:
    """
    通过 oracle-tokyo (已开启 sshd MaxSessions=200) 的本地 HTTP CONNECT -> 二级 SOCKS5h 桥接器。
    1. 绕过本机 OpenClash Fake-IP (198.18.0.0/15) 与透明代理对高端口 SOCKS5 的干扰；
    2. 远端 DNS 解析 (ATYP=0x03)，无需给 Chromium 传递 --host-resolver-rules；
    3. 支持高达 200 并发 HTTPS 通道，按压验证码后的大包上报不丢包。
    """

    def __init__(self, local_port: int = 0, ssh_host: str = "oracle-tokyo"):
        self.local_port = local_port
        self.ssh_host = ssh_host
        self.ssh_socks_port = 0
        self._ssh_proc = None
        self.upstream_host = ""
        self.upstream_port = 0
        self._lock = threading.Lock()
        self._active_writers = set()
        self._loop = None
        self._thread = None
        self._server = None

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def _pick_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _ensure_ssh_tunnel(self):
        with self._lock:
            if self._ssh_proc is not None and self._ssh_proc.poll() is None:
                return
            self.ssh_socks_port = self._pick_free_port()
            self._ssh_proc = subprocess.Popen(
                [
                    "ssh",
                    "-N",
                    "-o", "ExitOnForwardFailure=yes",
                    "-o", "ServerAliveInterval=10",
                    "-o", "ServerAliveCountMax=6",
                    "-D", f"127.0.0.1:{self.ssh_socks_port}",
                    self.ssh_host,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        for _ in range(25):
            time.sleep(0.2)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", self.ssh_socks_port)) == 0:
                    break

    def start(self) -> str:
        self._ensure_ssh_tunnel()

        ready = threading.Event()

        def _run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def _serve():
                self._server = await asyncio.start_server(
                    self._handle_client, "127.0.0.1", self.local_port
                )
                self.local_port = self._server.sockets[0].getsockname()[1]
                ready.set()
                async with self._server:
                    await self._server.serve_forever()

            try:
                self._loop.run_until_complete(_serve())
            except Exception:
                pass

        self._thread = threading.Thread(target=_run_loop, daemon=True)
        self._thread.start()
        ready.wait(timeout=5)
        return self.proxy_url

    def stop(self):
        self._close_all_active()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._ssh_proc:
            try:
                self._ssh_proc.terminate()
            except Exception:
                pass

    def _close_all_active(self):
        with self._lock:
            writers = list(self._active_writers)
            self._active_writers.clear()
        for w in writers:
            try:
                w.close()
            except Exception:
                pass

    def set_novproxy_node(self, node_str: str):
        self._ensure_ssh_tunnel()
        host, port_s = node_str.strip().split(":", 1)
        with self._lock:
            self.upstream_host = host.strip()
            self.upstream_port = int(port_s.strip())
        self._close_all_active()

    async def _socks5_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target_host: str, target_port: int):
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        resp = await asyncio.wait_for(reader.readexactly(2), timeout=15)
        if resp != b"\x05\x00":
            raise RuntimeError(f"SOCKS5 handshake failed: {resp!r}")

        try:
            ip4_packed = socket.inet_aton(target_host)
            req = b"\x05\x01\x00\x01" + ip4_packed + struct.pack("!H", target_port)
        except OSError:
            host_bytes = target_host.encode("idna")
            req = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + struct.pack("!H", target_port)

        writer.write(req)
        await writer.drain()

        hdr = await asyncio.wait_for(reader.readexactly(4), timeout=25)
        if hdr[1] != 0x00:
            raise RuntimeError(f"SOCKS5 connect to {target_host}:{target_port} failed with code {hdr[1]}")
        atyp = hdr[3]
        if atyp == 1:
            await reader.readexactly(4 + 2)
        elif atyp == 3:
            dlen = (await reader.readexactly(1))[0]
            await reader.readexactly(dlen + 2)
        elif atyp == 4:
            await reader.readexactly(16 + 2)

    async def _pipe(self, r: asyncio.StreamReader, w: asyncio.StreamWriter):
        try:
            while True:
                data = await r.read(65536)
                if not data:
                    break
                w.write(data)
                await w.drain()
        except Exception:
            pass
        finally:
            try:
                w.close()
            except Exception:
                pass

    async def _handle_client(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter):
        remote_w = None
        with self._lock:
            self._active_writers.add(client_w)
        try:
            line = await asyncio.wait_for(client_r.readline(), timeout=15)
            if not line:
                client_w.close()
                return
            parts = line.decode("latin1", errors="ignore").strip().split(" ")
            if len(parts) < 2:
                client_w.close()
                return
            method, target = parts[0].upper(), parts[1]

            headers = []
            while True:
                hline = await asyncio.wait_for(client_r.readline(), timeout=15)
                if hline in (b"\r\n", b"\n", b""):
                    break
                headers.append(hline)

            if method == "CONNECT":
                thost, tport_s = target.rsplit(":", 1)
                tport = int(tport_s)
            else:
                sp = urlsplit(target)
                thost = sp.hostname or ""
                tport = sp.port or (443 if sp.scheme == "https" else 80)

            with self._lock:
                up_host, up_port = self.upstream_host, self.upstream_port

            try:
                remote_r, remote_w = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", self.ssh_socks_port), timeout=12
                )
            except Exception:
                await asyncio.to_thread(self._ensure_ssh_tunnel)
                remote_r, remote_w = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", self.ssh_socks_port), timeout=12
                )
            with self._lock:
                self._active_writers.add(remote_w)

            # Hop 1: SSH SOCKS5 -> NovProxy node
            await self._socks5_connect(remote_r, remote_w, up_host, up_port)
            # Hop 2: NovProxy SOCKS5h -> Target
            await self._socks5_connect(remote_r, remote_w, thost, tport)

            if method == "CONNECT":
                client_w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_w.drain()
            else:
                sp = urlsplit(target)
                rel_path = (sp.path or "/") + (("?" + sp.query) if sp.query else "")
                remote_w.write(f"{method} {rel_path} {parts[2]}\r\n".encode("latin1"))
                for h in headers:
                    if not h.lower().startswith(b"proxy-"):
                        remote_w.write(h)
                remote_w.write(b"\r\n")
                await remote_w.drain()

            await asyncio.gather(
                self._pipe(client_r, remote_w),
                self._pipe(remote_r, client_w),
            )
        except Exception:
            try:
                client_w.close()
            except Exception:
                pass
            if remote_w:
                try:
                    remote_w.close()
                except Exception:
                    pass
        finally:
            with self._lock:
                self._active_writers.discard(client_w)
                if remote_w:
                    self._active_writers.discard(remote_w)


USED_IPS_FILE = "/tmp/novproxy_used_ips.txt"


def load_used_ips() -> set[str]:
    if not os.path.exists(USED_IPS_FILE):
        return set()
    try:
        with open(USED_IPS_FILE, "r", encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except Exception:
        return set()


def mark_ip_used(ip: str) -> None:
    if not ip:
        return
    try:
        with open(USED_IPS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{ip.strip()}\n")
    except Exception:
        pass


def fetch_verified_novproxy_nodes(
    regions=("CA", "SE", "CH", "AT", "BE", "NZ", "IE", "FI", "NO", "DK", "DE", "TW", "JP", "KR", "AU", "FR", "NL", "IT", "ES", "PT", "PL", "CZ"),
    per_region=2,
    sticky_time=None,
    ssh_host="oracle-tokyo",
) -> list[dict]:
    """
    通过 oracle-tokyo 提取全球多地区全新的 NovProxy 住宅节点，并验证：
    1. 连续 2 次请求 IP 一致（不跳 IP）；
    2. hosting=False 且 proxy=False（纯净家庭宽带）；
    3. 自动过滤掉本地 /tmp/novproxy_used_ips.txt 中已使用过的出口 IP。
    """
    import random
    if sticky_time is None:
        sticky_time = random.choice([15, 20, 25, 30, 40, 60, 90])
    used_ips = list(load_used_ips())
    py_code = f"""
import subprocess, json, concurrent.futures, random

regions = {list(regions)!r}
per_region = {int(per_region)}
sticky_time = {int(sticky_time)}
used_ips = set({used_ips!r})
raw_nodes = []
for r in regions:
    try:
        t_val = random.choice([15, 20, 25, 30, 45, 60, 90, sticky_time])
        out = subprocess.check_output(
            ["curl", "-s", "-m", "8", f"https://white.novproxy.com/white/api?region={{r}}&num={{per_region}}&time={{t_val}}&format=1&type=txt"],
            text=True
        ).strip().splitlines()
        for l in out:
            l = l.strip()
            if ":" in l:
                raw_nodes.append((r, l))
    except Exception:
        pass

def probe(item):
    expected_region, n = item
    url = "http://ip-api.com/json/?fields=status,countryCode,city,isp,hosting,proxy,mobile,query,timezone"
    ips = []
    info = None
    for _ in range(2):
        p = subprocess.run(["curl", "-s", "-m", "12", "-x", f"socks5h://{{n}}", url], capture_output=True, text=True)
        if '"status":"success"' not in p.stdout:
            return None
        try:
            d = json.loads(p.stdout)
            if d.get("hosting") or d.get("proxy"):
                return None
            if d.get("countryCode") != expected_region:
                return None
            q_ip = d.get("query")
            if not q_ip or q_ip in used_ips:
                return None
            ips.append(q_ip)
            info = d
        except Exception:
            return None
    if len(set(ips)) != 1 or not info:
        return None
    return {{
        "node": n,
        "exit_ip": info.get("query"),
        "country": info.get("countryCode"),
        "city": info.get("city"),
        "timezone": info.get("timezone") or "UTC",
        "isp": info.get("isp"),
    }}

good = []
seen_ips = set(used_ips)
with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
    for res in ex.map(probe, raw_nodes):
        if res and res["exit_ip"] not in seen_ips:
            seen_ips.add(res["exit_ip"])
            good.append(res)
print(json.dumps(good))
"""
    p = subprocess.run(
        ["ssh", ssh_host, "python3", "-"],
        input=py_code,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(p.stdout.strip().splitlines()[-1])
