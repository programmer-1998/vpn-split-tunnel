"""VPN Detector Manager - aggregates all VPN detectors."""

from __future__ import annotations

import threading
from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType, VPNDetector
from vpn_split_tunnel.core.vpn_detectors.hap import HAPPDetector
from vpn_split_tunnel.core.vpn_detectors.networkmanager import NetworkManagerDetector
from vpn_split_tunnel.core.vpn_detectors.openvpn import OpenVPNDetector
from vpn_split_tunnel.core.vpn_detectors.systemd_networkd import SystemdNetworkdDetector
from vpn_split_tunnel.core.vpn_detectors.tailscale import TailscaleDetector
from vpn_split_tunnel.core.vpn_detectors.unknown import UnknownTunnelDetector
from vpn_split_tunnel.core.vpn_detectors.wireguard import WireGuardDetector
from vpn_split_tunnel.core.vpn_detectors.windscribe import WindscribeDetector
from vpn_split_tunnel.core.vpn_detectors.xray import XrayDetector


class VPNDetectorManager:
    """Manages all VPN detectors and provides unified detection."""

    _instance: VPNDetectorManager | None = None
    _lock = threading.Lock()

    def __new__(cls) -> VPNDetectorManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._detectors: list[VPNDetector] = []
        self._register_detectors()

    def _register_detectors(self) -> None:
        """Register all available detectors in priority order."""
        detector_classes = [
            WireGuardDetector,
            HAPPDetector,  # User says HAPP is running in tun mode
            OpenVPNDetector,
            TailscaleDetector,
            XrayDetector,  # VLESS/VMESS/Trojan via Xray/Sing-box/Clash.Meta
            WindscribeDetector,
            NetworkManagerDetector,
            SystemdNetworkdDetector,
            # Last on purpose: it claims every tunnel-shaped device that no
            # named detector did, so a client this codebase has never heard of
            # still shows up on an unknown machine instead of being invisible.
            UnknownTunnelDetector,
        ]

        for detector_class in detector_classes:
            try:
                detector = detector_class()
                if detector.is_available():
                    self._detectors.append(detector)
            except Exception:
                # Silently skip detectors that fail to initialize
                pass

    def detect_all(self) -> list[VPNConnection]:
        """Detect all active VPN connections across all detectors."""
        all_connections = []
        seen_interfaces = set()
        
        for detector in self._detectors:
            try:
                connections = detector.detect()
                for conn in connections:
                    # Avoid duplicate interfaces - keep first (higher priority) detector
                    if conn.interface not in seen_interfaces:
                        seen_interfaces.add(conn.interface)
                        all_connections.append(conn)
            except Exception:
                # Continue with other detectors
                pass
        return all_connections

    def detect_by_type(self, vpn_type: VPNType) -> list[VPNConnection]:
        """Detect connections for a specific VPN type."""
        for detector in self._detectors:
            if detector.vpn_type == vpn_type:
                try:
                    return detector.detect()
                except Exception:
                    return []
        return []

    def get_available_types(self) -> list[VPNType]:
        """Get list of available VPN types."""
        return [d.vpn_type for d in self._detectors]

    def refresh(self) -> list[VPNConnection]:
        """Force refresh and return all connections."""
        return self.detect_all()


def get_vpn_detector_manager() -> VPNDetectorManager:
    """Get global VPN detector manager instance."""
    return VPNDetectorManager()