#!/usr/bin/env python3
"""
Pinewood Derby OpenSource — Race Server Launcher
Reads config.json and starts the correct combination of modules.

Usage: python3 run.py
"""
import json, sys, os, asyncio, subprocess, webbrowser, threading, signal, atexit, shutil
from pathlib import Path

ROOT       = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"

# Track cleanup actions so we can tear down hotspot / captive portal on exit
_cleanup_actions: list = []

# ── Helpers ──────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print("❌  config.json not found. Run setup first:")
        print("    python3 setup.py")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def check_deps():
    try:
        import fastapi, uvicorn, aiofiles
    except ImportError as e:
        print(f"❌  Missing dependency: {e}")
        print("    Install with:  pip install -r requirements.txt")
        sys.exit(1)


def _run(cmd, check=False, quiet=False):
    """Run a command, print stderr on failure, return CompletedProcess."""
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and not quiet:
        label = " ".join(cmd[:3]) + ("…" if len(cmd) > 3 else "")
        print(f"⚠️   Command failed ({label}): {r.stderr.strip() or r.stdout.strip()}")
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r


def _cleanup():
    """Run any registered cleanup actions (hotspot teardown, nftables flush)."""
    for desc, fn in reversed(_cleanup_actions):
        try:
            fn()
        except Exception as e:
            print(f"⚠️   Cleanup ({desc}): {e}")


# ── Hotspot via NetworkManager (nmcli) ───────────────────────────

CON_NAME = "DerbyHotspot"

def start_hotspot(config: dict):
    mode = config.get("hotspot_mode", "off")
    if mode == "off":
        return
    ssid = config.get("hotspot_ssid", "PinewoodDerby")
    port = config.get("local_port", 8080)

    if os.geteuid() != 0:
        # Re-exec with sudo, injecting user site-packages so deps are found
        import site
        user_site = site.getusersitepackages()
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{user_site}:{existing}" if existing else user_site
        print("📡  Hotspot needs root — re-launching with sudo...")
        try:
            result = subprocess.run(
                ["sudo", "-E", sys.executable] + sys.argv,
                check=False, env=env
            )
            if result.returncode == 0:
                sys.exit(0)
            print("⚠️   sudo failed (rc=%d). Hotspot disabled — continuing without it." % result.returncode)
        except FileNotFoundError:
            print("⚠️   sudo not available. Hotspot disabled — continuing without it.")
        except KeyboardInterrupt:
            sys.exit(0)
        return

    # ── We are root. Use nmcli to create the hotspot. ─────────────
    if not shutil.which("nmcli"):
        print("⚠️   nmcli not found. Install NetworkManager for hotspot support.")
        return

    print(f"📡  Starting WiFi hotspot '{ssid}' via NetworkManager...")

    # Remove any stale connection with this name
    _run(["nmcli", "con", "delete", CON_NAME], quiet=True)

    # Create a new WiFi AP connection (open, no password)
    # Do NOT pass any wifi-sec.* properties — omitting security entirely = open network
    r = _run([
        "nmcli", "con", "add",
        "type", "wifi",
        "ifname", "wlan0",
        "con-name", CON_NAME,
        "autoconnect", "no",
        "ssid", ssid,
        "802-11-wireless.mode", "ap",
        "802-11-wireless.band", "bg",
        "802-11-wireless.channel", "6",
        "ipv4.method", "shared",              # NM runs its own DHCP + DNS
    ])
    if r.returncode != 0:
        print("❌  Failed to create hotspot connection")
        return

    # Stop system dnsmasq so NetworkManager's internal DHCP can bind to port 67.
    # dnsmasq is 'enabled' so systemd can restart it quickly — we poll until
    # port 67 is actually free before continuing (avoids a race condition).
    _dnsmasq_was_running = subprocess.run(
        ["systemctl", "is-active", "--quiet", "dnsmasq"]
    ).returncode == 0
    if _dnsmasq_was_running:
        print("📡  Stopping system dnsmasq so NM can use port 67...")
        _run(["systemctl", "stop", "dnsmasq"])
        # Poll up to 5 s for port 67 to be released
        import time as _time
        for _i in range(10):
            r67 = subprocess.run(
                ["ss", "-ulpn", "sport", "=", "67"],
                capture_output=True, text=True
            )
            if "dnsmasq" not in r67.stdout and ":67" not in r67.stdout:
                print("✅  Port 67 is free")
                break
            _time.sleep(0.5)
        else:
            print("⚠️   Port 67 still in use after 5 s — hotspot DHCP may fail")

    # Also remove any stale NM connection file that may have a cached IP address
    stale_file = Path("/etc/NetworkManager/system-connections") / f"{CON_NAME}.nmconnection"
    if stale_file.exists():
        stale_file.unlink()
        _run(["systemctl", "reload", "NetworkManager"], quiet=True)

    # Bring it up
    r = _run(["nmcli", "con", "up", CON_NAME])
    if r.returncode != 0:
        print("❌  Failed to activate hotspot")
        _run(["nmcli", "con", "delete", CON_NAME], quiet=True)
        if _dnsmasq_was_running:
            _run(["systemctl", "start", "dnsmasq"], quiet=True)
        return

    # Register cleanup: tear down hotspot on exit, restart system dnsmasq
    _cleanup_actions.append(("hotspot", lambda: (
        _run(["nmcli", "con", "down", CON_NAME], quiet=True),
        _run(["nmcli", "con", "delete", CON_NAME], quiet=True),
    )))
    if _dnsmasq_was_running:
        _cleanup_actions.append(("dnsmasq-restore", lambda: _run(["systemctl", "start", "dnsmasq"], quiet=True)))

    # Detect actual IP assigned to wlan0 by NetworkManager
    _ip_r = subprocess.run(["nmcli", "-g", "IP4.ADDRESS", "dev", "show", "wlan0"],
                           capture_output=True, text=True)
    ap_ip = (_ip_r.stdout.strip().split("/")[0] or "192.168.4.1") if _ip_r.returncode == 0 else "192.168.4.1"

    print(f"✅  Hotspot active — SSID: {ssid}  |  AP IP: {ap_ip}")

    # ── Captive portal via nftables ───────────────────────────────
    if mode == "captive_portal":
        if not shutil.which("nft"):
            print("⚠️   nft (nftables) not found — captive portal disabled")
        else:
            # DNS redirect: force all DNS queries to our dnsmasq (192.168.4.1)
            # HTTP redirect: redirect port 80 → race server port
            # HTTPS redirect: redirect port 443 → race server port
            nft_rules = f"""
table ip derby_captive {{
    chain prerouting {{
        type nat hook prerouting priority dstnat; policy accept;
        iifname "wlan0" tcp dport 80 redirect to :{port}
        iifname "wlan0" tcp dport 443 redirect to :{port}
        iifname "wlan0" udp dport 53 dnat to {ap_ip}:53
    }}
}}
"""
            Path("/tmp/derby_nft.conf").write_text(nft_rules)
            r = _run(["nft", "-f", "/tmp/derby_nft.conf"])
            if r.returncode == 0:
                _cleanup_actions.append(("captive-portal", lambda: (
                    _run(["nft", "delete", "table", "ip", "derby_captive"], quiet=True),
                )))
                print(f"✅  Captive portal active — devices will auto-redirect to the race app")
            else:
                print("⚠️   Failed to set up captive portal nftables rules")

    if mode != "captive_portal":
        print(f"    Connect to '{ssid}', then open: http://{ap_ip}:{port}")


