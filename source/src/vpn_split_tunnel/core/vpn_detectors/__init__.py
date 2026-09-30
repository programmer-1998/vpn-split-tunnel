"""VPN Detectors package."""

from __future__ import annotations

from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType, VPNDetector
from vpn_split_tunnel.core.vpn_detectors.manager import VPNDetectorManager, get_vpn_detector_manager

__all__ = [
    "VPNConnection",
    "VPNType",
    "VPNDetector",
    "VPNDetectorManager",
    "get_vpn_detector_manager",
]