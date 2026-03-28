# PHANTOM STRIKE
### Advanced 802.11 WiFi Security Testing Platform

```
 ██████╗ ██╗  ██╗ █████╗ ███╗   ██╗████████╗ ██████╗ ███╗   ███╗
██╔══██╗██║  ██║██╔══██╗████╗  ██║╚══██╔══╝██╔═══██╗████╗ ████║
██████╔╝███████║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║
██╔═══╝ ██╔══██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║
██║     ██║  ██║██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║
╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝
  ███████╗████████╗██████╗ ██╗██╗  ██╗███████╗
  ██╔════╝╚══██╔══╝██╔══██╗██║██║ ██╔╝██╔════╝
  ███████╗   ██║   ██████╔╝██║█████╔╝ █████╗
  ╚════██║   ██║   ██╔══██╗██║██╔═██╗ ██╔══╝
  ███████║   ██║   ██║  ██║██║██║  ██╗███████╗
  ╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝╚══════╝
```

> **Authorized test environments only. You are responsible for compliance with local laws.**

---

Built by **JARVIS** — AI system for **RedParadox**

---

## Features

### Attack Modes
| Mode | Description |
|------|-------------|
| **Targeted Strike** | wifite-style: scan → lock target → auto-attack sequence |
| **Area Blast** | Multi-target simultaneous saturation — select by number, range, or all |
| **Deauth Flood** | Dual-stream: mdk4 + aireplay-ng simultaneously |
| **Auth DoS** | Fake authentication flood — freezes/crashes AP |
| **EAPOL Flood** | Session table fill — blocks all new connections |
| **WIDS Confusion** | Cross-link WDS nodes — overwhelms IDS/IPS |
| **Full Spectrum** | All 4 streams simultaneously — maximum saturation |
| **Handshake Capture** | Force deauth → capture WPA handshake → ready for cracking |
| **Beacon Flood** | Fill airspace with fake APs (IDS ghost mode active) |

### Stealth System
- **MAC Spoofing** — randomize adapter MAC to convincing vendor OUI before every attack
- **MAC Rotation** — background thread rotates identity every N seconds mid-attack
- **TX Power Reduction** — limit physical detection radius
- **Ghost Mode** — mdk4 randomizes frame rate and power (defeats rate-based IDS)
- **Auto-restore** — original hardware MAC restored on session exit

### UI
- Full ASCII art banner
- Animated scan progress bar
- Color-coded AP table (signal strength, encryption type)
- Live attack dashboard — streams active, elapsed time, stealth status
- Interactive menus — no flags needed

---

## Requirements

```bash
sudo apt-get install -y aircrack-ng mdk4
pip3 install rich
```

A monitor-mode capable Wi-Fi adapter is required.
Tested with: **MediaTek MT7921U** (USB, dual-band 2.4/5GHz)

---

## Usage

```bash
# Interactive (recommended)
sudo python3 phantom-strike.py

# Direct CLI
sudo python3 jarvis-wifi-ops.py -i wlan1 -m full -b AA:BB:CC:DD:EE:FF -c 6
sudo python3 jarvis-wifi-ops.py -i wlan1 --scan
```

### Quick Start
```
1 — Targeted Strike    scan → lock one target → choose attack
2 — Area Blast         hit multiple targets simultaneously
3 — Beacon Flood       fill airspace with fake APs
4 — Quick Scan         survey + export JSON
5 — Stealth Config     MAC spoof · rotation interval · TX power
```

---

## Stealth Mode

```
  ── STEALTH ────────────────────────────────
  IDENTITY   28:CD:C1:7F:2A:9B       ← current fake MAC
  ROTATIONS  4x  (next in 12s)       ← rotation count
  TX POWER   5 dBm                   ← reduced from 20 dBm
  GHOST      ACTIVE
```

Enable via menu option `5` before launching any attack.

---

## Tools

| File | Description |
|------|-------------|
| `phantom-strike.py` | Main platform — interactive, full stealth system |
| `jarvis-wifi-ops.py` | CLI tool — direct mode execution, scriptable |

---

## Legal

This software is intended for:
- Authorized penetration testing
- Security research on networks you own
- Controlled lab environments

Unauthorized use against networks you do not own or have explicit written permission to test is illegal in most jurisdictions.

---

*JARVIS // RedParadox — 2026*
