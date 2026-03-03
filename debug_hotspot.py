#!/usr/bin/env python3
"""
Pinewood Derby — Hotspot DHCP Diagnostic
Runs as root (or via sudo) and checks every layer that could cause
"Obtaining IP address → failed" on a connecting device.

Usage:  sudo python3 debug_hotspot.py
"""

import subprocess, sys, os, time, shutil, json
from pathlib import Path

CON_NAME = "DerbyHotspot"
SSID     = "PinewoodDerby"
IFACE    = "wlan0"

RED   = "\033[91m"
GRN   = "\033[92m"
YEL   = "\033[93m"
BLU   = "\033[94m"
BOLD  = "\033[1m"
RST   = "\033[0m"

def hdr(title: str):
    print(f"\n{BOLD}{BLU}{'═'*60}{RST}")
    print(f"{BOLD}{BLU}  {title}{RST}")
    print(f"{BOLD}{BLU}{'═'*60}{RST}")

def ok(msg):   print(f"  {GRN}✔  {RST}{msg}")
def warn(msg): print(f"  {YEL}⚠  {RST}{msg}")
def err(msg):  print(f"  {RED}✘  {RST}{msg}")
def info(msg): print(f"  {BLU}ℹ  {RST}{msg}")
def raw(label, text):
    print(f"\n  {BOLD}{label}:{RST}")
    for line in text.strip().splitlines():
        print(f"    {line}")

def run(cmd, quiet=False):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not quiet and r.returncode != 0:
        warn(f"Command {' '.join(cmd[:3])}... exited {r.returncode}")
    return r

# ─────────────────────────────────────────────────────────────────
hdr("0 — Root check")
if os.geteuid() != 0:
    err("This script must run as root.  Re-run with:  sudo python3 debug_hotspot.py")
    sys.exit(1)
ok("Running as root")

# ─────────────────────────────────────────────────────────────────
hdr("1 — Required tools")
for tool in ["nmcli", "ip", "ss", "nft", "journalctl", "systemctl"]:
    if shutil.which(tool):
        ok(f"{tool} found at {shutil.which(tool)}")
    else:
        err(f"{tool} NOT found — some checks will be skipped")

# ─────────────────────────────────────────────────────────────────
hdr("2 — System dnsmasq (port-67 conflict?)")
r = run(["systemctl", "is-active", "dnsmasq"])
state = r.stdout.strip()
if state == "active":
    err(f"System dnsmasq is ACTIVE — this occupies port 67 on ALL interfaces, "
        f"preventing NetworkManager's built-in DHCP from starting.")
    info("Fix: 'systemctl stop dnsmasq' before bringing up the hotspot.")
    info("run.py now does this automatically, but verify it actually ran.")
else:
    ok(f"System dnsmasq is {state} (not conflicting)")

r2 = run(["systemctl", "status", "dnsmasq", "--no-pager", "-l"])
raw("dnsmasq status", r2.stdout or r2.stderr)

# ─────────────────────────────────────────────────────────────────
hdr("3 — Port 67 (DHCP) listener")
r = run(["ss", "-ulpn", "sport", "=", "67"])
if r.stdout.strip():
    raw("Processes listening on UDP port 67", r.stdout)
    if "dnsmasq" in r.stdout:
        err("dnsmasq is holding port 67 — NM cannot start its DHCP server")
    elif "NetworkManager" in r.stdout or "nm-" in r.stdout:
        ok("NetworkManager owns port 67 (expected when hotspot is up)")
    else:
        warn("Unknown process holds port 67 — check above output")
else:
    info("Nothing listening on UDP 67 right now (hotspot probably not active yet)")

# ─────────────────────────────────────────────────────────────────
hdr(f"4 — Interface '{IFACE}' state")
r = run(["ip", "link", "show", IFACE])
if r.returncode != 0:
    err(f"Interface '{IFACE}' does not exist! Check your WiFi adapter.")
