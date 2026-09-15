"""Windows current-user startup registration for the packaged desktop widget."""

from __future__ import annotations

import sys
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ScientificAssistantDesktopWidget"


def startup_command() -> str:
    """Return the command stored in the current user's Run registry key."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    project_root = Path(__file__).resolve().parents[1]
    return f'"{Path(sys.executable).resolve()}" "{project_root / "main.py"}"'


def is_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return False
    return bool(str(value).strip())


def set_autostart(enabled: bool) -> None:
    """Enable or remove automatic startup without requiring administrator rights."""
    if sys.platform != "win32":
        if enabled:
            raise OSError("开机自启动仅支持 Windows")
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, startup_command())
            return
        try:
            winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
