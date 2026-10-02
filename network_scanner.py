#!/usr/bin/env python3
"""
WiFi Network Scanner & Device Manager
-------------------------------------
Apne connected WiFi network par maujood saare devices scan karo,
unki details (IP, MAC, Hostname, Manufacturer) dekho aur manage karo.

Features:
  1. LAN Scan      - ARP ping sweep se connected devices dhoondo (scapy + fallback socket ping)
  2. Live Table    - Scan results khubsurat table mein
  3. Device Info   - Kisi ek device ki poori detail
  4. Block/Allow   - Firewall (iptables) se device block/unblock karo (root chahiye)
  5. Wake-on-LAN   - Band device ko magic packet se wake karo
  6. History       - Har scan ka record JSON file mein save

Usage:
  python3 network_scanner.py scan                 # network scan karo
  python3 network_scanner.py list                 # pichhla scan result dikhao
  python3 network_scanner.py info <IP>            # device ki detail
  python3 network_scanner.py block <IP_OR_MAC>    # device block karo (sudo)
  python3 network_scanner.py unblock <IP_OR_MAC>  # device unblock karo (sudo)
  python3 network_scanner.py wol <MAC> [IP]       # Wake-on-LAN signal bhejo
  python3 network_scanner.py gui                  # interactive menu
"""

import argparse
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_data")
DEVICES_FILE = os.path.join(DATA_DIR, "devices.json")

# ----------------------------- helpers -----------------------------------

def run(cmd, capture=True):
    """Shell command chalao, output return karo (errors ignore)."""
    try:
        res = subprocess.run(
            cmd, shell=isinstance(cmd, str),
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            text=True, timeout=30,
        )
        return (res.stdout or "").strip() if capture else ""
    except Exception as e:
        return f"__ERROR__ {e}"


def get_default_gateway_and_ip():
    """Default gateway + local IP detect karo (Linux/macOS/Windows)."""
    gw, local_ip = None, None
    # Linux: `ip route` se
    out = run("ip route show default 2>/dev/null")
    m = re.search(r"default via (\d+\.\d+\.\d+\.\d+) dev (\S+)", out)
    if m:
        gw = m.group(1)
        iface = m.group(2)
        out2 = run(f"ip -4 addr show {iface} 2>/dev/null")
        m2 = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", out2)
        if m2:
            local_ip, prefix = m2.group(1), int(m2.group(2))
            return gw, local_ip, prefix
    # Fallback: UDP trick se local IP nikalo
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except OSError:
        local_ip = "127.0.0.1"
    # gateway guess: .1 of our /24
    if local_ip and local_ip != "127.0.0.1":
        gw = ".".join(local_ip.split(".")[:3]) + ".1"
    return gw, local_ip, 24


def ip_in_subnet(ip, base, prefix):
    try:
        mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
        a = struct.unpack("!I", socket.inet_aton(ip))[0]
        b = struct.unpack("!I", socket.inet_aton(base))[0]
        return (a & mask) == (b & mask)
    except OSError:
        return False


def mac_lookup_registry(mac):
    """MAC ke pehle 6 hex digits se vendor naam dhoondo (offline heuristics
    + online API attempt). Naam na mile to 'Unknown'."""
    known_vendors = {  # common OUI prefixes (best-effort sample)
        "F0:9F": "Apple", "A4:83": "Apple", "B8:C7": "Apple", "DC:A9": "Apple",
        "3C:22": "Apple", "14:99": "Apple",
        "AC:CF": "Cisco", "00:1B": "Cisco", "64:F9": "Cisco",
        "B0:BE": "Samsung", "5C:2E": "Samsung", "00:16:32": "Samsung",
        "E0:AC": "Samsung", "38:AA": "Samsung",
        "FC:FB": "Xiaomi", "78:11": "Xiaomi", "28:E3": "Xiaomi",
        "94:87": "Xiaomi", "64:09": "Xiaomi",
        "A0:3B": "Huawei", "C8:B5": "Huawei", "48:EF:FA": "Huawei",
        "1C:39:0E": "TP-Link", "44:CE:7E": "TP-Link", "50:C7:BF": "TP-Link",
        "60:A4:23": "TP-Link",
        "EC:08:6D": "Realtek", "00:E0:4C": "Realtek",
        "08:00:27": "VirtualBox", "52:54:00": "QEMU/KVM", "00:15:5D": "Hyper-V",
    }
    mmac = mac.upper()
    for key, vendor in known_vendors.items():
        if mmac.startswith(key):
            return vendor
    # online lookup (xapic free API) - optional, silently skip if offline
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://api.xapic.com/mac.php?mac={mmac.replace(':', '')}",
            headers={"User-Agent": "network-scanner/1.0"})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
            v = data.get("Vendor") or data.get("vendor")
            if v:
                return v
    except Exception:
        pass
    return "Unknown"


