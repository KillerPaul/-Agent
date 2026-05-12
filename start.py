from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMessageBox
from werkzeug.serving import make_server


PROJECT_ROOT = Path(__file__).resolve().parent
SERVER_URL = "http://127.0.0.1:5000"
SERVER_PORT = 5000


def is_server_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=1):
            return True
    except (URLError, OSError):
        return False


class EmbeddedServer:
    def __init__(self, host: str = "127.0.0.1", port: int = SERVER_PORT):
        self.host = host
        self.port = port
        self._server = None
        self._thread = None

    def start(self) -> None:
        if self._server is not None:
            return

        from front.port import app

        self._server = make_server(self.host, self.port, app, threaded=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="embedded-flask-server",
            daemon=True,
        )
        self._thread.start()

        for _ in range(60):
            if is_server_ready(SERVER_URL):
                return
            time.sleep(0.25)

        self.stop()
        raise RuntimeError("后端服务启动失败，未能监听 http://127.0.0.1:5000")

    def stop(self) -> None:
        if self._server is None:
            return

        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None


def main() -> int:
    app = QApplication(sys.argv)
    server = EmbeddedServer()

    try:
        server.start()
    except Exception as exc:
        QMessageBox.critical(None, "启动失败", str(exc))
        return 1

    app.aboutToQuit.connect(server.stop)

    view = QWebEngineView()
    view.setWindowTitle("控制流图助手")
    view.resize(1440, 920)
    view.load(QUrl(SERVER_URL))
    view.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
