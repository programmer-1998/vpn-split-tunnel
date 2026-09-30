"""UI Package."""

from __future__ import annotations

from vpn_split_tunnel.ui.main_window import MainWindow
from vpn_split_tunnel.ui.vpn_selector import VPNSelector
from vpn_split_tunnel.ui.mode_selector import ModeSelector
from vpn_split_tunnel.ui.app_selector import AppSelector
from vpn_split_tunnel.ui.domain_editor import DomainEditor
from vpn_split_tunnel.ui.action_bar import ActionBar
from vpn_split_tunnel.ui.preferences import PreferencesDialog
from vpn_split_tunnel.ui.about import create_about_dialog

__all__ = [
    "MainWindow",
    "VPNSelector",
    "ModeSelector",
    "AppSelector",
    "DomainEditor",
    "ActionBar",
    "PreferencesDialog",
    "create_about_dialog",
]