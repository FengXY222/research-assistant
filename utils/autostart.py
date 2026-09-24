"""Reliable Windows logon startup with Task Scheduler and registry fallback."""

from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path
from typing import Any


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ScientificAssistantDesktopWidget"
TASK_NAME = "科研助手 13.0 开机自启"


def _python_executable() -> Path:
    executable = Path(sys.executable).resolve()
    if not getattr(sys, "frozen", False) and executable.name.casefold() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            return pythonw
    return executable


def startup_command() -> str:
    """Return the exact quiet-start command used by both registration modes."""
    executable = _python_executable()
    if getattr(sys, "frozen", False):
        return f'"{executable}" --autostart'
    project_root = Path(__file__).resolve().parents[1]
    return f'"{executable}" "{project_root / "main.py"}" --autostart'


def _startup_probe_arguments() -> list[str]:
    executable = _python_executable()
    if getattr(sys, "frozen", False):
        return [str(executable), "--startup-probe"]
    project_root = Path(__file__).resolve().parents[1]
    return [str(executable), str(project_root / "main.py"), "--startup-probe"]


def probe_startup() -> dict[str, Any]:
    """Actually launch the application probe from a Task-Scheduler-like cwd."""

    arguments = _startup_probe_arguments()
    executable = Path(arguments[0])
    entrypoint = executable if getattr(sys, "frozen", False) else Path(arguments[1])
    if not executable.is_file() or not entrypoint.is_file():
        return {"healthy": False, "exit_code": None, "message": "启动文件不存在"}
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    working_directory = system_root / "System32"
    if not working_directory.is_dir():
        working_directory = executable.parent
    environment = dict(os.environ)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        result = subprocess.run(
            arguments,
            cwd=str(working_directory),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"healthy": False, "exit_code": None, "message": f"真实启动探针失败：{error}"}
    message = (result.stderr or result.stdout or "").strip()[:500]
    return {
        "healthy": result.returncode == 0,
        "exit_code": int(result.returncode),
        "message": "真实启动探针通过" if result.returncode == 0 else f"真实启动探针退出码 {result.returncode}：{message}",
        "working_directory": str(working_directory),
    }


def _run_schtasks(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["schtasks.exe", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=12,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _scheduled_task_exists() -> bool:
    if sys.platform != "win32":
        return False
    try:
        result = _run_schtasks("/Query", "/TN", TASK_NAME)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _registry_value() -> str:
    if sys.platform != "win32":
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return ""
    return str(value).strip()


def autostart_status() -> dict[str, Any]:
    """Probe the actual registration instead of trusting the checkbox."""
    if sys.platform != "win32":
        return {"enabled": False, "healthy": False, "mode": "unsupported", "message": "仅支持 Windows"}
    if _scheduled_task_exists():
        return {
            "enabled": True,
            "healthy": True,
            "mode": "task_scheduler",
            "message": "任务计划程序已验证",
            "command": startup_command(),
        }
    registry = _registry_value()
    if registry:
        healthy = "--autostart" in registry
        return {
            "enabled": True,
            "healthy": healthy,
            "mode": "registry_fallback",
            "message": "注册表回退已验证" if healthy else "旧启动项存在，但命令需要修复",
            "command": registry,
        }
    return {"enabled": False, "healthy": True, "mode": "disabled", "message": "未启用", "command": ""}


def is_autostart_enabled() -> bool:
    return bool(autostart_status().get("enabled"))


def _remove_registry_value() -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        try:
            winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass


def _set_registry_value(command: str) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)


def set_autostart(enabled: bool) -> dict[str, Any]:
    """Register a current-user logon task, falling back to HKCU Run."""
    if sys.platform != "win32":
        if enabled:
            raise OSError("开机自启动仅支持 Windows")
        return autostart_status()
    if not enabled:
        try:
            _run_schtasks("/Delete", "/TN", TASK_NAME, "/F")
        except (OSError, subprocess.SubprocessError):
            pass
        _remove_registry_value()
        status = autostart_status()
        if status["enabled"]:
            raise OSError("开机启动项未能完全移除")
        return status

    command = startup_command()
    task_error = ""
    try:
        created = _run_schtasks(
            "/Create", "/TN", TASK_NAME, "/TR", command,
            "/SC", "ONLOGON", "/DELAY", "0000:30", "/RL", "LIMITED", "/F",
        )
        if created.returncode == 0 and _scheduled_task_exists():
            probe = probe_startup()
            if probe.get("healthy"):
                _remove_registry_value()
                status = autostart_status()
                status["probe"] = probe
                return status
            try:
                _run_schtasks("/Delete", "/TN", TASK_NAME, "/F")
            except (OSError, subprocess.SubprocessError):
                pass
            raise OSError(str(probe.get("message", "真实启动探针失败")))
        task_error = (created.stderr or created.stdout or "任务计划程序拒绝创建").strip()
    except (OSError, subprocess.SubprocessError) as error:
        task_error = str(error)

    _set_registry_value(command)
    status = autostart_status()
    if not status.get("enabled") or not status.get("healthy"):
        raise OSError("无法创建可用的开机启动项" + (f"：{task_error}" if task_error else ""))
    probe = probe_startup()
    if not probe.get("healthy"):
        _remove_registry_value()
        raise OSError(str(probe.get("message", "真实启动探针失败")))
    status["fallback_reason"] = task_error
    status["probe"] = probe
    return status
