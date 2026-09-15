from types import SimpleNamespace

from ui import main_window


class _Signal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback


class _Timer:
    instances = []
    singles = []

    def __init__(self, parent=None) -> None:
        self.parent = parent
        self.timeout = _Signal()
        self.interval = 0
        self.started = False
        self.stopped = False
        self.instances.append(self)

    def setInterval(self, value: int) -> None:
        self.interval = value

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    @classmethod
    def singleShot(cls, delay: int, callback) -> None:
        cls.singles.append((delay, callback))


def test_special_issue_due_check_runs_after_startup_and_hourly(monkeypatch):
    _Timer.instances.clear()
    _Timer.singles.clear()
    monkeypatch.delenv("RESEARCH_ASSISTANT_DISABLE_BACKGROUND", raising=False)
    monkeypatch.setattr(main_window, "QTimer", _Timer)
    page = SimpleNamespace(auto_refresh_if_due=lambda: None)
    window = SimpleNamespace(special_issue_page=page)

    main_window.MainWindow._start_special_issue_checks(window)

    timer = window._special_issue_timer
    assert timer.interval == 60 * 60 * 1000
    assert timer.timeout.callback == page.auto_refresh_if_due
    assert timer.started
    assert _Timer.singles == [(12000, page.auto_refresh_if_due)]
