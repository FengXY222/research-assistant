"""Small Windows-only global shortcut bridge for the desktop widget.

``RegisterHotKey`` handles normal combinations such as ``Ctrl+Alt+J``.  A
passive low-level keyboard hook handles the two-tap defaults (Double-Tab and
Double-Space) without swallowing the original key press.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from time import monotonic
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication


WM_HOTKEY = 0x0312
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
DEFAULT_HOTKEY_ID = 0x4A51
WH_KEYBOARD_LL = 13
HC_ACTION = 0
VK_TAB = 0x09
VK_SPACE = 0x20
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000


@dataclass(frozen=True)
class HotkeyStatus:
    active: bool
    message: str


if sys.platform == "win32":
    ULONG_PTR = ctypes.c_size_t

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("message", ctypes.c_uint),
            ("wParam", ULONG_PTR),
            ("lParam", ctypes.c_ssize_t),
            ("time", ctypes.c_uint),
            ("pt", _POINT),
            ("lPrivate", ctypes.c_uint),
        ]

    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", ctypes.c_uint),
            ("scanCode", ctypes.c_uint),
            ("flags", ctypes.c_uint),
            ("time", ctypes.c_uint),
            ("dwExtraInfo", ULONG_PTR),
        ]

    _LRESULT = ctypes.c_ssize_t
    _WPARAM = ctypes.c_size_t
    _LPARAM = ctypes.c_ssize_t
    _LowLevelKeyboardProc = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, _WPARAM, _LPARAM)


def _virtual_key(combination) -> tuple[int, int] | None:
    """Translate one Qt key sequence to the constrained Windows hotkey API."""
    modifiers = combination.keyboardModifiers()
    mod = 0
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        mod |= MOD_CONTROL
    if modifiers & Qt.KeyboardModifier.AltModifier:
        mod |= MOD_ALT
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        mod |= MOD_SHIFT
    if modifiers & Qt.KeyboardModifier.MetaModifier:
        mod |= MOD_WIN
    if not mod:
        return None

    key = int(combination.key())
    if int(Qt.Key.Key_A) <= key <= int(Qt.Key.Key_Z):
        return mod, ord(chr(key))
    if int(Qt.Key.Key_0) <= key <= int(Qt.Key.Key_9):
        return mod, ord(chr(key))
    if int(Qt.Key.Key_F1) <= key <= int(Qt.Key.Key_F24):
        return mod, 0x70 + key - int(Qt.Key.Key_F1)
    known = {
        int(Qt.Key.Key_Tab): VK_TAB,
        int(Qt.Key.Key_Backspace): 0x08,
        int(Qt.Key.Key_Return): 0x0D,
        int(Qt.Key.Key_Enter): 0x0D,
        int(Qt.Key.Key_Escape): 0x1B,
        int(Qt.Key.Key_Space): VK_SPACE,
        int(Qt.Key.Key_PageUp): 0x21,
        int(Qt.Key.Key_PageDown): 0x22,
        int(Qt.Key.Key_End): 0x23,
        int(Qt.Key.Key_Home): 0x24,
        int(Qt.Key.Key_Left): 0x25,
        int(Qt.Key.Key_Up): 0x26,
        int(Qt.Key.Key_Right): 0x27,
        int(Qt.Key.Key_Down): 0x28,
        int(Qt.Key.Key_Insert): 0x2D,
        int(Qt.Key.Key_Delete): 0x2E,
    }
    vk = known.get(key)
    return (mod, vk) if vk else None


def normalize_shortcut_config(
    shortcut: dict | None,
    double_tap_mode: str,
    default_sequence: str,
) -> dict[str, str]:
    """Normalize persisted hotkey data without registering anything on Windows."""
    raw = shortcut if isinstance(shortcut, dict) else {}
    fallback_mode = str(double_tap_mode).strip().casefold() or "double_tab"
    raw_mode = str(raw.get("mode", fallback_mode)).strip().casefold()
    mode = raw_mode
    if raw_mode not in {fallback_mode, "sequence", "off"}:
        mode = fallback_mode
        return {"mode": mode, "sequence": str(default_sequence).strip()}
    sequence = str(raw.get("sequence", default_sequence)).strip()
    return {"mode": mode, "sequence": sequence or str(default_sequence).strip()}


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: "GlobalHotkeyManager") -> None:
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt callback name
        if sys.platform != "win32" or not self._owner.active or self._owner.mode != "sequence":
            return False, 0
        try:
            native = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
        except (TypeError, ValueError, OSError):
            return False, 0
        if native.message == WM_HOTKEY and int(native.wParam) == self._owner.hotkey_id:
            QTimer.singleShot(0, self._owner.trigger)
            return True, 0
        return False, 0


class GlobalHotkeyManager:
    """Register one configurable Windows-wide action while the app is running."""

    def __init__(
        self,
        trigger: Callable[[], None],
        *,
        hotkey_id: int = DEFAULT_HOTKEY_ID,
        double_tap_mode: str = "double_tab",
        double_tap_key: int = VK_TAB,
        double_tap_label: str = "双击 Tab",
        disabled_during_ime: bool = False,
        feature_label: str = "期刊库导入",
    ) -> None:
        self.trigger = trigger
        self.hotkey_id = int(hotkey_id)
        self.double_tap_mode = str(double_tap_mode).casefold()
        self.double_tap_key = int(double_tap_key)
        self.double_tap_label = str(double_tap_label)
        self.disabled_during_ime = bool(disabled_during_ime)
        self.feature_label = str(feature_label)
        self.active = False
        self.mode = "off"
        self.sequence = ""
        self._registered_hotkey = False
        self._keyboard_hook = None
        self._keyboard_proc = None
        self._last_tap_time = 0.0
        self._tap_is_down = False
        self._filter = _HotkeyFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)

    def configure(self, shortcut: dict | None) -> HotkeyStatus:
        self.clear()
        shortcut = normalize_shortcut_config(shortcut, self.double_tap_mode, "")
        mode = shortcut["mode"]
        if mode == "off":
            return HotkeyStatus(False, f"{self.feature_label}快捷键已关闭")
        if mode == self.double_tap_mode:
            return self._configure_double_tap()
        if mode != "sequence":
            return HotkeyStatus(False, "无法识别的快捷键设置")
        sequence = QKeySequence(str(shortcut.get("sequence", "")))
        if sequence.isEmpty():
            return HotkeyStatus(False, "请设置一个带 Ctrl、Alt、Shift 或 Win 的组合键")
        translated = _virtual_key(sequence[0])
        if translated is None:
            return HotkeyStatus(False, "全局组合键需含 Ctrl、Alt、Shift 或 Win，并使用常规按键")
        if sys.platform != "win32":
            return HotkeyStatus(False, "全局组合键仅支持 Windows")
        modifiers, key = translated
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, self.hotkey_id, modifiers | MOD_NOREPEAT, key):
            error = ctypes.get_last_error()
            detail = "该组合键正被其他程序占用" if error == 1409 else "Windows 未能注册该组合键"
            return HotkeyStatus(False, detail)
        self.active = True
        self.mode = "sequence"
        self._registered_hotkey = True
        self.sequence = sequence.toString()
        return HotkeyStatus(True, f"全局组合键 {self.sequence} 已启用")

    def _configure_double_tap(self) -> HotkeyStatus:
        """Install a passive hook for a global two-tap shortcut."""
        if sys.platform != "win32":
            return HotkeyStatus(False, f"全局{self.double_tap_label}仅支持 Windows")
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _LowLevelKeyboardProc, ctypes.c_void_p, ctypes.c_uint]
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            user32.UnhookWindowsHookEx.restype = ctypes.c_int
            user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, _WPARAM, _LPARAM]
            user32.CallNextHookEx.restype = _LRESULT
            self._keyboard_proc = _LowLevelKeyboardProc(self._keyboard_callback)
            module = kernel32.GetModuleHandleW(None)
            hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._keyboard_proc, module, 0)
        except (AttributeError, OSError) as error:
            self._keyboard_proc = None
            return HotkeyStatus(False, f"Windows 未能启用全局{self.double_tap_label}：{error}")
        if not hook:
            self._keyboard_proc = None
            return HotkeyStatus(False, f"Windows 未能启用全局{self.double_tap_label}")
        self._keyboard_hook = hook
        self.active = True
        self.mode = self.double_tap_mode
        self.sequence = self.double_tap_label
        return HotkeyStatus(True, f"全局{self.double_tap_label}已启用")

    def _keyboard_callback(self, code, message, l_param):
        """Observe the configured key globally and always pass it onward."""
        user32 = ctypes.windll.user32
        try:
            if code == HC_ACTION and self.active and self.mode == self.double_tap_mode:
                event = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                key = int(event.vkCode)
                message = int(message)
                if key == self.double_tap_key:
                    if message in {WM_KEYDOWN, WM_SYSKEYDOWN} and not self._tap_is_down:
                        self._tap_is_down = True
                        blocked = self._has_keyboard_modifier() or (
                            self.disabled_during_ime and self._ime_input_active()
                        )
                        if blocked:
                            self._last_tap_time = 0.0
                        else:
                            now = monotonic()
                            if now - self._last_tap_time <= 0.55:
                                self._last_tap_time = 0.0
                                QTimer.singleShot(0, self.trigger)
                            else:
                                self._last_tap_time = now
                    elif message in {WM_KEYUP, WM_SYSKEYUP}:
                        self._tap_is_down = False
                elif message in {WM_KEYDOWN, WM_SYSKEYDOWN}:
                    self._last_tap_time = 0.0
        except (TypeError, ValueError, OSError):
            # A hook must never block typing because a callback payload was
            # malformed, so fall through to CallNextHookEx.
            pass
        return user32.CallNextHookEx(self._keyboard_hook, code, message, l_param)

    @staticmethod
    def _has_keyboard_modifier() -> bool:
        user32 = ctypes.windll.user32
        return any(
            bool(user32.GetAsyncKeyState(key) & 0x8000)
            for key in (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN)
        )

    @staticmethod
    def _ime_input_active() -> bool:
        """Treat an open IME on the foreground window as active text input.

        This intentionally errs on the safe side: double-space remains a
        normal space while a Chinese/Japanese/Korean input method is open.
        """
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            imm32 = ctypes.windll.imm32
            user32.GetForegroundWindow.restype = ctypes.c_void_p
            imm32.ImmGetContext.argtypes = [ctypes.c_void_p]
            imm32.ImmGetContext.restype = ctypes.c_void_p
            imm32.ImmGetOpenStatus.argtypes = [ctypes.c_void_p]
            imm32.ImmGetOpenStatus.restype = ctypes.c_int
            imm32.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            imm32.ImmReleaseContext.restype = ctypes.c_int
            window = user32.GetForegroundWindow()
            if not window:
                return False
            context = imm32.ImmGetContext(window)
            if not context:
                return False
            try:
                return bool(imm32.ImmGetOpenStatus(context))
            finally:
                imm32.ImmReleaseContext(window, context)
        except (AttributeError, OSError):
            return False

    def clear(self) -> None:
        if sys.platform == "win32":
            if self._registered_hotkey:
                ctypes.windll.user32.UnregisterHotKey(None, self.hotkey_id)
            if self._keyboard_hook:
                ctypes.windll.user32.UnhookWindowsHookEx(self._keyboard_hook)
        self.active = False
        self.mode = "off"
        self.sequence = ""
        self._registered_hotkey = False
        self._keyboard_hook = None
        self._keyboard_proc = None
        self._last_tap_time = 0.0
        self._tap_is_down = False

    def close(self) -> None:
        self.clear()
        app = QApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self._filter)
