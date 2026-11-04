"""Run the app:  OMNIPAY_DEMO=1 python -m app.serve   (stdlib server for local/demo; use gunicorn in the container)."""
from __future__ import annotations

import logging
import os
import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path
import time
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from app.config import Settings
from app.groq import GroqClient
from app.platform import DEMO_PRICES, Platform
from app.server import LAN_URL, make_app


class Threaded(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class Quiet(WSGIRequestHandler):
    def log_message(self, *a):
        pass


def build() -> Platform:
    cfg = Settings.from_env()
    pf = Platform(cfg, prices=DEMO_PRICES)                         # prices are SIMULATED until a real feed adapter exists (LAUNCH_READINESS)
    pf.groq = GroqClient(cfg, pf.ledger) if cfg.groq_api_key else None
    if cfg.demo and not pf.ledger.conn().execute("SELECT 1 FROM users LIMIT 1").fetchone():
        from app.seed import seed
        seed(pf)
    return pf


def jobs_loop(pf, every=60):
    while True:
        time.sleep(every)
        try:
            pf.run_jobs()
        except Exception:                                          # noqa: BLE001
            logging.getLogger("omnipay.jobs").exception("job tick failed")


def application():
    """WSGI entrypoint for gunicorn: gunicorn 'app.serve:application()'."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    pf = build()
    if os.environ.get("OMNIPAY_JOBS", "1") == "1":
        threading.Thread(target=jobs_loop, args=(pf,), daemon=True).start()
    return make_app(pf)


def lan_ip() -> str | None:
    """The address other devices on this Wi-Fi can reach (no packet is sent: UDP connect only picks a route)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def self_signed(ip: str | None) -> tuple[str, str]:
    """Phones only allow the camera on HTTPS pages, so local demos need TLS. Makes (once) a self-signed certificate
    for localhost and the LAN address with openssl. Demo use only: browsers show a warning you accept once."""
    d = Path(os.environ.get("OMNIPAY_CERT_DIR", Path.home() / ".omnipay-cert"))
    d.mkdir(parents=True, exist_ok=True)
    cert, key = d / f"cert-{ip or 'local'}.pem", d / f"key-{ip or 'local'}.pem"
    if not cert.exists():
        if not shutil.which("openssl"):
            raise SystemExit("OMNIPAY_HTTPS=1 needs openssl, or set OMNIPAY_TLS_CERT and OMNIPAY_TLS_KEY to your own files.")
        san = "DNS:localhost,IP:127.0.0.1" + (f",IP:{ip}" if ip else "")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825", "-subj", "/CN=OmniPay demo",
                        "-addext", f"subjectAltName={san}", "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    return str(cert), str(key)


def main():
    app = application()
    port = int(os.environ.get("PORT", "8000"))
    cert, key = os.environ.get("OMNIPAY_TLS_CERT"), os.environ.get("OMNIPAY_TLS_KEY")
    ip = lan_ip()
    if os.environ.get("OMNIPAY_HTTPS") == "1" and not cert:
        cert, key = self_signed(ip)
    scheme = "https" if cert else "http"
    LAN_URL["url"] = f"{scheme}://{ip}:{port}" if ip else None
    srv = make_server("0.0.0.0", port, app, server_class=Threaded, handler_class=Quiet)
    if cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print(f"OmniPay on {scheme}://localhost:{port}  (PAYPAL_ENV={os.environ.get('PAYPAL_ENV', 'sandbox')}, demo={os.environ.get('OMNIPAY_DEMO') == '1'})")
    if LAN_URL["url"]:
        print(f"On your phone (same Wi-Fi): {LAN_URL['url']}" + ("" if cert else "   (for the camera scanner on a phone, start with OMNIPAY_HTTPS=1)"))
    srv.serve_forever()


if __name__ == "__main__":
    main()