else:
    raw(f"ip link {IFACE}", r.stdout)
    if "NO-CARRIER" in r.stdout:
        warn("NO-CARRIER — wlan0 is up but has no connection (normal before hotspot starts)")
    elif "UP" in r.stdout:
        ok("Interface is UP")

r2 = run(["ip", "addr", "show", IFACE])
raw(f"ip addr {IFACE}", r2.stdout or "(no output)")
if "192.168." in r2.stdout:
    ok("wlan0 has an IP address assigned")
else:
    warn("wlan0 has no IPv4 address — hotspot may not be active")

# ─────────────────────────────────────────────────────────────────
hdr("5 — NetworkManager overall status")
r = run(["nmcli", "-t", "general", "status"])
raw("nmcli general", r.stdout)

r2 = run(["nmcli", "device", "status"])
raw("nmcli device status", r2.stdout)

# ─────────────────────────────────────────────────────────────────
hdr(f"6 — Existing NM connection '{CON_NAME}'")
r = run(["nmcli", "con", "show", CON_NAME])
if r.returncode == 0:
    ok(f"Connection '{CON_NAME}' exists in NM")
    # Pull out the key fields
    for keyword in ["ipv4.method", "ipv4.address", "802-11-wireless.mode",
                    "802-11-wireless.ssid", "GENERAL.STATE", "IP4.ADDRESS"]:
        for line in r.stdout.splitlines():
            if line.lower().startswith(keyword.lower()):
                info(line.strip())
else:
    warn(f"Connection '{CON_NAME}' not found (hotspot not started yet)")

# ─────────────────────────────────────────────────────────────────
hdr("7 — Step-by-step hotspot bring-up")

# 7a — stop dnsmasq
info("Stopping system dnsmasq...")
r = run(["systemctl", "stop", "dnsmasq"])
if r.returncode == 0:
    ok("systemctl stop dnsmasq → OK")
else:
    warn(f"Could not stop dnsmasq (may not be installed): {r.stderr.strip()}")

# 7b — delete stale connection
info(f"Deleting stale NM connection '{CON_NAME}' if present...")
r = run(["nmcli", "con", "delete", CON_NAME], quiet=True)
if r.returncode == 0:
    ok("Deleted old connection")
else:
    info("No stale connection to delete (that's fine)")

# 7c — create connection
info("Creating new AP connection (open, ipv4.method shared)...")
cmd = [
    "nmcli", "con", "add",
    "type",  "wifi",
    "ifname", IFACE,
    "con-name", CON_NAME,
    "autoconnect", "no",
    "ssid", SSID,
    "802-11-wireless.mode", "ap",
    "802-11-wireless.band", "bg",
    "802-11-wireless.channel", "6",
    "ipv4.method", "shared",
]
info("Command: " + " ".join(cmd))
r = run(cmd)
if r.returncode == 0:
    ok("nmcli con add → success")
    raw("stdout", r.stdout)
else:
    err("nmcli con add FAILED")
    raw("stdout", r.stdout)
    raw("stderr", r.stderr)

# 7d — bring connection up
info(f"Activating '{CON_NAME}'...")
r = run(["nmcli", "con", "up", CON_NAME])
if r.returncode == 0:
    ok("nmcli con up → success")
    raw("stdout", r.stdout)
else:
    err("nmcli con up FAILED")
    raw("stdout", r.stdout)
    raw("stderr", r.stderr)
    info("Trying to get more detail from journalctl (last 60 NM log lines)...")
    r2 = run(["journalctl", "-u", "NetworkManager", "-n", "60", "--no-pager"])
    raw("NM journal", r2.stdout)

# 7e — check IP assigned to wlan0
time.sleep(2)
info(f"Checking IP assigned to {IFACE} after activation...")
r = run(["nmcli", "-g", "IP4.ADDRESS", "dev", "show", IFACE])
ap_ip_raw = r.stdout.strip()
if ap_ip_raw:
    ap_ip = ap_ip_raw.split("/")[0]
    ok(f"AP IP: {ap_ip}  (raw: {ap_ip_raw})")
