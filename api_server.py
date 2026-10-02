#!/usr/bin/env python3
"""
WiFi Network Scanner - REST API Server (React frontend ke liye)
----------------------------------------------------------------
network_scanner.py ki scanning/blocking/WoL capabilities ko ek HTTP
JSON API ke through expose karta hai. Koi extra dependency nahi -
sirf Python standard library (http.server).

Endpoints:
  GET  /api/devices                -> pichhla scan result (devices.json)
  POST /api/scan                   -> fresh scan chalao, updated list wapas
  GET  /api/device/<ip>            -> ek device ki detail
  POST /api/block                  -> {"target": "IP ya MAC"} device block
  POST /api/unblock                -> {"target": "IP ya MAC"} device unblock
  POST /api/wol                    -> {"mac": "...", "ip": "(optional)"}
  GET  /api/stats                  -> total devices / blocked / vendor summary

Run:
  python3 api_server.py            # port 5000 default
  python3 api_server.py --port 8080

Frontend (frontend/) isi server se /api/* calls karega (CORS enabled).
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# network_scanner.py ke functions reuse karo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import network_scanner as ns


def json_response(handler, payload, status=200):
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler):
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if length == 0:
        return {}
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[API] {self.address_string()} - {fmt % args}")

    # CORS preflight
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/devices":
            devices = ns.load_devices()
            json_response(self, {"devices": devices, "count": len(devices)})

        elif path.startswith("/api/device/"):
            ip = path.split("/api/device/", 1)[1].strip("/")
            devices = ns.load_devices()
            match = next((d for d in devices if d.get("ip") == ip), None)
            if match:
                json_response(self, {"device": match})
            else:
                json_response(self, {"error": f"Device {ip} not found"}, 404)

        elif path == "/api/stats":
            devices = ns.load_devices()
            vendors = {}
            blocked = 0
            for d in devices:
                v = d.get("vendor", "Unknown")
                vendors[v] = vendors.get(v, 0) + 1
                if d.get("blocked"):
                    blocked += 1
            json_response(self, {
                "total": len(devices),
                "blocked": blocked,
                "online": len(devices) - blocked,
                "vendors": vendors,
            })

        elif path == "/api/health":
            json_response(self, {"status": "ok"})

        else:
            json_response(self, {"error": "Unknown endpoint"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/scan":
            try:
                devices = ns.full_scan(verbose=False)
                ns.save_devices(devices)
                json_response(self, {"devices": devices, "count": len(devices)})
            except Exception as e:
                json_response(self, {"error": str(e)}, 500)

        elif path in ("/api/block", "/api/unblock"):
            data = read_json_body(self)
            if data is None:
                json_response(self, {"error": "Invalid JSON body"}, 400)
                return
            target = (data or {}).get("target", "").strip()
            if not target:
                json_response(self, {"error": "'target' (IP or MAC) required"}, 400)
                return
            try:
                ok = ns.block_device(target, unblock=(path == "/api/unblock"))
                # blocked flag devices.json mein update karo
                devices = ns.load_devices()
                changed = False
                for d in devices:
                    if d.get("ip") == target or d.get("mac") == target:
                        d["blocked"] = (path == "/api/block")
                        changed = True
                if changed:
                    with open(ns.DEVICES_FILE, "w") as f:
                        json.dump(devices, f, indent=2, default=str)
                if ok:
                    msg = "blocked" if path == "/api/block" else "unblocked"
                    json_response(self, {"success": True, "message": f"{target} {msg}"})
                else:
                    json_response(self, {"success": False,
                                         "message": "Command failed (sudo/root zaroori hai)"},
                                 500)
            except Exception as e:
                json_response(self, {"error": str(e)}, 500)

        elif path == "/api/wol":
            data = read_json_body(self)
            if data is None:
                json_response(self, {"error": "Invalid JSON body"}, 400)
                return
            mac = (data or {}).get("mac", "").strip()
            ip = (data or {}).get("ip")
            if not mac:
                json_response(self, {"error": "'mac' required"}, 400)
                return
            try:
                ok = ns.wake_on_lan(mac, ip)
                json_response(self, {"success": bool(ok),
                                     "message": "Magic packet bhej diya" if ok
                                     else "WoL fail hua"})
            except Exception as e:
                json_response(self, {"error": str(e)}, 500)

        else:
            json_response(self, {"error": "Unknown endpoint"}, 404)


def main():
    parser = argparse.ArgumentParser(description="Network Scanner API Server")
    parser.add_argument("--port", type=int, default=5000, help="Port (default 5000)")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"[*] Network Scanner API chal raha hai: http://localhost:{args.port}")
    print("[*] Endpoints: /api/devices /api/scan /api/device/<ip> "
          "/api/block /api/unblock /api/wol /api/stats")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Server band.")
        server.shutdown()


if __name__ == "__main__":
    main()