def reverse_dns(ip):
    try:
        host = socket.gethostbyaddr(ip)[0]
        return host
    except (socket.herror, socket.gaierror, OSError):
        return None


def arping_scan_subnet(subnet_prefix, count=254, timeout=2):
    """Scapy ARP broadcast se live hosts dhoondo. Scapy na ho to [] wapas."""
    try:
        from scapy.all import ARP, Ether, srp
    except ImportError:
        return None
    target = f"{subnet_prefix}.1/{count}" if count < 254 else f"{subnet_prefix}.0/24"
    pkt = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=target)
    try:
        ans, _ = srp(pkt, timeout=timeout, verbose=False)
    except PermissionError:
        print("[!] Scapy ke liye root privileges chahiye. Fallback ping-sweep use kar rahe hain.")
        return None
    found = []
    for snd, rcv in ans:
        found.append({"ip": rcv.psrc, "mac": rcv.hwsrc})
    return found


def ping_sweep(subnet_prefix, threads=60):
    """Fallback: TCP connect ping sweep (koi root nahi chahiye)."""
    alive = []
    lock = threading.Lock()

    def probe(i):
        ip = f"{subnet_prefix}.{i}"
        for port in (80, 443, 22, 53, 445, 8080):
            try:
                s = socket.create_connection((ip, port), timeout=0.6)
                s.close()
                with lock:
                    alive.append(ip)
                return
            except OSError:
                continue
        # ICMP-ish fallback via system ping (linux)
        out = run(f"ping -c 1 -W 1 {ip}")
        if "bytes from" in out:
            with lock:
                alive.append(ip)

    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(probe, range(1, 255)))
    return alive


def read_arp_table():
    """System ARP table parse karo (ip neigh / arp -a)."""
    entries = {}
    out = run("ip -4 neigh show 2>/dev/null") or run("arp -a 2>/dev/null")
    for line in out.splitlines():
        m = re.search(r"(\d+\.\d+\.\d+\.\d+).*?((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", line)
        if m:
            entries[m.group(1)] = m.group(2).upper()
    return entries


# --------------------------- core scan ------------------------------------

def full_scan(verbose=True):
    gw, my_ip, prefix = get_default_gateway_and_ip()
    if not my_ip:
        print("[!] Local IP detect nahi hui. Kya aap kisi network se connected hain?")
        return []
    subnet_prefix = ".".join(my_ip.split(".")[:3])
    if verbose:
        print(f"[*] Local IP     : {my_ip}")
        print(f"[*] Gateway      : {gw}")
        print(f"[*] Subnet       : {subnet_prefix}.0/24")
        print("[*] Scan shuru... (thora wait karein)")

    # sources of discovery
    arp_tab = read_arp_table()
    discovered = {}

    hosts = arping_scan_subnet(subnet_prefix)
    if hosts is None:  # scapy fail -> ping sweep
        if verbose:
            print("[*] Ping-sweep se dhoond rahe hain...")
        for ip in ping_sweep(subnet_prefix):
            discovered.setdefault(ip, {"ip": ip, "mac": arp_tab.get(ip, "?")})
    else:
        for h in hosts:
            discovered[h["ip"]] = {"ip": h["ip"], "mac": h["mac"]}

    # merge ARP table entries (gateway etc.)
    for ip, mac in arp_tab.items():
        if ip_in_subnet(ip, my_ip, prefix):
            discovered.setdefault(ip, {"ip": ip, "mac": mac})
    if gw:
        discovered.setdefault(gw, {"ip": gw, "mac": arp_tab.get(gw, "?")})

    # enrich: hostname + vendor
    devices = []
    for ip in sorted(discovered, key=lambda x: tuple(map(int, x.split(".")))):
        d = discovered[ip]
        mac = d.get("mac") or "?"
        host = reverse_dns(ip)
        vendor = mac_lookup_registry(mac) if mac != "?" else "Unknown"
        devices.append({
            "ip": ip,
            "mac": mac,
            "hostname": host or "-",
            "vendor": vendor,
            "mine": ip == my_ip,
            "last_seen": datetime.now().isoformat(timespec="seconds"),
        })
    if verbose:
        print(f"[+] {len(devices)} devices mile.")
    save_devices(devices)
    return devices


# --------------------------- persistence -----------------------------------

def save_devices(devices):
    os.makedirs(DATA_DIR, exist_ok=True)
    old = load_devices()
    merged = {d["ip"]: d for d in old}
    for d in devices:
        merged[d["ip"]] = d
    with open(DEVICES_FILE, "w") as f:
        json.dump(list(merged.values()), f, indent=2)
    # history log
    hist_file = os.path.join(DATA_DIR, "history.log")
    with open(hist_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | scanned {len(devices)} devices | "
                + ",".join(d["ip"] for d in devices) + "\n")


def load_devices():
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE) as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []


# --------------------------- management ------------------------------------