else:
    err(f"No IP address on {IFACE} after activation — NM DHCP server may have failed to start")

r2 = run(["ip", "addr", "show", IFACE])
raw(f"ip addr {IFACE} (after activation)", r2.stdout)

# ─────────────────────────────────────────────────────────────────
hdr("8 — Port 67 after activation")
r = run(["ss", "-ulpn", "sport", "=", "67"])
if r.stdout.strip():
    raw("UDP port 67 listeners", r.stdout)
    if "dnsmasq" in r.stdout:
        err("dnsmasq still holds port 67 — DHCP will not work for connecting devices")
    else:
        ok("Port 67 is not held by dnsmasq")
else:
    err("Nothing is listening on UDP 67 — NM's DHCP server did NOT start. "
        "Connecting devices will time out on 'Obtaining IP address'.")
    info("This usually means dnsmasq was still running when 'con up' was called, "
         "OR the ipv4.method=shared profile failed to set up properly.")

# ─────────────────────────────────────────────────────────────────
hdr("9 — NetworkManager journal (last 80 lines)")
r = run(["journalctl", "-u", "NetworkManager", "-n", "80", "--no-pager"])
raw("NM log", r.stdout or r.stderr)

# Key phrases to look for
important = [
    ("dnsmasq", "dnsmasq mentioned — watch for bind/port errors"),
    ("failed to",  "failure message"),
    ("already in use", "port conflict"),
    ("DHCP", "DHCP activity"),
    ("AP mode", "AP mode activation"),
    ("shared",  "shared/NAT config"),
    ("error",   "error message"),
    ("address already in use", "port conflict"),
]
print(f"\n  {BOLD}Key phrases found in NM log:{RST}")
for line in r.stdout.splitlines():
    ll = line.lower()
    for phrase, label in important:
        if phrase.lower() in ll:
            print(f"  {YEL}→{RST} [{label}] {line.strip()}")
            break

# ─────────────────────────────────────────────────────────────────
hdr("10 — nftables state")
r = run(["nft", "list", "ruleset"])
if r.stdout.strip():
    raw("nft ruleset", r.stdout)
else:
    info("nftables ruleset is empty (captive portal not active, or nft not installed)")

# ─────────────────────────────────────────────────────────────────
hdr("11 — Summary & recommendations")
print()

# Re-check port 67
r67 = run(["ss", "-ulpn", "sport", "=", "67"], quiet=True)
has_dhcp = bool(r67.stdout.strip())

# Re-check wlan0 IP
rip = run(["nmcli", "-g", "IP4.ADDRESS", "dev", "show", IFACE], quiet=True)
has_ip = bool(rip.stdout.strip())

if has_dhcp and has_ip:
    ok("DHCP server is running AND wlan0 has an IP — hotspot should be functional!")
    ok(f"AP IP: {rip.stdout.strip()}")
    info("If devices still fail: check nftables (section 10) isn't blocking DHCP packets.")
elif not has_dhcp:
    err("NOTHING is listening on UDP port 67 — clients WILL fail at 'Obtaining IP address'")
    err("Root cause: NM's dnsmasq-based DHCP server failed to start.")
    print()
    print(f"  {BOLD}Likely fixes:{RST}")
    print(f"  1. Ensure system dnsmasq is STOPPED before running run.py")
    print(f"     Run:  sudo systemctl stop dnsmasq && sudo systemctl disable dnsmasq")
    print(f"  2. Check NM journal above for bind/port errors at the time of 'con up'")
    print(f"  3. Try:  sudo systemctl restart NetworkManager  then re-run run.py")
elif not has_ip:
    err("wlan0 has no IP — NM may not have applied the shared profile correctly")
    info("Try:  nmcli con down/up again, or 'systemctl restart NetworkManager'")

print()
info("Done. Share this output to diagnose further.")
