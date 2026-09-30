"""Configuration management for VPN Split Tunnel."""

from __future__ import annotations

import configparser
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RoutingConfig:
    """Network routing configuration.

    `rule_priority` must be *lower* than the priority the active VPN client's
    own rules use. On this machine HAPP installs `from all nop` at priority
    9010, which terminates route lookup; a rule placed after it never runs.
    8500 sits above the system default rules (32765) and below the VPN
    client's block, which is where a per-app override has to go.
    """
    tun_device: str = "tun0"
    fwmark: int = 5555
    routing_table: int = 101
    rule_priority: int = 8500
    cgroup_slice: str = "vpn-split-tunnel.slice"
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 10808


@dataclass
class UIConfig:
    """UI configuration."""
    theme: str = "system"  # system, light, dark
    language: str = "system"  # system, en, fa
    window_width: int = 1000
    window_height: int = 700
    remember_geometry: bool = True


@dataclass
class VPNConfig:
    """VPN-specific configuration."""
    auto_detect: bool = True
    preferred_vpn: str = ""  # windscribe, hap, wireguard, xray, etc.
    custom_vpn_commands: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class PolicyConfig:
    """Split tunneling policy configuration."""
    mode: str = "include"  # include, exclude
    selected_apps: list[str] = field(default_factory=list)
    selected_domains: list[str] = field(default_factory=list)
    selected_ips: list[str] = field(default_factory=list)
    apply_to_all_users: bool = False


@dataclass
class Config:
    """Main configuration container."""
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    vpn: VPNConfig = field(default_factory=VPNConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)

    _config_path: Path = field(default_factory=lambda: Path.home() / ".config" / "vpn-split-tunnel" / "config.ini", init=False)
    _parser: configparser.ConfigParser = field(default_factory=configparser.ConfigParser, init=False)

    def load(self, path: Path | None = None) -> None:
        """Load configuration from file."""
        if path:
            self._config_path = path

        if not self._config_path.exists():
            logger.debug("Config file not found: %s", self._config_path)
            return

        logger.debug("Loading config from %s", self._config_path)
        self._parser.read(self._config_path)

        # Routing
        if self._parser.has_section("routing"):
            s = self._parser["routing"]
            self.routing.tun_device = s.get("tun_device", self.routing.tun_device)
            self.routing.fwmark = s.getint("fwmark", self.routing.fwmark)
            self.routing.routing_table = s.getint("routing_table", self.routing.routing_table)
            self.routing.rule_priority = s.getint("rule_priority", self.routing.rule_priority)
            self.routing.cgroup_slice = s.get("cgroup_slice", self.routing.cgroup_slice)
            self.routing.proxy_host = s.get("proxy_host", self.routing.proxy_host)
            self.routing.proxy_port = s.getint("proxy_port", self.routing.proxy_port)

        # UI
        if self._parser.has_section("ui"):
            s = self._parser["ui"]
            self.ui.theme = s.get("theme", self.ui.theme)
            self.ui.language = s.get("language", self.ui.language)
            self.ui.window_width = s.getint("window_width", self.ui.window_width)
            self.ui.window_height = s.getint("window_height", self.ui.window_height)
            self.ui.remember_geometry = s.getboolean("remember_geometry", self.ui.remember_geometry)

        # VPN
        if self._parser.has_section("vpn"):
            s = self._parser["vpn"]
            self.vpn.auto_detect = s.getboolean("auto_detect", self.vpn.auto_detect)
            self.vpn.preferred_vpn = s.get("preferred_vpn", self.vpn.preferred_vpn)

        # Policy
        if self._parser.has_section("policy"):
            s = self._parser["policy"]
            self.policy.mode = s.get("mode", self.policy.mode)
            self.policy.selected_apps = self._parse_list(s.get("selected_apps", ""))
            self.policy.selected_domains = self._parse_list(s.get("selected_domains", ""))
            self.policy.selected_ips = self._parse_list(s.get("selected_ips", ""))
            self.policy.apply_to_all_users = s.getboolean("apply_to_all_users", self.policy.apply_to_all_users)

    def save(self, path: Path | None = None) -> None:
        """Save configuration to file."""
        if path:
            self._config_path = path

        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        # Routing
        self._parser["routing"] = {
            "tun_device": self.routing.tun_device,
            "fwmark": str(self.routing.fwmark),
            "routing_table": str(self.routing.routing_table),
            "rule_priority": str(self.routing.rule_priority),
            "cgroup_slice": self.routing.cgroup_slice,
            "proxy_host": self.routing.proxy_host,
            "proxy_port": str(self.routing.proxy_port),
        }

        # UI
        self._parser["ui"] = {
            "theme": self.ui.theme,
            "language": self.ui.language,
            "window_width": str(self.ui.window_width),
            "window_height": str(self.ui.window_height),
            "remember_geometry": str(self.ui.remember_geometry).lower(),
        }

        # VPN
        self._parser["vpn"] = {
            "auto_detect": str(self.vpn.auto_detect).lower(),
            "preferred_vpn": self.vpn.preferred_vpn,
        }

        # Policy
        self._parser["policy"] = {
            "mode": self.policy.mode,
            "selected_apps": ",".join(self.policy.selected_apps),
            "selected_domains": ",".join(self.policy.selected_domains),
            "selected_ips": ",".join(self.policy.selected_ips),
            "apply_to_all_users": str(self.policy.apply_to_all_users).lower(),
        }

        with self._config_path.open("w") as f:
            self._parser.write(f)

    @staticmethod
    def _parse_list(value: str) -> list[str]:
        """Parse comma-separated list."""
        if not value:
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    @classmethod
    def get_default_config_path(cls) -> Path:
        """Get default config path following XDG spec."""
        xdg_config = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        return Path(xdg_config) / "vpn-split-tunnel" / "config.ini"


# Global config instance
config = Config()


def get_config() -> Config:
    """Get global configuration instance."""
    return config