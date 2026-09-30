#!/usr/bin/env python3
"""Debug script to test VPN detection."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from vpn_split_tunnel.core.vpn_detectors.manager import get_vpn_detector_manager
from vpn_split_tunnel.core.vpn_detectors.hap import HAPPDetector
from vpn_split_tunnel.core.vpn_detectors.wireguard import WireGuardDetector
from vpn_split_tunnel.core.vpn_detectors.openvpn import OpenVPNDetector
from vpn_split_tunnel.core.vpn_detectors.tailscale import TailscaleDetector
from vpn_split_tunnel.core.vpn_detectors.xray import XrayDetector
from vpn_split_tunnel.core.vpn_detectors.windscribe import WindscribeDetector
from vpn_split_tunnel.core.vpn_detectors.networkmanager import NetworkManagerDetector
from vpn_split_tunnel.core.vpn_detectors.systemd_networkd import SystemdNetworkdDetector

import subprocess
import json

def run_cmd(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr

def main():
    print("=" * 60)
    print("VPN Detection Debug")
    print("=" * 60)

    # Show all interfaces
    print("\n📡 All network interfaces (ip link):")
    rc, out, err = run_cmd(["ip", "-j", "link", "show"])
    if rc == 0:
        interfaces = json.loads(out)
        for iface in interfaces:
            name = iface.get("ifname", "")
            flags = iface.get("flags", [])
            state = "UP" if "UP" in flags else "DOWN"
            print(f"  {name:15} {state:4}  flags={flags}")

    # Show interfaces with IPs
    print("\n📡 Interfaces with IPv4 addresses:")
    rc, out, err = run_cmd(["ip", "-j", "addr", "show"])
    if rc == 0:
        addr_data = json.loads(out)
        for addr_info in addr_data:
            name = addr_info.get("ifname", "")
            for addr in addr_info.get("addr_info", []):
                if addr.get("family") == "inet":
                    print(f"  {name:15} {addr.get('local')}/{addr.get('prefixlen')}")

    # Check systemd services
    print("\n🔧 Systemd VPN services:")
    vpn_services = [
        "hap", "windscribe", "openvpn", "wg-quick", 
        "tailscaled", "xray", "sing-box", "clash-meta", "mihomo"
    ]
    for svc in vpn_services:
        rc, out, err = run_cmd(["systemctl", "is-active", svc])
        status = out.strip() if rc == 0 else "inactive/not-found"
        if status == "active":
            print(f"  ✅ {svc}: {status}")

    # Test each detector
    print("\n🔍 Individual Detectors:")
    
    detectors = [
        ("HAPP", HAPPDetector()),
        ("WireGuard", WireGuardDetector()),
        ("OpenVPN", OpenVPNDetector()),
        ("Tailscale", TailscaleDetector()),
        ("Xray/Sing-box", XrayDetector()),
        ("Windscribe", WindscribeDetector()),
        ("NetworkManager", NetworkManagerDetector()),
        ("systemd-networkd", SystemdNetworkdDetector()),
    ]

    for name, detector in detectors:
        print(f"\n  {name}:")
        try:
            avail = detector.is_available()
            print(f"    Available: {avail}")
            if avail:
                conns = detector.detect()
                if conns:
                    for c in conns:
                        print(f"    ✅ {c.get_display_name()} - Interface: {c.interface}, Gateway: {c.gateway_ip}")
                else:
                    print(f"    ❌ No connections detected")
            else:
                print(f"    (not available)")
        except Exception as e:
            print(f"    ⚠️ Error: {e}")

    # Manager
    print("\n" + "=" * 60)
    print("📋 Manager (All VPNs):")
    manager = get_vpn_detector_manager()
    all_conns = manager.detect_all()
    if all_conns:
        for c in all_conns:
            print(f"  ✅ {c.get_display_name()} - Interface: {c.interface}")
    else:
        print("  ❌ No active VPN connections detected")

    # Check HAP specifically
    print("\n🔍 HAPP Deep Check:")
    rc, out, err = run_cmd(["systemctl", "status", "hap", "--no-pager"])
    if rc == 0:
        print("  systemctl status hap:")
        for line in out.split("\n")[:20]:
            print(f"    {line}")
    else:
        print("  hap service not found or error")

    # Check HAPP CLI
    import shutil
    for cmd in ["happ", "happ-cli"]:
        if not shutil.which(cmd):
            print(f"  {cmd}: not in PATH")
            continue
        rc, out, err = run_cmd([cmd, "status"])
        if rc == 0:
            print(f"  {cmd} status:")
            for line in out.split("\n")[:10]:
                print(f"    {line}")
            break
    else:
        print("  HAPP CLI not available or not in PATH")

if __name__ == "__main__":
    main()