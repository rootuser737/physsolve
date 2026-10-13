"""Locate bundled resources and user data, in source runs and in frozen (PyInstaller) builds."""
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)


def resource_path(rel):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / rel


def exe_dir():
    return Path(sys.executable).parent if FROZEN else Path(__file__).parent


def user_config_path():
    root = Path(os.environ.get("APPDATA") or Path.home() / ".config") if os.name == "nt" else Path.home() / ".scisketch"
    return (root / "SciSketch" / "config.json") if os.name == "nt" else (root / "config.json")
