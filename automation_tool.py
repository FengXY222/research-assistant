"""轻点自动化 - Windows 鼠标动作录制与回放工具。

仅依赖 Python 标准库，可直接由 PyInstaller 打包为单文件 EXE。
"""

from __future__ import annotations

import copy
import ctypes
from ctypes import Structure, Union, byref, wintypes
import json
import math
import os
from pathlib import Path
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_NAME = "轻点自动化"
APP_VERSION = "2.0.0"
PROJECT_EXT = ".json"

WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
LLMHF_INJECTED = 0x00000001

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
winmm = ctypes.windll.winmm


class POINT(Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


ULONG_PTR = wintypes.WPARAM


class MSLLHOOKSTRUCT(Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KBDLLHOOKSTRUCT(Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExW.argtypes = (
    ctypes.c_int,
    HOOKPROC,
    wintypes.HINSTANCE,
    wintypes.DWORD,
)
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.PostThreadMessageW.argtypes = (
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
user32.PostThreadMessageW.restype = wintypes.BOOL


def enable_dpi_awareness() -> None:
    """避免 Windows 显示缩放导致录制与回放坐标不一致。"""
    try:
        # PER_MONITOR_AWARE_V2；必须在 Tk 创建窗口前调用。
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass


def app_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    target = base / "QingDianAutomation"
    target.mkdir(parents=True, exist_ok=True)
    return target


def virtual_screen() -> dict[str, int]:
    return {
        "x": user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        "y": user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        "width": max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
        "height": max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
    }


def cursor_position() -> tuple[int, int]:
    point = POINT()
    if not user32.GetCursorPos(byref(point)):
        raise OSError("无法读取鼠标位置")
    return point.x, point.y


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def to_int(value, default: int, minimum: int | None = None) -> int:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        result = default
    return max(minimum, result) if minimum is not None else result


def to_float(value, default: float, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, result) if minimum is not None else result


DEFAULT_SETTINGS = {
    "run_mode": "cycles",
    "cycles": 1,
    "duration_minutes": 10.0,
    "speed": 1.0,
    "start_delay": 3.0,
    "cycle_delay": 1.0,
    "smooth_move": True,
    "move_duration_ms": 120,
    "scale_coordinates": False,
    "corner_pause": True,
}


def normalize_action(raw: dict, previous_timestamp: float | None = None) -> dict:
    """兼容旧版动作，并把脏数据限制在安全范围内。"""
    kind = raw.get("type", "click")
    if kind not in {"click", "scroll", "wait"}:
        kind = "click"

    timestamp = to_float(raw.get("timestamp_recording"), 0.0, 0.0)
    if "delay" in raw:
        delay = to_float(raw.get("delay"), 0.3, 0.0)
    elif previous_timestamp is None:
        delay = min(timestamp, 10.0)
    else:
        delay = max(0.0, timestamp - previous_timestamp)

    action = {
        "enabled": bool(raw.get("enabled", True)),
        "name": str(raw.get("name", "")).strip()[:80],
        "type": kind,
        "delay": round(min(delay, 86400.0), 3),
        "repeat": min(to_int(raw.get("repeat"), 1, 1), 9999),
    }
    if kind in {"click", "scroll"}:
        action["x"] = to_int(raw.get("x", raw.get("screen_x")), 0)
        action["y"] = to_int(raw.get("y", raw.get("screen_y")), 0)
        action["jitter"] = min(to_int(raw.get("jitter"), 0, 0), 5000)
        region = raw.get("region")
        if isinstance(region, dict):
            action["region"] = {
                "x": to_int(region.get("x"), action["x"]),
                "y": to_int(region.get("y"), action["y"]),
                "width": min(to_int(region.get("width"), 0, 0), 20000),
                "height": min(to_int(region.get("height"), 0, 0), 20000),
            }
    if kind == "click":
        button = str(raw.get("button", "left"))
        action["button"] = button if button in {"left", "right", "middle"} else "left"
        action["hold_ms"] = min(
            to_int(raw.get("hold_ms", raw.get("hold_duration_ms")), 60, 1), 60000
        )
    elif kind == "scroll":
        action["amount"] = int(clamp(to_int(raw.get("amount"), -120), -12000, 12000))
    else:
        action["duration"] = min(to_float(raw.get("duration"), 1.0, 0.01), 86400.0)
    return action


class Project:
    def __init__(self) -> None:
        self.path: Path | None = None
        self.actions: list[dict] = []
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.recorded_screen = virtual_screen()
        self.dirty = False

    def clear(self) -> None:
        self.path = None
        self.actions.clear()
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.recorded_screen = virtual_screen()
        self.dirty = False

    def load(self, path: str | os.PathLike) -> None:
        source = Path(path)
        with source.open("r", encoding="utf-8-sig") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
            raise ValueError("这不是有效的动作项目文件")

        actions = []
        previous = None
        for item in data["actions"]:
            if not isinstance(item, dict):
                continue
            actions.append(normalize_action(item, previous))
            previous = to_float(item.get("timestamp_recording"), previous or 0.0, 0.0)
        if not actions:
            raise ValueError("项目中没有可用动作")

        settings = copy.deepcopy(DEFAULT_SETTINGS)
        incoming = data.get("settings", data.get("execution_settings", {}))
        if isinstance(incoming, dict):
            legacy_mode = incoming.get("mode")
            if legacy_mode:
                incoming = dict(incoming)
                incoming["run_mode"] = {
                    "fixed_count": "cycles",
                    "fixed_duration": "duration",
                    "infinite": "infinite",
                }.get(legacy_mode, "cycles")
                incoming["cycles"] = incoming.get("count", settings["cycles"])
                old_factor = to_float(incoming.get("speed_factor"), 1.0, 0.1)
                incoming["speed"] = round(1.0 / old_factor, 2)
            settings.update({key: incoming[key] for key in settings if key in incoming})

        metadata = data.get("metadata", {})
        screen = metadata.get("recorded_screen") if isinstance(metadata, dict) else None
        self.recorded_screen = screen if isinstance(screen, dict) else virtual_screen()
        self.actions = actions
        self.settings = settings
        self.path = source
        self.dirty = False

    def save(self, path: str | os.PathLike | None = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("尚未指定保存位置")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "2.0",
            "app": APP_NAME,
            "metadata": {"recorded_screen": self.recorded_screen},
            "settings": self.settings,
            "actions": self.actions,
        }
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
        self.path = target
        self.dirty = False
        return target


class MouseRecorder:
    """在独立消息线程中录制鼠标点击和滚轮。"""

    def __init__(self, on_done) -> None:
        self.on_done = on_done
        self.actions: list[dict] = []
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.mouse_hook = None
        self.keyboard_hook = None
        self.press_data: dict[str, tuple[float, int, int]] = {}
        self.started_at = 0.0
        self.last_action_end = 0.0
        self.error = ""

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.actions = []
        self.press_data.clear()
        self.stop_event.clear()
        self.ready_event.clear()
        self.started_at = time.perf_counter()
        self.last_action_end = self.started_at
        self.thread = threading.Thread(target=self._run, name="mouse-recorder", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)

    def _add(self, action: dict, trigger_time: float, end_time: float) -> None:
        # delay 只表示“上一动作结束 -> 本动作触发”的空闲时间。
        # 旧实现使用两次松开时刻相减，把本次按住时间重复算了一遍。
        action["delay"] = (
            0.0
            if not self.actions
            else round(max(0.0, trigger_time - self.last_action_end), 3)
        )
        action["enabled"] = True
        action["repeat"] = 1
        action["name"] = ""
        self.actions.append(action)
        self.last_action_end = end_time

    def _mouse_callback(self, code, message, pointer):
        if code >= 0:
            event = MSLLHOOKSTRUCT.from_address(pointer)
            if event.flags & LLMHF_INJECTED:
                return user32.CallNextHookEx(self.mouse_hook, code, message, pointer)
            now = time.perf_counter()
            downs = {
                WM_LBUTTONDOWN: "left",
                WM_RBUTTONDOWN: "right",
                WM_MBUTTONDOWN: "middle",
            }
            ups = {
                WM_LBUTTONUP: "left",
                WM_RBUTTONUP: "right",
                WM_MBUTTONUP: "middle",
            }
            if message in downs:
                self.press_data[downs[message]] = (now, event.pt.x, event.pt.y)
            elif message in ups:
                button = ups[message]
                pressed = self.press_data.pop(button, None)
                if pressed:
                    pressed_at, x, y = pressed
                    self._add(
                        {
                            "type": "click",
                            "button": button,
                            "x": x,
                            "y": y,
                            "hold_ms": max(1, round((now - pressed_at) * 1000)),
                            "jitter": 0,
                        },
                        pressed_at,
                        now,
                    )
            elif message == WM_MOUSEWHEEL:
                amount = ctypes.c_short((event.mouseData >> 16) & 0xFFFF).value
                self._add(
                    {
                        "type": "scroll",
                        "amount": amount,
                        "x": event.pt.x,
                        "y": event.pt.y,
                        "jitter": 0,
                    },
                    now,
                    now,
                )
        return user32.CallNextHookEx(self.mouse_hook, code, message, pointer)

    def _keyboard_callback(self, code, message, pointer):
        if code >= 0 and message in (WM_KEYDOWN, WM_SYSKEYDOWN):
            event = KBDLLHOOKSTRUCT.from_address(pointer)
            if event.vkCode in (VK_F8, VK_F9):
                self.stop_event.set()
                user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
                return 1
        return user32.CallNextHookEx(self.keyboard_hook, code, message, pointer)

    def _run(self) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()
        mouse_callback = HOOKPROC(self._mouse_callback)
        keyboard_callback = HOOKPROC(self._keyboard_callback)
        try:
            self.mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_callback, None, 0)
            self.keyboard_hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, keyboard_callback, None, 0
            )
            if not self.mouse_hook or not self.keyboard_hook:
                code = ctypes.get_last_error()
                raise OSError(code, "无法安装全局录制钩子")
            self.ready_event.set()
            message = MSG()
            while not self.stop_event.is_set():
                result = user32.GetMessageW(byref(message), None, 0, 0)
                if result <= 0:
                    break
                user32.TranslateMessage(byref(message))
                user32.DispatchMessageW(byref(message))
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.ready_event.set()
            if self.mouse_hook:
                user32.UnhookWindowsHookEx(self.mouse_hook)
                self.mouse_hook = None
            if self.keyboard_hook:
                user32.UnhookWindowsHookEx(self.keyboard_hook)
                self.keyboard_hook = None
            self.thread_id = 0
            self.on_done(copy.deepcopy(self.actions), self.error)


def send_mouse_input(flags: int, data: int = 0, x: int = 0, y: int = 0) -> None:
    item = INPUT(type=INPUT_MOUSE)
    item.mi = MOUSEINPUT(x, y, data & 0xFFFFFFFF, flags, 0, 0)
    if user32.SendInput(1, byref(item), ctypes.sizeof(INPUT)) != 1:
        raise OSError(ctypes.get_last_error(), "系统未接受鼠标输入")


def set_cursor_position(x: int, y: int) -> None:
    screen = virtual_screen()
    right = screen["x"] + screen["width"] - 1
    bottom = screen["y"] + screen["height"] - 1
    x = int(clamp(x, screen["x"], right))
    y = int(clamp(y, screen["y"], bottom))
    # DPI 感知开启后直接使用物理桌面坐标，避免绝对坐标归一化的舍入误差。
    if not user32.SetCursorPos(x, y):
        raise OSError(ctypes.get_last_error(), "无法移动鼠标")
    actual_x, actual_y = cursor_position()
    if (actual_x, actual_y) != (x, y):
        user32.SetCursorPos(x, y)


class PlaybackEngine:
    def __init__(self, event_callback) -> None:
        self.event_callback = event_callback
        self.thread: threading.Thread | None = None
        self.monitor_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.running = False
        self.paused = False
        self.actions: list[dict] = []
        self.settings: dict = {}
        self.recorded_screen: dict = {}
        self.pause_started_at = 0.0
        self.paused_total = 0.0

    def emit(self, kind: str, **payload) -> None:
        self.event_callback(kind, payload)

    def start(self, actions: list[dict], settings: dict, recorded_screen: dict) -> None:
        if self.running:
            return
        self.actions = copy.deepcopy([a for a in actions if a.get("enabled", True)])
        if not self.actions:
            raise ValueError("没有已启用的动作")
        self.settings = copy.deepcopy(settings)
        self.recorded_screen = copy.deepcopy(recorded_screen)
        self.stop_event.clear()
        self.pause_event.set()
        self.running = True
        self.paused = False
        self.pause_started_at = 0.0
        self.paused_total = 0.0
        self.thread = threading.Thread(target=self._run, name="playback", daemon=True)
        self.monitor_thread = threading.Thread(
            target=self._safety_monitor, name="safety-monitor", daemon=True
        )
        self.thread.start()
        self.monitor_thread.start()

    def stop(self, reason: str = "用户停止") -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.pause_event.set()
        self.emit("log", message=reason)

    def pause(self, reason: str = "已暂停") -> None:
        if self.running and not self.paused:
            self.paused = True
            self.pause_started_at = time.perf_counter()
            self.pause_event.clear()
            self.emit("state", running=True, paused=True)
            self.emit("log", message=reason)

    def resume(self) -> None:
        if self.running and self.paused:
            self.paused_total += max(0.0, time.perf_counter() - self.pause_started_at)
            self.pause_started_at = 0.0
            self.paused = False
            self.pause_event.set()
            self.emit("state", running=True, paused=False)
            self.emit("log", message="继续运行")

    def toggle_pause(self) -> None:
        self.resume() if self.paused else self.pause("F10：已暂停")

    def _active_now(self) -> float:
        now = time.perf_counter()
        current_pause = (
            now - self.pause_started_at
            if self.paused and self.pause_started_at
            else 0.0
        )
        return now - self.paused_total - current_pause

    def _wait_until(self, target: float, deadline: float | None = None) -> bool:
        while self._active_now() < target:
            if self.stop_event.is_set() or (deadline and self._active_now() >= deadline):
                return False
            self.pause_event.wait(0.05)
            if not self.pause_event.is_set():
                continue
            remaining = target - self._active_now()
            time.sleep(min(0.01, max(0.001, remaining)))
        return not self.stop_event.is_set()

    def _interruptible_wait(self, seconds: float, deadline: float | None = None) -> bool:
        return self._wait_until(self._active_now() + max(0.0, seconds), deadline)

    def _scale_point(self, x: int, y: int) -> tuple[int, int]:
        if not self.settings.get("scale_coordinates"):
            return x, y
        old = self.recorded_screen
        new = virtual_screen()
        try:
            rx = (x - int(old["x"])) / max(1, int(old["width"]) - 1)
            ry = (y - int(old["y"])) / max(1, int(old["height"]) - 1)
            return (
                round(new["x"] + rx * (new["width"] - 1)),
                round(new["y"] + ry * (new["height"] - 1)),
            )
        except (KeyError, TypeError, ValueError):
            return x, y

    def _target_point(self, action: dict) -> tuple[int, int]:
        region = action.get("region")
        if isinstance(region, dict) and region.get("width", 0) > 0 and region.get("height", 0) > 0:
            x = random.randint(region["x"], region["x"] + region["width"])
            y = random.randint(region["y"], region["y"] + region["height"])
        else:
            radius = max(0, int(action.get("jitter", 0)))
            angle = random.random() * math.tau
            distance = radius * math.sqrt(random.random())
            x = round(action["x"] + math.cos(angle) * distance)
            y = round(action["y"] + math.sin(angle) * distance)
        return self._scale_point(x, y)

    def _move(self, x: int, y: int, duration: float, deadline: float | None) -> bool:
        if not self.settings.get("smooth_move", True):
            set_cursor_position(x, y)
            return True
        start_x, start_y = cursor_position()
        duration = max(0.0, duration)
        if duration <= 0:
            set_cursor_position(x, y)
            return True
        distance = math.hypot(x - start_x, y - start_y)
        steps = max(3, min(80, round(duration * 1000 / 8), round(distance / 4) + 3))
        bend_x = random.uniform(-0.12, 0.12) * distance
        bend_y = random.uniform(-0.12, 0.12) * distance
        move_started = self._active_now()
        for index in range(1, steps + 1):
            if self.stop_event.is_set() or (deadline and self._active_now() >= deadline):
                return False
            self.pause_event.wait()
            t = index / steps
            eased = 3 * t * t - 2 * t * t * t
            px = start_x + (x - start_x) * eased + math.sin(math.pi * t) * bend_x
            py = start_y + (y - start_y) * eased + math.sin(math.pi * t) * bend_y
            set_cursor_position(round(px), round(py))
            if not self._wait_until(move_started + duration * index / steps, deadline):
                return False
        set_cursor_position(x, y)
        return True

    def _execute_action(
        self,
        action: dict,
        speed: float,
        deadline: float | None,
        cycle_start: float,
        timeline: float,
    ) -> tuple[bool, float]:
        # 锚定绝对时间轴，单次调度偏差不会继续累加到后续动作。
        timeline += action.get("delay", 0.0) / speed
        scheduled = cycle_start + timeline
        if action["type"] == "wait":
            if not self._wait_until(scheduled, deadline):
                return False, timeline
            timeline += action["duration"] / speed
            return self._wait_until(cycle_start + timeline, deadline), timeline

        repeat_total = action.get("repeat", 1)
        for repeat_index in range(repeat_total):
            x, y = self._target_point(action)
            scheduled = cycle_start + timeline
            move_duration = (
                max(0.0, self.settings.get("move_duration_ms", 120) / 1000 / speed)
                if self.settings.get("smooth_move", True)
                else 0.0
            )
            if not self._wait_until(max(cycle_start, scheduled - move_duration), deadline):
                return False, timeline
            available = max(0.0, scheduled - self._active_now())
            if not self._move(x, y, min(move_duration, available), deadline):
                return False, timeline
            if not self._wait_until(scheduled, deadline):
                return False, timeline
            if action["type"] == "scroll":
                send_mouse_input(MOUSEEVENTF_WHEEL, action["amount"])
            else:
                flags = {
                    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
                    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
                    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
                }[action["button"]]
                send_mouse_input(flags[0])
                timeline += max(0.01, action["hold_ms"] / 1000 / speed)
                if not self._wait_until(cycle_start + timeline, deadline):
                    send_mouse_input(flags[1])
                    return False, timeline
                send_mouse_input(flags[1])
            if repeat_index + 1 < repeat_total:
                timeline += 0.12 / speed
        return True, timeline

    def _run(self) -> None:
        error = ""
        completed = 0
        try:
            self.emit("state", running=True, paused=False)
            speed = clamp(to_float(self.settings.get("speed"), 1.0), 0.1, 10.0)
            start_delay = clamp(to_float(self.settings.get("start_delay"), 3.0), 0.0, 3600.0)
            if start_delay:
                self.emit("log", message=f"{start_delay:g} 秒后开始；F9 急停，F10 暂停")
                if not self._interruptible_wait(start_delay):
                    return

            mode = self.settings.get("run_mode", "cycles")
            max_cycles = max(1, to_int(self.settings.get("cycles"), 1, 1))
            run_started = self._active_now()
            deadline = None
            if mode == "duration":
                duration = max(0.01, to_float(self.settings.get("duration_minutes"), 10.0))
                deadline = run_started + duration * 60
                max_cycles = sys.maxsize
            elif mode == "infinite":
                max_cycles = sys.maxsize

            self.emit("log", message=f"开始执行，共 {len(self.actions)} 个已启用动作")
            cycle = 0
            while cycle < max_cycles and not self.stop_event.is_set():
                if deadline and self._active_now() >= deadline:
                    break
                cycle += 1
                first = self.actions[0]
                configured_move = (
                    max(0.0, self.settings.get("move_duration_ms", 120) / 1000 / speed)
                    if self.settings.get("smooth_move", True)
                    else 0.0
                )
                lead_in = (
                    max(0.0, configured_move - first.get("delay", 0.0) / speed)
                    if first["type"] in {"click", "scroll"}
                    else 0.0
                )
                cycle_start = self._active_now() + lead_in
                timeline = 0.0
                for index, action in enumerate(self.actions, 1):
                    ok, timeline = self._execute_action(
                        action, speed, deadline, cycle_start, timeline
                    )
                    if not ok:
                        break
                    completed += 1
                    self.emit(
                        "progress",
                        cycle=cycle,
                        action=index,
                        total=len(self.actions),
                        completed=completed,
                    )
                else:
                    if cycle < max_cycles:
                        cycle_delay = max(0.0, to_float(self.settings.get("cycle_delay"), 1.0))
                        if not self._interruptible_wait(cycle_delay / speed, deadline):
                            break
                    continue
                break
        except Exception as exc:
            error = str(exc)
            self.emit("log", message=f"运行出错：{error}")
        finally:
            stopped = self.stop_event.is_set()
            self.running = False
            self.paused = False
            self.stop_event.set()
            self.pause_event.set()
            if not error:
                self.emit("log", message="已停止" if stopped else "任务已完成")
            self.emit("state", running=False, paused=False)

    def _safety_monitor(self) -> None:
        f9_was_down = False
        f10_was_down = False
        corner_since = 0.0
        while self.running and not self.stop_event.is_set():
            f9_down = bool(user32.GetAsyncKeyState(VK_F9) & 0x8000)
            f10_down = bool(user32.GetAsyncKeyState(VK_F10) & 0x8000)
            if f9_down and not f9_was_down:
                self.stop("F9：紧急停止")
                break
            if f10_down and not f10_was_down:
                self.toggle_pause()
            f9_was_down = f9_down
            f10_was_down = f10_down

            if self.settings.get("corner_pause") and not self.paused:
                screen = virtual_screen()
                x, y = cursor_position()
                in_corner = x <= screen["x"] + 2 and y <= screen["y"] + 2
                if in_corner:
                    corner_since = corner_since or time.perf_counter()
                    if time.perf_counter() - corner_since >= 0.8:
                        self.pause("鼠标停在左上角：已安全暂停")
                else:
                    corner_since = 0.0
            time.sleep(0.04)


TYPE_LABELS = {"click": "鼠标点击", "scroll": "滚轮", "wait": "等待"}
BUTTON_LABELS = {"left": "左键", "right": "右键", "middle": "中键"}


class ActionDialog(tk.Toplevel):
    def __init__(self, parent, action: dict | None = None, preset_point=None) -> None:
        super().__init__(parent)
        self.title("编辑动作" if action else "添加动作")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.result = None
        source = action or {
            "enabled": True,
            "name": "",
            "type": "click",
            "button": "left",
            "x": preset_point[0] if preset_point else 0,
            "y": preset_point[1] if preset_point else 0,
            "hold_ms": 60,
            "delay": 0.3,
            "jitter": 0,
            "repeat": 1,
        }
        self.vars = {
            "enabled": tk.BooleanVar(value=source.get("enabled", True)),
            "name": tk.StringVar(value=source.get("name", "")),
            "type": tk.StringVar(value=TYPE_LABELS.get(source.get("type"), "鼠标点击")),
            "button": tk.StringVar(value=BUTTON_LABELS.get(source.get("button"), "左键")),
            "x": tk.StringVar(value=source.get("x", 0)),
            "y": tk.StringVar(value=source.get("y", 0)),
            "hold_ms": tk.StringVar(value=source.get("hold_ms", 60)),
            "delay": tk.StringVar(value=source.get("delay", 0.3)),
            "jitter": tk.StringVar(value=source.get("jitter", 0)),
            "repeat": tk.StringVar(value=source.get("repeat", 1)),
            "amount": tk.StringVar(value=source.get("amount", -120)),
            "duration": tk.StringVar(value=source.get("duration", 1.0)),
        }
        self._build()
        self._type_changed()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self._save())
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _row(self, frame, row, label, variable, width=16):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=6)
        entry = ttk.Entry(frame, textvariable=variable, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=6)
        return entry

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=20)
        outer.grid(sticky="nsew")
        outer.columnconfigure(1, weight=1)
        ttk.Checkbutton(outer, text="启用此动作", variable=self.vars["enabled"]).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        self._row(outer, 1, "名称（可选）", self.vars["name"], 24)
        ttk.Label(outer, text="动作类型").grid(row=2, column=0, sticky="w", pady=6)
        kind = ttk.Combobox(
            outer,
            textvariable=self.vars["type"],
            values=list(TYPE_LABELS.values()),
            state="readonly",
            width=21,
        )
        kind.grid(row=2, column=1, sticky="ew", pady=6)
        kind.bind("<<ComboboxSelected>>", lambda _event: self._type_changed())
        self.delay_entry = self._row(outer, 3, "执行前等待（秒）", self.vars["delay"])

        self.dynamic = ttk.LabelFrame(outer, text="动作参数", padding=12)
        self.dynamic.grid(row=4, column=0, columnspan=2, sticky="ew", pady=10)
        self.dynamic.columnconfigure(1, weight=1)

        buttons = ttk.Frame(outer)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=6)
        ttk.Button(buttons, text="保存", style="Accent.TButton", command=self._save).pack(
            side="left"
        )

    def _type_changed(self) -> None:
        for child in self.dynamic.winfo_children():
            child.destroy()
        kind = {value: key for key, value in TYPE_LABELS.items()}[self.vars["type"].get()]
        if kind == "wait":
            self._row(self.dynamic, 0, "等待时长（秒）", self.vars["duration"])
            return
        self._row(self.dynamic, 0, "X 坐标", self.vars["x"])
        self._row(self.dynamic, 1, "Y 坐标", self.vars["y"])
        ttk.Button(self.dynamic, text="读取当前鼠标位置", command=self._capture_now).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=6
        )
        self._row(self.dynamic, 3, "随机范围半径（像素）", self.vars["jitter"])
        self._row(self.dynamic, 4, "重复次数", self.vars["repeat"])
        if kind == "click":
            ttk.Label(self.dynamic, text="鼠标按键").grid(row=5, column=0, sticky="w", pady=6)
            ttk.Combobox(
                self.dynamic,
                textvariable=self.vars["button"],
                values=list(BUTTON_LABELS.values()),
                state="readonly",
            ).grid(row=5, column=1, sticky="ew", pady=6)
            self._row(self.dynamic, 6, "按住时间（毫秒）", self.vars["hold_ms"])
        else:
            self._row(self.dynamic, 5, "滚动量（上正下负）", self.vars["amount"])

    def _capture_now(self) -> None:
        try:
            x, y = cursor_position()
            self.vars["x"].set(x)
            self.vars["y"].set(y)
        except Exception as exc:
            messagebox.showerror("读取失败", str(exc), parent=self)

    def _save(self) -> None:
        try:
            kind = {value: key for key, value in TYPE_LABELS.items()}[self.vars["type"].get()]
            raw = {
                "enabled": self.vars["enabled"].get(),
                "name": self.vars["name"].get(),
                "type": kind,
                "delay": float(self.vars["delay"].get()),
                "repeat": int(self.vars["repeat"].get()),
            }
            if kind in {"click", "scroll"}:
                raw.update(
                    x=int(self.vars["x"].get()),
                    y=int(self.vars["y"].get()),
                    jitter=int(self.vars["jitter"].get()),
                )
            if kind == "click":
                raw["button"] = {
                    value: key for key, value in BUTTON_LABELS.items()
                }[self.vars["button"].get()]
                raw["hold_ms"] = int(self.vars["hold_ms"].get())
            elif kind == "scroll":
                raw["amount"] = int(self.vars["amount"].get())
            else:
                raw["duration"] = float(self.vars["duration"].get())
            if raw["delay"] < 0 or raw["repeat"] < 1:
                raise ValueError
            if raw.get("jitter", 0) < 0 or raw.get("hold_ms", 1) < 1:
                raise ValueError
            if raw.get("duration", 1) <= 0:
                raise ValueError
            self.result = normalize_action(raw)
            self.destroy()
        except (ValueError, KeyError):
            messagebox.showerror("参数不正确", "请检查数值；等待不能为负，次数至少为 1。", parent=self)


class AutomationApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.project = Project()
        self.recorder: MouseRecorder | None = None
        self.engine = PlaybackEngine(self._engine_event)
        self.event_queue: list[tuple[str, dict]] = []
        self.event_lock = threading.Lock()
        self.recording = False
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1120x730")
        self.minsize(920, 600)
        self.configure(bg="#f5f7fb")
        self._configure_style()
        self._build_ui()
        self._load_last_window_state()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._drain_events)
        self.bind("<Control-o>", lambda _event: self.open_project())
        self.bind("<Control-s>", lambda _event: self.save_project())
        self.bind("<Control-n>", lambda _event: self.new_project())
        self.bind("<Delete>", lambda _event: self.delete_selected())

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TFrame", background="#f5f7fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Card.TLabel", background="#ffffff", foreground="#1f2937")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"), background="#f5f7fb")
        style.configure("Sub.TLabel", foreground="#667085", background="#f5f7fb")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(15, 8))
        style.map("Accent.TButton", foreground=[("disabled", "#94a3b8")])
        style.configure("Big.TButton", font=("Microsoft YaHei UI", 12, "bold"), padding=(20, 12))
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TNotebook", background="#f5f7fb", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(22, 10), font=("Microsoft YaHei UI", 10))

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(24, 16, 24, 6))
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="录制、整理、运行，一处完成",
            style="Sub.TLabel",
        ).pack(side="left", padx=16, pady=(8, 0))
        self.file_label = ttk.Label(header, text="未保存项目", style="Sub.TLabel")
        self.file_label.pack(side="right", pady=(8, 0))

        toolbar = ttk.Frame(self, padding=(24, 4, 24, 10))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="新建", command=self.new_project).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="打开", command=self.open_project).pack(side="left", padx=6)
        ttk.Button(toolbar, text="保存", command=self.save_project).pack(side="left", padx=6)
        ttk.Button(toolbar, text="另存为", command=lambda: self.save_project(True)).pack(side="left", padx=6)
        ttk.Label(toolbar, text="快捷键：F8 结束录制　F9 急停　F10 暂停/继续", style="Sub.TLabel").pack(side="right")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        self.actions_page = ttk.Frame(self.notebook, padding=12)
        self.run_page = ttk.Frame(self.notebook, padding=12)
        self.help_page = ttk.Frame(self.notebook, padding=18)
        self.notebook.add(self.actions_page, text="1  动作")
        self.notebook.add(self.run_page, text="2  运行")
        self.notebook.add(self.help_page, text="使用说明")
        self._build_actions_page()
        self._build_run_page()
        self._build_help_page()

        status = ttk.Frame(self, padding=(24, 0, 24, 12))
        status.pack(fill="x")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status, textvariable=self.status_var, style="Sub.TLabel").pack(side="left")
        self.count_var = tk.StringVar(value="0 个动作")
        ttk.Label(status, textvariable=self.count_var, style="Sub.TLabel").pack(side="right")

    def _build_actions_page(self) -> None:
        top = ttk.Frame(self.actions_page)
        top.pack(fill="x", pady=(0, 10))
        self.record_button = ttk.Button(
            top, text="●  录制鼠标动作", style="Accent.TButton", command=self.start_recording
        )
        self.record_button.pack(side="left")
        self.stop_record_button = ttk.Button(
            top, text="■  结束录制 (F8)", command=self.stop_recording, state="disabled"
        )
        self.stop_record_button.pack(side="left", padx=8)
        ttk.Button(top, text="读取鼠标位置并添加", command=self.capture_and_add).pack(
            side="left", padx=8
        )
        ttk.Label(
            top,
            text="录制会捕获左/右/中键点击与滚轮",
            style="Sub.TLabel",
        ).pack(side="right")

        table_card = ttk.Frame(self.actions_page, style="Card.TFrame", padding=10)
        table_card.pack(fill="both", expand=True)
        columns = ("index", "enabled", "name", "type", "position", "delay", "detail")
        self.tree = ttk.Treeview(table_card, columns=columns, show="headings", selectmode="extended")
        headings = {
            "index": "#",
            "enabled": "启用",
            "name": "名称",
            "type": "类型",
            "position": "位置",
            "delay": "前置等待",
            "detail": "参数",
        }
        widths = {"index": 45, "enabled": 55, "name": 190, "type": 90, "position": 140, "delay": 95, "detail": 230}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="center" if column != "name" else "w")
        scroll = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.edit_selected())
        self.tree.bind("<space>", lambda _event: self.toggle_selected())

        controls = ttk.Frame(self.actions_page, padding=(0, 10, 0, 0))
        controls.pack(fill="x")
        ttk.Button(controls, text="＋ 添加", command=self.add_action).pack(side="left")
        ttk.Button(controls, text="编辑", command=self.edit_selected).pack(side="left", padx=6)
        ttk.Button(controls, text="复制", command=self.duplicate_selected).pack(side="left", padx=6)
        ttk.Button(controls, text="删除", command=self.delete_selected).pack(side="left", padx=6)
        ttk.Button(controls, text="↑ 上移", command=lambda: self.move_selected(-1)).pack(side="right", padx=6)
        ttk.Button(controls, text="↓ 下移", command=lambda: self.move_selected(1)).pack(side="right")

    def _build_run_page(self) -> None:
        content = ttk.Frame(self.run_page)
        content.pack(fill="both", expand=True)
        left = ttk.Frame(content, style="Card.TFrame", padding=18)
        left.pack(side="left", fill="y", padx=(0, 12))
        right = ttk.Frame(content, style="Card.TFrame", padding=18)
        right.pack(side="left", fill="both", expand=True)

        self.run_vars = {
            "run_mode": tk.StringVar(value="按次数"),
            "cycles": tk.StringVar(value="1"),
            "duration_minutes": tk.StringVar(value="10"),
            "speed": tk.StringVar(value="1.0"),
            "start_delay": tk.StringVar(value="3"),
            "cycle_delay": tk.StringVar(value="1"),
            "smooth_move": tk.BooleanVar(value=True),
            "move_duration_ms": tk.StringVar(value="120"),
            "scale_coordinates": tk.BooleanVar(value=False),
            "corner_pause": tk.BooleanVar(value=True),
        }
        ttk.Label(left, text="运行设置", font=("Microsoft YaHei UI", 12, "bold"), style="Card.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        ttk.Label(left, text="运行方式", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=7)
        mode = ttk.Combobox(left, textvariable=self.run_vars["run_mode"], values=["按次数", "按时长", "持续运行"], state="readonly", width=15)
        mode.grid(row=1, column=1, pady=7)
        mode.bind("<<ComboboxSelected>>", lambda _event: self._mode_changed())
        ttk.Label(left, text="循环次数", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=7)
        self.cycles_entry = ttk.Entry(left, textvariable=self.run_vars["cycles"], width=17)
        self.cycles_entry.grid(row=2, column=1, pady=7)
        ttk.Label(left, text="运行分钟", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=7)
        self.duration_entry = ttk.Entry(left, textvariable=self.run_vars["duration_minutes"], width=17)
        self.duration_entry.grid(row=3, column=1, pady=7)
        ttk.Label(left, text="播放速度", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=7)
        ttk.Combobox(left, textvariable=self.run_vars["speed"], values=["0.25", "0.5", "0.75", "1.0", "1.5", "2.0", "3.0", "5.0"], width=14).grid(row=4, column=1, pady=7)
        ttk.Label(left, text="开始倒计时（秒）", style="Card.TLabel").grid(row=5, column=0, sticky="w", pady=7)
        ttk.Entry(left, textvariable=self.run_vars["start_delay"], width=17).grid(row=5, column=1, pady=7)
        ttk.Label(left, text="每轮间隔（秒）", style="Card.TLabel").grid(row=6, column=0, sticky="w", pady=7)
        ttk.Entry(left, textvariable=self.run_vars["cycle_delay"], width=17).grid(row=6, column=1, pady=7)
        ttk.Separator(left).grid(row=7, column=0, columnspan=2, sticky="ew", pady=12)
        ttk.Checkbutton(left, text="平滑移动鼠标", variable=self.run_vars["smooth_move"]).grid(row=8, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Label(left, text="移动耗时（毫秒）", style="Card.TLabel").grid(row=9, column=0, sticky="w", pady=7)
        ttk.Entry(left, textvariable=self.run_vars["move_duration_ms"], width=17).grid(row=9, column=1, pady=7)
        ttk.Checkbutton(left, text="按当前屏幕缩放坐标", variable=self.run_vars["scale_coordinates"]).grid(row=10, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Checkbutton(left, text="鼠标停左上角时暂停", variable=self.run_vars["corner_pause"]).grid(row=11, column=0, columnspan=2, sticky="w", pady=5)
        self._mode_changed()

        ttk.Label(right, text="运行控制", font=("Microsoft YaHei UI", 12, "bold"), style="Card.TLabel").pack(anchor="w")
        controls = ttk.Frame(right, style="Card.TFrame")
        controls.pack(fill="x", pady=14)
        self.start_button = ttk.Button(controls, text="▶  开始运行", style="Big.TButton", command=self.start_playback)
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(controls, text="⏸  暂停", command=self.pause_playback, state="disabled")
        self.pause_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(controls, text="■  停止", command=lambda: self.engine.stop(), state="disabled")
        self.stop_button.pack(side="left")
        self.run_status = tk.StringVar(value="等待开始")
        ttk.Label(right, textvariable=self.run_status, style="Card.TLabel", font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=(4, 6))
        self.progress = ttk.Progressbar(right, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 16))
        ttk.Label(right, text="运行记录", style="Card.TLabel").pack(anchor="w", pady=(0, 6))
        log_frame = ttk.Frame(right, style="Card.TFrame")
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=16, state="disabled", relief="flat", background="#f8fafc", foreground="#334155", font=("Microsoft YaHei UI", 9), padx=10, pady=10)
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        ttk.Button(right, text="清空记录", command=self.clear_log).pack(anchor="e", pady=(8, 0))

    def _build_help_page(self) -> None:
        card = ttk.Frame(self.help_page, style="Card.TFrame", padding=28)
        card.pack(fill="both", expand=True)
        text = (
            "快速开始\n\n"
            "1. 在“动作”页点击“录制鼠标动作”，窗口会最小化，2 秒后开始。\n"
            "2. 完成操作后按 F8；窗口会自动恢复，动作可双击编辑。\n"
            "3. 在“运行”页设置次数、时长和速度，再点击“开始运行”。\n\n"
            "安全控制\n\n"
            "• F9：任何时候紧急停止运行。\n"
            "• F10：暂停或继续。\n"
            "• 启用安全选项后，将鼠标停在虚拟桌面左上角约 1 秒即可暂停。\n\n"
            "项目文件\n\n"
            "项目使用 JSON 格式保存。可以打开旧版录制文件和 stage2_annotated.json；"
            "旧版矩形区域会继续生效。切换分辨率或显示器布局后，可启用“按当前屏幕缩放坐标”。"
        )
        ttk.Label(card, text=text, style="Card.TLabel", justify="left", wraplength=780, font=("Microsoft YaHei UI", 11)).pack(anchor="nw")

    def _mode_changed(self) -> None:
        if not hasattr(self, "cycles_entry"):
            return
        mode = self.run_vars["run_mode"].get()
        self.cycles_entry.configure(state="normal" if mode == "按次数" else "disabled")
        self.duration_entry.configure(state="normal" if mode == "按时长" else "disabled")

    def _settings_from_ui(self) -> dict:
        mode = {"按次数": "cycles", "按时长": "duration", "持续运行": "infinite"}[self.run_vars["run_mode"].get()]
        settings = {
            "run_mode": mode,
            "cycles": int(self.run_vars["cycles"].get()),
            "duration_minutes": float(self.run_vars["duration_minutes"].get()),
            "speed": float(self.run_vars["speed"].get()),
            "start_delay": float(self.run_vars["start_delay"].get()),
            "cycle_delay": float(self.run_vars["cycle_delay"].get()),
            "smooth_move": self.run_vars["smooth_move"].get(),
            "move_duration_ms": int(self.run_vars["move_duration_ms"].get()),
            "scale_coordinates": self.run_vars["scale_coordinates"].get(),
            "corner_pause": self.run_vars["corner_pause"].get(),
        }
        if settings["cycles"] < 1 or settings["duration_minutes"] <= 0:
            raise ValueError("次数至少为 1，运行分钟必须大于 0")
        if not 0.1 <= settings["speed"] <= 10:
            raise ValueError("播放速度应在 0.1～10 之间")
        if settings["start_delay"] < 0 or settings["cycle_delay"] < 0:
            raise ValueError("倒计时和轮次间隔不能为负")
        if not 0 <= settings["move_duration_ms"] <= 60000:
            raise ValueError("移动耗时应在 0～60000 毫秒之间")
        return settings

    def _settings_to_ui(self) -> None:
        settings = self.project.settings
        self.run_vars["run_mode"].set({"cycles": "按次数", "duration": "按时长", "infinite": "持续运行"}.get(settings.get("run_mode"), "按次数"))
        for key in ("cycles", "duration_minutes", "speed", "start_delay", "cycle_delay", "move_duration_ms"):
            self.run_vars[key].set(str(settings.get(key, DEFAULT_SETTINGS[key])))
        for key in ("smooth_move", "scale_coordinates", "corner_pause"):
            self.run_vars[key].set(bool(settings.get(key, DEFAULT_SETTINGS[key])))
        self._mode_changed()

    def _mark_dirty(self) -> None:
        self.project.dirty = True
        self._update_title()

    def _update_title(self) -> None:
        name = self.project.path.name if self.project.path else "未保存项目"
        marker = " *" if self.project.dirty else ""
        self.title(f"{APP_NAME} {APP_VERSION} — {name}{marker}")
        self.file_label.configure(text=f"{name}{marker}")
        enabled = sum(1 for action in self.project.actions if action.get("enabled", True))
        self.count_var.set(f"{len(self.project.actions)} 个动作 · {enabled} 个启用")

    def refresh_tree(self, selection: list[int] | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, action in enumerate(self.project.actions):
            kind = action["type"]
            position = "—" if kind == "wait" else f"{action['x']}, {action['y']}"
            if kind == "click":
                detail = f"{BUTTON_LABELS[action['button']]} · {action['hold_ms']} ms · ×{action['repeat']}"
            elif kind == "scroll":
                detail = f"滚动 {action['amount']} · ×{action['repeat']}"
            else:
                detail = f"等待 {action['duration']:g} 秒"
            if action.get("jitter", 0):
                detail += f" · ±{action['jitter']} px"
            self.tree.insert("", "end", iid=str(index), values=(index + 1, "✓" if action.get("enabled", True) else "—", action.get("name") or "未命名", TYPE_LABELS[kind], position, f"{action.get('delay', 0):g} 秒", detail))
        if selection:
            valid = [str(i) for i in selection if 0 <= i < len(self.project.actions)]
            self.tree.selection_set(valid)
            if valid:
                self.tree.see(valid[0])
        self._update_title()

    def selected_indices(self) -> list[int]:
        return sorted(int(item) for item in self.tree.selection())

    def add_action(self, preset_point=None) -> None:
        dialog = ActionDialog(self, preset_point=preset_point)
        self.wait_window(dialog)
        if dialog.result:
            self.project.actions.append(dialog.result)
            self._mark_dirty()
            self.refresh_tree([len(self.project.actions) - 1])

    def edit_selected(self) -> None:
        selected = self.selected_indices()
        if len(selected) != 1:
            self.status_var.set("请选择一个动作进行编辑")
            return
        index = selected[0]
        dialog = ActionDialog(self, self.project.actions[index])
        self.wait_window(dialog)
        if dialog.result:
            self.project.actions[index] = dialog.result
            self._mark_dirty()
            self.refresh_tree([index])

    def duplicate_selected(self) -> None:
        selected = self.selected_indices()
        if not selected:
            return
        insert_at = selected[-1] + 1
        copies = [copy.deepcopy(self.project.actions[index]) for index in selected]
        for offset, action in enumerate(copies):
            action["name"] = f"{action.get('name') or '动作'} - 副本"
            self.project.actions.insert(insert_at + offset, action)
        self._mark_dirty()
        self.refresh_tree(list(range(insert_at, insert_at + len(copies))))

    def delete_selected(self) -> None:
        selected = self.selected_indices()
        if not selected:
            return
        for index in reversed(selected):
            self.project.actions.pop(index)
        self._mark_dirty()
        self.refresh_tree([min(selected[0], len(self.project.actions) - 1)])

    def toggle_selected(self) -> None:
        selected = self.selected_indices()
        if not selected:
            return
        enable = not all(self.project.actions[index].get("enabled", True) for index in selected)
        for index in selected:
            self.project.actions[index]["enabled"] = enable
        self._mark_dirty()
        self.refresh_tree(selected)

    def move_selected(self, direction: int) -> None:
        selected = self.selected_indices()
        if len(selected) != 1:
            self.status_var.set("请选择一个动作进行移动")
            return
        old = selected[0]
        new = old + direction
        if not 0 <= new < len(self.project.actions):
            return
        self.project.actions[old], self.project.actions[new] = self.project.actions[new], self.project.actions[old]
        self._mark_dirty()
        self.refresh_tree([new])

    def capture_and_add(self) -> None:
        self.status_var.set("请把鼠标移到目标位置，1.5 秒后自动读取")
        self.withdraw()

        def capture():
            try:
                point = cursor_position()
            except Exception as exc:
                point = None
                messagebox.showerror("读取失败", str(exc))
            self.deiconify()
            self.lift()
            if point:
                self.add_action(point)

        self.after(1500, capture)

    def start_recording(self) -> None:
        if self.engine.running or self.recording:
            return
        self.recording = True
        self.status_var.set("窗口最小化后 2 秒开始录制，按 F8 结束")
        self.record_button.configure(state="disabled")
        self.stop_record_button.configure(state="normal")
        self.iconify()

        def begin():
            if not self.recording:
                return
            self.recorder = MouseRecorder(self._recording_finished_from_thread)
            self.recorder.start()
            self.status_var.set("录制中…按 F8 结束")

        self.after(2000, begin)

    def stop_recording(self) -> None:
        if not self.recording:
            return
        self.status_var.set("正在结束录制…")
        if self.recorder:
            self.recorder.stop()
        else:
            self.recording = False
            self.record_button.configure(state="normal")
            self.stop_record_button.configure(state="disabled")
            self.deiconify()

    def _recording_finished_from_thread(self, actions, error) -> None:
        with self.event_lock:
            self.event_queue.append(("recording_done", {"actions": actions, "error": error}))

    def _finish_recording_ui(self, actions: list[dict], error: str) -> None:
        self.recording = False
        self.recorder = None
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()
        self.record_button.configure(state="normal")
        self.stop_record_button.configure(state="disabled")
        if error:
            messagebox.showerror("录制失败", f"{error}\n\n可尝试以管理员身份运行本软件。")
            self.status_var.set("录制失败")
            return
        if actions:
            start = len(self.project.actions)
            self.project.actions.extend(actions)
            self.project.recorded_screen = virtual_screen()
            self._mark_dirty()
            self.refresh_tree(list(range(start, len(self.project.actions))))
            self.status_var.set(f"录制完成，新增 {len(actions)} 个动作")
        else:
            self.status_var.set("录制结束，没有捕获到动作")

    def _confirm_discard(self) -> bool:
        if not self.project.dirty:
            return True
        choice = messagebox.askyesnocancel("项目尚未保存", "是否先保存当前修改？")
        if choice is None:
            return False
        if choice:
            return self.save_project()
        return True

    def new_project(self) -> None:
        if self.engine.running or self.recording:
            messagebox.showwarning("暂时无法新建", "请先停止录制或运行。")
            return
        if not self._confirm_discard():
            return
        self.project.clear()
        self._settings_to_ui()
        self.refresh_tree()
        self.status_var.set("已新建项目")

    def open_project(self) -> None:
        if self.engine.running or self.recording:
            messagebox.showwarning("暂时无法打开", "请先停止录制或运行。")
            return
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(title="打开动作项目", filetypes=[("动作项目", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            self.project.load(path)
            self._settings_to_ui()
            self.refresh_tree()
            self.status_var.set(f"已打开：{Path(path).name}")
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def save_project(self, save_as: bool = False) -> bool:
        try:
            self.project.settings = self._settings_from_ui()
        except ValueError as exc:
            messagebox.showerror("设置有误", str(exc))
            self.notebook.select(self.run_page)
            return False
        path = self.project.path
        if save_as or path is None:
            chosen = filedialog.asksaveasfilename(title="保存动作项目", defaultextension=PROJECT_EXT, filetypes=[("动作项目", "*.json")])
            if not chosen:
                return False
            path = Path(chosen)
        try:
            saved = self.project.save(path)
            self._update_title()
            self.status_var.set(f"已保存：{saved.name}")
            return True
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return False

    def start_playback(self) -> None:
        if self.recording:
            return
        try:
            settings = self._settings_from_ui()
            self.project.settings = settings
            self.engine.start(self.project.actions, settings, self.project.recorded_screen)
            self._mark_dirty()
            self.progress["value"] = 0
        except (ValueError, OSError) as exc:
            messagebox.showerror("无法开始", str(exc))

    def pause_playback(self) -> None:
        if self.engine.paused:
            self.engine.resume()
        else:
            self.engine.pause()

    def _engine_event(self, kind: str, payload: dict) -> None:
        with self.event_lock:
            self.event_queue.append((kind, payload))

    def _drain_events(self) -> None:
        with self.event_lock:
            events, self.event_queue = self.event_queue, []
        for kind, payload in events:
            if kind == "recording_done":
                self._finish_recording_ui(payload["actions"], payload["error"])
            elif kind == "log":
                self.log(payload["message"])
            elif kind == "state":
                self._set_run_state(payload["running"], payload["paused"])
            elif kind == "progress":
                total = max(1, payload["total"])
                self.progress["value"] = payload["action"] / total * 100
                self.run_status.set(f"第 {payload['cycle']} 轮 · 动作 {payload['action']}/{total}")
        self.after(50, self._drain_events)

    def _set_run_state(self, running: bool, paused: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.pause_button.configure(state="normal" if running else "disabled", text="▶  继续" if paused else "⏸  暂停")
        self.run_status.set("已暂停" if paused else ("运行中" if running else "等待开始"))
        self.record_button.configure(state="disabled" if running else "normal")

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _load_last_window_state(self) -> None:
        config = app_data_dir() / "window.json"
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            geometry = data.get("geometry", "")
            if isinstance(geometry, str) and "x" in geometry:
                self.geometry(geometry)
        except Exception:
            pass

    def _save_window_state(self) -> None:
        try:
            (app_data_dir() / "window.json").write_text(
                json.dumps({"geometry": self.geometry()}), encoding="utf-8"
            )
        except Exception:
            pass

    def _on_close(self) -> None:
        if self.recording:
            if not messagebox.askyesno("结束录制", "正在录制，确定退出吗？"):
                return
            self.stop_recording()
        if self.engine.running:
            if not messagebox.askyesno("停止运行", "任务仍在运行，确定退出吗？"):
                return
            self.engine.stop("程序退出")
        if not self._confirm_discard():
            return
        self._save_window_state()
        try:
            winmm.timeEndPeriod(1)
        except Exception:
            pass
        self.destroy()


def main() -> None:
    enable_dpi_awareness()
    try:
        winmm.timeBeginPeriod(1)
    except Exception:
        pass
    app = AutomationApp()
    app.mainloop()


if __name__ == "__main__":
    main()
