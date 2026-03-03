#!/usr/bin/env python3
"""
Pinewood Derby OpenSource — Race Server Launcher
Reads config.json and starts the correct combination of modules.

Usage: python3 run.py
"""
import json, sys, os, asyncio, subprocess, webbrowser, threading
from pathlib import Path

ROOT       = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"

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


# ── Hotspot (Raspberry Pi / Linux) ───────────────────────────────

def start_hotspot(config: dict):
    mode = config.get("hotspot_mode", "off")
    if mode == "off":
        return
    ssid = config.get("hotspot_ssid", "PinewoodDerby")
    port = config.get("local_port", 8080)

    if os.geteuid() != 0:
        # Try to re-exec with sudo so user-installed packages remain visible.
        # Inject user site-packages into PYTHONPATH so root can find them.
        import site
        user_site = site.getusersitepackages()
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{user_site}:{existing}" if existing else user_site
        print("📡  Hotspot requested — re-launching as root (Ctrl+C to cancel)...")
        try:
            result = subprocess.run(
                ["sudo", "-E", sys.executable] + sys.argv,
                check=False, env=env
            )
            if result.returncode == 0:
                sys.exit(0)
            # sudo failed (e.g. wrong password, not in sudoers) — fall through
            print("⚠️   sudo failed (rc=%d). Hotspot disabled — continuing without it." % result.returncode)
        except FileNotFoundError:
            print("⚠️   sudo not available. Hotspot disabled — continuing without it.")
        except KeyboardInterrupt:
            sys.exit(0)
        return

    # Check required tools are installed
    import shutil
    missing = [t for t in ("hostapd", "dnsmasq") if not shutil.which(t)]
    if missing:
        print(f"⚠️   Hotspot disabled — missing tools: {', '.join(missing)}")
        print(f"    Install with:  sudo apt install {' '.join(missing)}")
        return

    print(f"📡  Starting WiFi hotspot '{ssid}'...")

    # Write hostapd config
    hostapd_conf = f"""interface=wlan0
driver=nl80211
ssid={ssid}
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
"""
    # Write dnsmasq config
    dnsmasq_conf = f"""interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
address=/#/192.168.4.1
"""
    Path("/tmp/derby_hostapd.conf").write_text(hostapd_conf)
    Path("/tmp/derby_dnsmasq.conf").write_text(dnsmasq_conf)

    cmds = [
        ["ip", "addr", "add", "192.168.4.1/24", "dev", "wlan0"],
        ["ip", "link", "set", "wlan0", "up"],
        ["hostapd", "-B", "/tmp/derby_hostapd.conf"],
        ["dnsmasq", "--conf-file=/tmp/derby_dnsmasq.conf", "--no-daemon", "&"],
    ]
    if mode == "captive_portal":
        # Redirect all HTTP traffic to race app
        cmds += [
            ["iptables", "-t", "nat", "-A", "PREROUTING", "-i", "wlan0",
             "-p", "tcp", "--dport", "80", "-j", "REDIRECT", "--to-port", str(port)],
        ]

    for cmd in cmds:
        try:
            subprocess.run(cmd, capture_output=True)
        except FileNotFoundError as e:
            print(f"⚠️   Hotspot command failed: {e}")
            return

    print(f"✅  Hotspot active — SSID: {ssid}")
    if mode == "captive_portal":
        print(f"    Any device connecting to '{ssid}' will auto-open the race app")
    else:
        print(f"    Connect to '{ssid}', then open: http://192.168.4.1:{port}")


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
    try:
        Path("/etc/avahi/services/pinewood-derby.service").write_text(service_xml)
        subprocess.run(["systemctl", "restart", "avahi-daemon"], capture_output=True)
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
