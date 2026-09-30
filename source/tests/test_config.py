"""Tests for configuration management."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from vpn_split_tunnel.utils.config import Config, RoutingConfig, UIConfig, VPNConfig, PolicyConfig


class TestConfig:
    """Test Config class."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = Config()
        assert isinstance(config.routing, RoutingConfig)
        assert isinstance(config.ui, UIConfig)
        assert isinstance(config.vpn, VPNConfig)
        assert isinstance(config.policy, PolicyConfig)

        # Check defaults
        assert config.routing.tun_device == "tun0"
        assert config.routing.fwmark == 5555
        assert config.routing.routing_table == 101
        assert config.ui.theme == "system"
        assert config.ui.language == "system"
        assert config.policy.mode == "include"

    def test_save_load_roundtrip(self) -> None:
        """Test saving and loading config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.ini"
            config = Config()
            config._config_path = config_path

            # Modify some values
            config.routing.tun_device = "wg0"
            config.routing.fwmark = 1234
            config.ui.theme = "dark"
            config.ui.language = "fa"
            config.policy.mode = "exclude"
            config.policy.selected_apps = ["org.mozilla.firefox", "org.gnome.Terminal"]
            config.policy.selected_domains = ["example.com", ".google.com"]
            config.policy.selected_ips = ["192.168.1.0/24", "10.0.0.1"]

            # Save
            config.save()

            # Load into new config
            new_config = Config()
            new_config._config_path = config_path
            new_config.load()

            # Verify
            assert new_config.routing.tun_device == "wg0"
            assert new_config.routing.fwmark == 1234
            assert new_config.ui.theme == "dark"
            assert new_config.ui.language == "fa"
            assert new_config.policy.mode == "exclude"
            assert new_config.policy.selected_apps == ["org.mozilla.firefox", "org.gnome.Terminal"]
            assert new_config.policy.selected_domains == ["example.com", ".google.com"]
            assert new_config.policy.selected_ips == ["192.168.1.0/24", "10.0.0.1"]

    def test_load_nonexistent(self) -> None:
        """Test loading non-existent config doesn't crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "nonexistent.ini"
            config = Config()
            config._config_path = config_path
            config.load()  # Should not raise

    def test_parse_list(self) -> None:
        """Test list parsing."""
        assert Config._parse_list("") == []
        assert Config._parse_list("a") == ["a"]
        assert Config._parse_list("a,b,c") == ["a", "b", "c"]
        assert Config._parse_list(" a , b , c ") == ["a", "b", "c"]
        assert Config._parse_list("a,,b") == ["a", "b"]