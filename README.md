# 📡 Network Scanner — WiFi Device Manager

Ye tool aapke connected WiFi/LAN network par maujood **saare devices ko scan** karta hai aur unhe **manage** karne ki suvidha deta hai.

## ✨ Features
| Feature | Command |
|---|---|
| 🔍 Network Scan (ARP sweep + ping fallback) | `python3 network_scanner.py scan` |
| 📋 Devices list dekho | `python3 network_scanner.py list` |
| ℹ️ Kisi device ki detail | `python3 network_scanner.py info <IP>` |
| 🚫 Device block karo (MAC/IP firewall rule) | `sudo python3 network_scanner.py block <IP_OR_MAC>` |
| ✅ Device unblock karo | `sudo python3 network_scanner.py unblock <IP_OR_MAC>` |
| ⚡ Wake-on-LAN magic packet | `python3 network_scanner.py wol <MAC>` |
| 🖥️ Interactive menu | `python3 network_scanner.py gui` |

Har device ke saath ye details milti hain:
- **IP address**
- **MAC address**
- **Hostname** (reverse DNS se)
- **Vendor/Manufacturer** (MAC OUI lookup se — Apple, Samsung, TP-Link, etc.)
- Aapki apni device pe `<- YOU` tag
- Blocked devices pe `[BLOCKED]` tag

Scan ka record `scan_data/devices.json` aur `scan_data/history.log` mein save hota hai.

## 🛠️ Installation
```bash
pip install -r requirements.txt   # scapy + psutil
```

## 🚀 Usage
```bash
# 1) Install dependencies
pip install -r requirements.txt

# 2) Best results ke liye sudo se scan karo (ARP scanning ke liye)
sudo python3 network_scanner.py scan

# 3) Ya interactive menu use karo
sudo python3 network_scanner.py gui
```

Example output:
```
------------------------------------------------------------------------------
IP               MAC                  HOSTNAME             VENDOR
------------------------------------------------------------------------------
192.168.1.1      AC:84:C6:11:22:33    router.local         TP-Link
192.168.1.5      F0:9F:xx:xx:xx:xx    Ahmeds-iPhone        Apple  <- YOU
192.168.1.9      B0:BE:xx:xx:xx:xx    samsung-tv           Samsung  [BLOCKED]
------------------------------------------------------------------------------
Total: 3 devices
```

## 🖥️ Web Dashboard (React Frontend)

Devices ka data browser mein graphically dekhne aur manage karne ke liye ek **React (Vite)** dashboard bhi hai:

```bash
# Terminal 1 — API server chalao (port 5000)
python3 api_server.py

# Terminal 2 — React frontend chalao (port 3000)
cd frontend
npm install
npm run dev
```

Phir browser mein kholo: **http://localhost:3000**

Dashboard features:
- 🔍 ek click mein network scan (Scan Network button)
- 📊 stats cards: Total / Online / Blocked / Known Vendors
- 🔎 IP, MAC, hostname ya vendor se live search
- 🔒 Block / 🔓 Unblock buttons har device pe
- ⚡ Wake-on-LAN button
- Status indicators: green dot = online, red = blocked, "This PC" badge

API endpoints (`api_server.py`, port 5000):

| Method | Endpoint | Kaam |
|---|---|---|
| GET | `/api/devices` | pichhla scan result |
| POST | `/api/scan` | fresh scan chalao |
| GET | `/api/device/<ip>` | ek device ki detail |
| POST | `/api/block` | `{"target": "IP/MAC"}` block |
| POST | `/api/unblock` | `{"target": "IP/MAC"}` unblock |
| POST | `/api/wol` | `{"mac": "..."} `wake karo |
| GET | `/api/stats` | summary counts |

## 📝 Notes
- **Root/sudo**: ARP scan (`scapy`) aur block/unblock (`iptables`) ke liye zaroori hai. Bina sudo ke tool automatic **ping-sweep fallback** use karta hai.
- **Block/Unblock**: Ye aapki machine se us device ka traffic rokta hai (firewall rule). Router-level blocking ke liye router admin panel kholein.
- **Wake-on-LAN**: Target PC BIOS/OS mein WOL enable hona chahiye.
- Sirf apne hi network par use karein — doosron ke network scan karna gair-qanooni ho sakta hai.