def setup_mdns(config: dict):
    """Advertise derby.local via avahi (mDNS) so hostname works on LAN."""
    port = config.get("local_port", 8080)
    service_xml = f"""<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name>Pinewood Derby</name>
  <service>
    <type>_http._tcp</type>
    <port>{port}</port>
    <txt-record>path=/</txt-record>
  </service>
</service-group>"""
    svc_path = Path("/etc/avahi/services/pinewood-derby.service")
    try:
        svc_path.write_text(service_xml)
        subprocess.run(["systemctl", "restart", "avahi-daemon"], capture_output=True)
        _cleanup_actions.append(("mdns", lambda: (
            svc_path.unlink(missing_ok=True),
            subprocess.run(["systemctl", "restart", "avahi-daemon"], capture_output=True),
        )))
        print(f"✅  mDNS: reachable at http://derby.local:{port}")
    except Exception:
        pass  # avahi not available, skip silently


# ── GPIO (Module 7) ──────────────────────────────────────────────

def start_gpio(config: dict, loop: asyncio.AbstractEventLoop,
               broadcast_fn, gpio_scorer=None):
    """Start GPIO listener if scoring_mode is sensor_gpio."""
    if config.get("scoring_mode") != "sensor_gpio":
        return None
    sys.path.insert(0, str(ROOT / "backend"))
    from scoring_gpio import GPIOScoring
    scorer = GPIOScoring(config, broadcast_fn)
    scorer.start(loop)
    return scorer


# ── Main ─────────────────────────────────────────────────────────

def main():
    config = load_config()
    check_deps()

    # Register cleanup so hotspot / captive-portal are torn down on exit
    atexit.register(_cleanup)
    def _sig_handler(signum, frame):
        print("\n🛑  Shutting down…")
        _cleanup()
        sys.exit(0)
    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    backend_mode = config.get("backend_mode", "supabase")
    scoring_mode = config.get("scoring_mode", "heat")
    frontend_mode = config.get("frontend_mode", "local")
    port = config.get("local_port", 8080)
    host = config.get("local_host", "0.0.0.0")

    print(f"\n🏎️   Pinewood Derby — Race Server")
    print(f"    Backend:  {backend_mode}")
    print(f"    Scoring:  {scoring_mode}")
    print(f"    Frontend: {frontend_mode}")

    if backend_mode == "supabase":
        # Supabase mode — no local server needed, just open the frontend
        print("\n✅  Supabase mode: no local server to start.")
        if frontend_mode == "local":
            frontend = ROOT / "frontend" / "index.html"
            print(f"    Opening: {frontend}")
            webbrowser.open(str(frontend))
        else:
            print("    Push your code to GitHub Pages and open your site URL.")
        return

    # ── Local backend mode ────────────────────────────────────────
    sys.path.insert(0, str(ROOT / "backend"))
    import uvicorn
    from server import create_app

    app = create_app(config)

    # Start hotspot if configured
    if config.get("hotspot_mode", "off") != "off":
        start_hotspot(config)
        setup_mdns(config)

    display_host = "localhost" if host == "0.0.0.0" else host
    url          = f"http://{display_host}:{port}"
    print(f"\n✅  Starting server on {url}")
    if frontend_mode == "local":
        print(f"    Frontend served at:  {url}")
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    print("    Press Ctrl+C to stop\n")

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
