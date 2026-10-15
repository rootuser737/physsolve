"""Offline rich-content view for the AI chat (QtWebEngine).

Renders markdown, LaTeX (KaTeX), Mermaid, SVG, p5.js and three.js entirely on this
machine from the bundled web/vendor folder. All network access is blocked, so
AI-generated code cannot phone home, and p5/three code runs in sandboxed iframes.
"""
import json
import os
import platform
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from paths import resource_path

WEB_DIR = resource_path("web")


def configure_chromium(mode="compatible"):
    """Call BEFORE creating QApplication. 'compatible' = software rendering: works on every machine, VM and
    remote desktop (no GPU/driver surprises). 'fast' = let Chromium use the GPU."""
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    add = "--disable-gpu --disable-gpu-compositing --enable-unsafe-swiftshader" if mode == "compatible" else "--ignore-gpu-blocklist"
    for f in add.split():
        if f not in flags:
            flags += " " + f
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags.strip()
    if mode == "compatible":
        os.environ.setdefault("QT_QUICK_BACKEND", "software")

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
    console = []

    def javaScriptConsoleMessage(self, level, message, line, source):
        if "ermissions policy" in message or "device" in message.lower():      # harmless sensor warnings from p5/three
            return
        SafePage.console = (SafePage.console + [f"{message} ({Path(source).name}:{line})"])[-20:]

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

    # ---- theme + health + saving helpers --------------------------------------------------------------
    def set_theme(self, pal, dark):
        self._theme = (pal, dark)
        if self._ready:
            self.page().runJavaScript(f"window.setTheme({json.dumps(pal)}, {json.dumps(bool(dark))});")

    def health(self, cb):
        """cb(dict) with which libraries are alive inside the page."""
        js = ("JSON.stringify({marked: typeof marked, katex: typeof katex, mermaid: typeof mermaid, purify: typeof DOMPurify,"
              " setMessages: typeof window.setMessages, libs: !!window.LIBSRC})")

        def got(r):
            try:
                d = json.loads(r)
            except (TypeError, ValueError):
                d = {}
            d["loaded"] = self._ready
            d["ok"] = bool(self._ready and all(d.get(k) not in (None, "undefined") for k in ("marked", "katex", "mermaid", "purify"))
                           and d.get("setMessages") == "function" and d.get("libs"))
            cb(d)
        self.page().runJavaScript(js, got)

    def collect_widgets(self, cb):
        self.page().runJavaScript("window.collectWidgets ? window.collectWidgets() : '[]'",
                                  lambda r: cb(json.loads(r) if r else []))

    def save_pdf(self, path, cb=None):
        def done(p, ok):
            if cb:
                cb(p, ok)
        self.page().pdfPrintingFinished.connect(done)
        self.page().printToPdf(str(path))

    def diagnostics(self, cb):
        def got(h):
            lines = ["SciSketch render diagnostics", "-" * 34, f"Python {sys.version.split()[0]} on {platform.platform()}",
                     f"Chromium flags: {os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '(none)')}",
                     f"Qt quick backend: {os.environ.get('QT_QUICK_BACKEND', 'default')}",
                     f"Page loaded: {h.get('loaded')}   renderer healthy: {h.get('ok')}",
                     f"Libraries: marked={h.get('marked')} katex={h.get('katex')} mermaid={h.get('mermaid')} "
                     f"purify={h.get('purify')} p5/three bundled={h.get('libs')}",
                     f"Blocked network requests (expected 0 for normal use): {len(OfflineGuard.blocked)}"]
            if SafePage.console:
                lines.append("Page console: " + " | ".join(SafePage.console[-4:]))
            cb("\n".join(lines), h)
        self.health(got)

    def _loaded(self, ok):
        self._ready = ok
        if ok and getattr(self, "_theme", None):
            self.set_theme(*self._theme)
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
