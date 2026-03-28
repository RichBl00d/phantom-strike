#!/bin/bash
# PHANTOM STRIKE — Kali Linux Setup
# Run as root: sudo bash setup.sh

set -e

echo ""
echo " ██████╗ ██╗  ██╗ █████╗ ███╗   ██╗████████╗ ██████╗ ███╗   ███╗"
echo "██╔══██╗██║  ██║██╔══██╗████╗  ██║╚══██╔══╝██╔═══██╗████╗ ████║"
echo "██████╔╝███████║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║"
echo "██╔═══╝ ██╔══██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║"
echo "██║     ██║  ██║██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║"
echo "╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝"
echo ""
echo "  PHANTOM STRIKE — Setup"
echo ""

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "[!] Run as root: sudo bash setup.sh"
    exit 1
fi

echo "[*] Installing system dependencies..."
apt-get update -qq
apt-get install -y aircrack-ng mdk4 python3-pip iw wireless-tools

echo "[*] Installing Python packages..."
pip3 install --break-system-packages anthropic rich 2>/dev/null || pip3 install anthropic rich

echo "[*] Setting execute permissions..."
chmod +x phantom-strike.py phantom-ai.py jarvis-wifi-ops.py

echo ""
echo "[+] Setup complete."
echo ""
echo "    phantom-strike.py  — interactive platform"
echo "    phantom-ai.py      — AI autonomous agent (requires ANTHROPIC_API_KEY)"
echo "    jarvis-wifi-ops.py — CLI tool"
echo ""
echo "    export ANTHROPIC_API_KEY=sk-ant-..."
echo "    sudo python3 phantom-ai.py"
echo ""
