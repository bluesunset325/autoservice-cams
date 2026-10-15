"""Тестовая страница АП: проверка удалённого доступа (Tailscale) без камер.

Нужен только Python 3 — никаких библиотек. Запуск:
    python ap_test.py            (порт 8000, как у настоящей АП)
    python ap_test.py 8001       (другой порт, если 8000 занят)
Остановить: Ctrl+C.
"""
from __future__ import annotations

import html
import ipaddress
import json
import socket
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_NET6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def is_tailscale(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return addr in TAILSCALE_NET or addr in TAILSCALE_NET6


def tailscale_ip() -> str | None:
    """Адрес этого компьютера в сети Tailscale (если Tailscale установлен и включён)."""
    candidates = ["tailscale",
                  r"C:\Program Files\Tailscale\tailscale.exe",
                  "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]
    for exe in candidates:
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        ip = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
        if is_tailscale(ip):
            return ip
    return next((ip for ip in own_ips() if is_tailscale(ip)), None)


def own_ips() -> list[str]:
    """IPv4-адреса этого компьютера (без отправки данных: UDP-сокет только выбирает маршрут)."""
    ips = []
    try:
        ips += [ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    if sys.platform == "darwin":
        for iface in ("en0", "en1"):
            try:
                out = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True, timeout=3)
                ips.append(out.stdout.strip())
            except (OSError, subprocess.SubprocessError):
                pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    return list(dict.fromkeys(ip for ip in ips if ip))


LAN_NETS = [ipaddress.ip_network(n) for n in ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12")]


def local_ip() -> str | None:
    """Адрес в локальной сети (192.168.x.x и т.п.); адреса VPN и Tailscale не берём."""
    for ip in own_ips():
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if any(addr in n for n in LAN_NETS):
            return ip
    return None


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>АП — проверка связи</title>
<style>
  :root {{ color-scheme: light dark; --ok: #1a7f37; --warn: #9a6700; --bg: #f6f7f9; --card: #fff; --text: #1f2328; --muted: #656d76; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --ok: #3fb950; --warn: #d29922; --bg: #0d1117; --card: #161b22; --text: #e6edf3; --muted: #8d96a0; }} }}
  body {{ margin: 0; font: 16px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); }}
  main {{ max-width: 520px; margin: 0 auto; padding: 24px 16px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); margin: 0 0 20px; }}
  .card {{ background: var(--card); border-radius: 12px; padding: 18px; margin-bottom: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .big {{ font-size: 20px; font-weight: 600; color: {color}; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 6px 0; vertical-align: top; }}
  td:first-child {{ color: var(--muted); width: 45%; }}
  code {{ font-size: 15px; word-break: break-all; }}
</style></head>
<body><main>
  <h1>АП — проверка связи</h1>
  <p class="sub">Тестовая страница, без камер и входа</p>
  <div class="card"><div class="big">{verdict}</div><p>{hint}</p></div>
  <div class="card"><table>
    <tr><td>Ваш адрес</td><td><code>{client}</code></td></tr>
    <tr><td>Компьютер АП</td><td><code>{host}</code></td></tr>
    <tr><td>Адрес в Tailscale</td><td><code>{ts_ip}</code></td></tr>
    <tr><td>Адрес в локальной сети</td><td><code>{lan_ip}</code></td></tr>
    <tr><td>Время на компьютере АП</td><td><code>{now}</code></td></tr>
  </table></div>
</main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "AP-test"

    def do_GET(self):
        client = self.client_address[0]
        via_ts = is_tailscale(client)
        info = {
            "client": client,
            "via_tailscale": via_ts,
            "host": socket.gethostname(),
            "tailscale_ip": self.server.ts_ip,
            "lan_ip": self.server.lan_ip,
            "time": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        }
        if self.path.startswith("/ping"):
            body, ctype = json.dumps(info, ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path in ("/", "/index.html"):
            if via_ts:
                verdict, color = "✅ Работает через Tailscale", "var(--ok)"
                hint = "Удалённый доступ настроен. Настоящая АП откроется по этому же адресу."
            else:
                verdict, color = "✅ Страница открылась (не через Tailscale)", "var(--warn)"
                hint = ("Вы зашли из локальной сети или с этого же компьютера. Для проверки удалённого доступа "
                        "выключите Wi-Fi на телефоне, включите Tailscale и откройте адрес вида http://100.x.y.z:8000")
            esc = {k: html.escape(str(v if v is not None else "—")) for k, v in info.items()}
            body = PAGE.format(verdict=verdict, color=color, hint=html.escape(hint), client=esc["client"],
                               host=esc["host"], ts_ip=esc["tailscale_ip"], lan_ip=esc["lan_ip"],
                               now=esc["time"]).encode()
            ctype = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_error(self, fmt, *args):
        pass  # ошибку покажет log_request той же строкой, без дубля

    def log_message(self, fmt, *args):
        if self.path.startswith(("/favicon", "/apple-touch")):
            return
        client = self.client_address[0]
        mark = "через Tailscale" if is_tailscale(client) else "локально"
        print(f"{datetime.now():%H:%M:%S}  зашли с {client} ({mark})  {self.requestline}", flush=True)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        print(f"Не удалось занять порт {port}: {e}")
        print("Скорее всего, уже запущена настоящая АП (или этот тест). Остановите её или запустите: "
              f"python ap_test.py {port + 1}")
        sys.exit(1)
    server.ts_ip, server.lan_ip = tailscale_ip(), local_ip()
    print("Тестовая страница АП запущена. Остановить: Ctrl+C\n")
    print(f"  На этом компьютере:   http://localhost:{port}")
    if server.lan_ip:
        print(f"  В локальной сети:     http://{server.lan_ip}:{port}")
    if server.ts_ip:
        print(f"  Через Tailscale:      http://{server.ts_ip}:{port}   <- открыть с телефона, Wi-Fi выключен")
    else:
        print("  Tailscale: адрес не найден (не установлен или не включён). Адрес 100.x.y.z смотрите в приложении Tailscale.")
    print("\nКто заходит на страницу — будет видно ниже:\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