def is_mac(s):
    return bool(re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", s))


def block_device(target, unblock=False):
    """iptables se device block/unblock. Root required."""
    if os.geteuid() != 0:
        print("[!] Block/unblock ke liye sudo/root zaroori hai. Try: sudo python3 network_scanner.py block ...")
        return False
    if is_mac(target):
        args = f"-m mac --mac-source {target.upper()}"
    else:
        args = f"-d {target}"
    action = "D" if not unblock else "R"
    chain = "INPUT" if not unblock else "INPUT"
    out = run(f"iptables -{action} {chain} {args} -j DROP")
    if "__ERROR__" in out or "not found" in out.lower():
        print(f"[!] iptables available nahi ya error: {out}")
        return False
    verb = "UNBLOCK" if unblock else "BLOCK"
    print(f"[+] {verb} rule lag gaya: {target}")
    # tag in saved data
    devs = load_devices()
    for d in devs:
        if d["ip"] == target or d["mac"] == target.upper():
            d["blocked"] = not unblock
    with open(DEVICES_FILE, "w") as f:
        json.dump(devs, f, indent=2)
    return True


def wake_on_lan(mac, broadcast_ip=None):
    if not is_mac(mac):
        print("[!] Invalid MAC format. Example: AA:BB:CC:DD:EE:FF")
        return False
    bytes_ = bytes.fromhex(mac.replace(":", ""))
    packet = b"\xff\xff\xff\xff\xff\xff" + bytes_ * 16
    dest = broadcast_ip or "<broadcast>"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for port in (9, 7):
        try:
            sock.sendto(packet, (dest, port))
            print(f"[+] Magic packet bheja: {mac} -> {dest}:{port}")
        except OSError:
            continue
    sock.close()
    return True


# --------------------------- display ---------------------------------------

BAR = "-" * 78


def print_table(devices):
    if not devices:
        print("(koi device nahi mila — pehle 'scan' chalayein)")
        return
    print(BAR)
    print(f"{'IP':<16} {'MAC':<20} {'HOSTNAME':<20} {'VENDOR':<14}")
    print(BAR)
    for d in devices:
        me = "  <- YOU" if d.get("mine") else ("  [BLOCKED]" if d.get("blocked") else "")
        print(f"{d['ip']:<16} {d['mac']:<20} {d['hostname'][:19]:<20} {d['vendor'][:13]:<14}{me}")
    print(BAR)
    print(f"Total: {len(devices)} devices")


# --------------------------- GUI (interactive) ------------------------------

def gui():
    while True:
        print("\n===== WiFi Device Manager =====")
        print("1) Network scan karo")
        print("2) Devices list dekho")
        print("3) Device ki detail")
        print("4) Device block karo")
        print("5) Device unblock karo")
        print("6) Wake-on-LAN bhejo")
        print("7) Exit")
        c = input("Choice: ").strip()
        devs = load_devices()
        if c == "1":
            print_table(full_scan())
        elif c == "2":
            print_table(devs)
        elif c == "3":
            ip = input("IP: ").strip()
            for d in devs:
                if d["ip"] == ip:
                    for k, v in d.items():
                        print(f"  {k:>10}: {v}")
                    break
            else:
                print("Device nahi mila list mein.")
        elif c == "4":
            t = input("IP ya MAC: ").strip()
            block_device(t)
        elif c == "5":
            t = input("IP ya MAC: ").strip()
            block_device(t, unblock=True)
        elif c == "6":
            mac = input("Target MAC: ").strip()
            b = input("Broadcast IP (optional): ").strip() or None
            wake_on_lan(mac, b)
        elif c == "7":
            print("Bye!")
            break


# --------------------------- CLI --------------------------------------------

def main():
    p = argparse.ArgumentParser(description="WiFi Network Scanner & Device Manager")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("scan", help="connected devices scan karo")
    sub.add_parser("list", help="pichhle scan ke devices dikhao")
    i = sub.add_parser("info"); i.add_argument("ip")
    b = sub.add_parser("block"); b.add_argument("target")
    u = sub.add_parser("unblock"); u.add_argument("target")
    w = sub.add_parser("wol"); w.add_argument("mac"); w.add_argument("--broadcast", default=None)
    sub.add_parser("gui", help="interactive menu")
    args = p.parse_args()

    if args.cmd == "scan":
        print_table(full_scan())
    elif args.cmd == "list":
        print_table(load_devices())
    elif args.cmd == "info":
        for d in load_devices():
            if d["ip"] == args.ip:
                for k, v in d.items():
                    print(f"{k:>10}: {v}")
                return
        print("Device nahi mila. Pehle scan chalayein.")
    elif args.cmd == "block":
        block_device(args.target)
    elif args.cmd == "unblock":
        block_device(args.target, unblock=True)
    elif args.cmd == "wol":
        wake_on_lan(args.mac, args.broadcast)
    elif args.cmd == "gui":
        gui()
    else:
        p.print_help()


if __name__ == "__main__":
    main()
