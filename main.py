"""科研助手 - Windows desktop MVP entry point."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4
from collections.abc import Callable

from utils.storage_bootstrap import prepare_v12_data_root

# Bind the stable personal-data root before importing any module that creates
# JSON paths at import time.
prepare_v12_data_root()

from utils.action_transaction import recover_json_transactions
from utils.file_manager import DATA_DIR

# Recover any interrupted multi-file action before application readers open data.
recover_json_transactions(DATA_DIR)

from PySide6.QtCore import QSharedMemory
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.theme import DIALOG_BASE_STYLE, apply_application_theme, ensure_application_font
from utils.app_info import APP_NAME, APP_VERSION, INSTANCE_CHANNEL
from utils.file_manager import load_app_settings


SERVER_NAME = f"ScientificAssistantDesktopWidget_SingleInstance_{INSTANCE_CHANNEL}"
SHARED_MEMORY_KEY = f"ScientificAssistantDesktopWidget_InstanceLock_{INSTANCE_CHANNEL}"


def run_startup_probe() -> int:
    """Non-visual acceptance probe used immediately after enabling autostart."""

    app = QApplication.instance() or QApplication(sys.argv)
    executable = Path(sys.executable).resolve()
    entrypoint = executable if getattr(sys, "frozen", False) else Path(__file__).resolve()
    if not executable.is_file() or not entrypoint.is_file():
        return 61
    probe_file = DATA_DIR / f".startup-probe-{uuid4().hex}.tmp"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe_file.write_text("ok", encoding="utf-8")
        if probe_file.read_text(encoding="utf-8") != "ok":
            return 62
    except OSError:
        return 63
    finally:
        try:
            probe_file.unlink(missing_ok=True)
        except OSError:
            pass
    suffix = uuid4().hex
    memory = QSharedMemory(SHARED_MEMORY_KEY + "_StartupProbe_" + suffix)
    server = QLocalServer()
    server_name = SERVER_NAME + "_StartupProbe_" + suffix
    try:
        if not memory.create(1):
            return 64
        QLocalServer.removeServer(server_name)
        if not server.listen(server_name):
            return 65
    finally:
        server.close()
        QLocalServer.removeServer(server_name)
        if memory.isAttached():
            memory.detach()
    app.processEvents()
    return 0


class SingleInstance:
    """Use a local socket to activate an existing instance instead of opening another one."""

    def __init__(self) -> None:
        self.server = QLocalServer()
        self.shared_memory = QSharedMemory(SHARED_MEMORY_KEY)
        self.callback: Callable[[], None] | None = None
        self.is_primary = self._start_server()

    def _start_server(self) -> bool:
        if self.shared_memory.create(1):
            return self._listen_as_primary()
        if self._notify_primary():
            return False

        # A crashed process can leave a stale shared-memory key or pipe.
        if self.shared_memory.attach():
            self.shared_memory.detach()
        QLocalServer.removeServer(SERVER_NAME)
        if self.shared_memory.create(1):
            return self._listen_as_primary()
        return False

    def _listen_as_primary(self) -> bool:
        QLocalServer.removeServer(SERVER_NAME)
        if self.server.listen(SERVER_NAME):
            self.server.newConnection.connect(self._activate_existing_window)
            return True
        self.shared_memory.detach()
        return False

    @staticmethod
    def _notify_primary() -> bool:
        socket = QLocalSocket()
        socket.connectToServer(SERVER_NAME)
        if socket.waitForConnected(400):
            socket.write(b"activate")
            socket.waitForBytesWritten(200)
            socket.disconnectFromServer()
            return True
        return False

    def _activate_existing_window(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readAll()
            socket.disconnectFromServer()
            if self.callback:
                self.callback()


def main() -> int:
    if "--startup-probe" in sys.argv:
        sys.argv = [value for value in sys.argv if value != "--startup-probe"]
        return run_startup_probe()
    started_at_login = "--autostart" in sys.argv
    if started_at_login:
        # Keep Qt from interpreting the application-specific switch.
        sys.argv = [value for value in sys.argv if value != "--autostart"]
    try:
        from utils.v13_migration import migrate_to_v13

        migrate_to_v13()
    except Exception as error:  # noqa: BLE001 - backup-first migration failure must not destroy access to old data
        print(f"科研助手 13.0 数据迁移未完成：{error}", file=sys.stderr)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Personal Research Assistant")
    app.setProperty("research_assistant_runtime_ready", True)
    app.setStyle("Fusion")
    ensure_application_font(app)
    # Dialogs are top-level windows, so they do not inherit the main widget's
    # stylesheet.  Give every editor, setting and reminder a shared baseline.
    app.setStyleSheet(DIALOG_BASE_STYLE)
    # Apply the saved theme before any widget exists.  The main window builds
    # its pages afterwards, so the full QSS is polished once on an empty
    # application instead of re-polishing every page at startup or on the
    # first Settings preview/save (which used to freeze the UI for seconds).
    settings = load_app_settings()
    appearance = settings.get("appearance", {})
    appearance = appearance if isinstance(appearance, dict) else {}
    apply_application_theme(
        app,
        str(appearance.get("theme_id", "fog_teal")),
        str(appearance.get("density", "comfortable")),
    )
    app.setQuitOnLastWindowClosed(False)

    instance = SingleInstance()
    if not instance.is_primary:
        return 0

    window = MainWindow()
    instance.callback = window.show_and_activate
    if started_at_login:
        window.start_in_tray()
    else:
        window.show_and_activate()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
