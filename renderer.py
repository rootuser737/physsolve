"""Offline rich-content view for the AI chat (QtWebEngine).

Renders markdown, LaTeX (KaTeX), Mermaid, SVG, p5.js and three.js entirely on this
machine from the bundled web/vendor folder. All network access is blocked, so
AI-generated code cannot phone home, and p5/three code runs in sandboxed iframes.
"""
import json
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from paths import resource_path

WEB_DIR = resource_path("web")
ALLOWED_SCHEMES = {"file", "data", "blob", "about", "qrc"}
SANDBOX_LIBS = {"p5": "p5.min.js", "three": "three.min.js"}   # inlined into sandboxed iframes by chat.js


class OfflineGuard(QWebEngineUrlRequestInterceptor):
    """Block every request that is not a local resource."""
    blocked = []

    def interceptRequest(self, info):
        if info.requestUrl().scheme() not in ALLOWED_SCHEMES:
            OfflineGuard.blocked.append(info.requestUrl().toString())
            info.block(True)


class SafePage(QWebEnginePage):
    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if url.scheme() in ("http", "https"):
                QDesktopServices.openUrl(url)       # open links in the system browser
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class ChatView(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.profile = QWebEngineProfile(self)      # no name = off-the-record, nothing stored on disk
        self.guard = OfflineGuard(self)
        self.profile.setUrlRequestInterceptor(self.guard)
        self.setPage(SafePage(self.profile, self))
        s = self.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        self._ready, self._pending = False, None
        self._timer = QTimer(self, singleShot=True, interval=60)   # coalesce streaming updates
        self._timer.timeout.connect(self._flush)
        self.loadFinished.connect(self._loaded)
        self.load(QUrl.fromLocalFile(str(WEB_DIR / "chat.html")))

    def _loaded(self, ok):
        self._ready = ok
        if ok:
            libs = {k: (WEB_DIR / "vendor" / f).read_text(encoding="utf-8").replace("</script", "<\\/script")
                    for k, f in SANDBOX_LIBS.items()}
            self.page().runJavaScript(f"window.LIBSRC = {json.dumps(libs)};")
        if ok and self._pending is not None:
            self._flush()

    def set_messages(self, messages, streaming=False):
        self._pending = (messages, streaming)
        if self._ready and not self._timer.isActive():
            self._timer.start()

    def _flush(self):
        if self._pending is None:
            return
        msgs, streaming = self._pending
        self._pending = None
        self.page().runJavaScript(f"window.setMessages({json.dumps(msgs)}, {json.dumps(bool(streaming))});")
