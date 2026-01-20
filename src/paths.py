"""Platform-specific path utilities."""

import os
import sys
from pathlib import Path

APP_NAME = "anidb-to-trakt"


def get_config_dir() -> Path:
    """Get platform-specific configuration directory."""
    if sys.platform == "win32":
        # Windows: use APPDATA
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    elif sys.platform == "darwin":
        # macOS: use Application Support
        return Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        # Linux/Unix: use XDG_CONFIG_HOME or ~/.config
        config_home = os.getenv("XDG_CONFIG_HOME")
        if config_home:
            return Path(config_home) / APP_NAME
        return Path.home() / ".config" / APP_NAME


def get_token_path() -> Path:
    """Get platform-specific token storage path."""
    return get_config_dir() / "tokens.json"
