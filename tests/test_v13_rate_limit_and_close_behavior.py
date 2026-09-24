from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.main_window import MainWindow
from ui.settings_dialog import SettingsDialog
from utils.api_rate_limit import SlidingWindowRateLimiter
from utils.file_manager import normalize_app_settings


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_global_api_limiter_allows_only_five_calls_per_sliding_second() -> None:
    clock = _FakeClock()
    limiter = SlidingWindowRateLimiter(5, 1.0, clock=clock, sleeper=clock.sleep)

    for _ in range(6):
        limiter.acquire()

    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] == 1.0
    assert clock.now == 1.0


def test_close_to_tray_setting_defaults_off_and_round_trips_through_dialog() -> None:
    application = QApplication.instance() or QApplication([])
    assert application is not None
    assert normalize_app_settings({})["close_to_tray"] is False
    assert normalize_app_settings({"close_to_tray": True})["close_to_tray"] is True

    dialog = SettingsDialog({"close_to_tray": True})
    try:
        assert dialog.close_to_tray.isChecked()
        assert dialog.values()["close_to_tray"] is True
    finally:
        dialog.close()


def test_window_close_is_intercepted_but_explicit_tray_exit_bypasses_it() -> None:
    saved: list[str] = []
    hidden: list[bool] = []
    probe = SimpleNamespace(
        todo_page=SimpleNamespace(save=lambda: saved.append("todo")),
        paper_page=SimpleNamespace(save=lambda: saved.append("paper")),
        settings={"close_to_tray": True},
        _exit_requested=False,
        _hide_to_tray=lambda notify=False: hidden.append(notify),
    )
    event = QCloseEvent()

    MainWindow.closeEvent(probe, event)

    assert not event.isAccepted()
    assert saved == ["todo", "paper"]
    assert hidden == [True]

    explicit = SimpleNamespace(_exit_requested=False, close=lambda: saved.append("closed"))
    MainWindow._quit_application(explicit)
    assert explicit._exit_requested is True
    assert saved[-1] == "closed"
