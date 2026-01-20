"""Tests for platform-specific path utilities."""

import os
from unittest.mock import patch

from src.paths import APP_NAME, get_config_dir, get_token_path


class TestGetConfigDir:
    """Tests for get_config_dir function."""

    def test_windows_uses_appdata_env_var(self):
        """Windows reads APPDATA environment variable."""
        with (
            patch("src.paths.sys.platform", "win32"),
            patch.dict(os.environ, {"APPDATA": "/fake/appdata"}, clear=True),
        ):
            result = get_config_dir()
            assert result.parts[-2:] == ("appdata", APP_NAME)

    def test_windows_fallback_uses_home_appdata_roaming(self):
        """Windows falls back to ~/AppData/Roaming when APPDATA not set."""
        with patch("src.paths.sys.platform", "win32"), patch.dict(os.environ, {}, clear=True):
            result = get_config_dir()
            assert result.parts[-3:] == ("AppData", "Roaming", APP_NAME)

    def test_macos_uses_library_application_support(self):
        """macOS uses ~/Library/Application Support."""
        with patch("src.paths.sys.platform", "darwin"):
            result = get_config_dir()
            assert result.parts[-3:] == ("Library", "Application Support", APP_NAME)

    def test_linux_uses_xdg_config_home_when_set(self):
        """Linux reads XDG_CONFIG_HOME environment variable."""
        with (
            patch("src.paths.sys.platform", "linux"),
            patch.dict(os.environ, {"XDG_CONFIG_HOME": "/fake/xdg"}, clear=True),
        ):
            result = get_config_dir()
            assert result.parts[-2:] == ("xdg", APP_NAME)

    def test_linux_fallback_uses_dot_config(self):
        """Linux falls back to ~/.config when XDG_CONFIG_HOME not set."""
        with patch("src.paths.sys.platform", "linux"), patch.dict(os.environ, {}, clear=True):
            result = get_config_dir()
            assert result.parts[-2:] == (".config", APP_NAME)


class TestGetTokenPath:
    """Tests for get_token_path function."""

    def test_token_file_is_inside_config_dir(self):
        """Token path is tokens.json inside config directory."""
        result = get_token_path()
        assert result.parent == get_config_dir()
        assert result.name == "tokens.json"
