"""SciSketch AI client + the live-teacher chat panel.

The API key is read from the SCISKETCH_KEY environment variable or from a local
config.json (git-ignored). It is never hard-coded and never sent anywhere except
the configured endpoint.
"""
import json
import os
import re
from pathlib import Path

import requests
from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
)

from paths import exe_dir, user_config_path

DEFAULT_URL = "https://ikzsalomhnqxkevmhtrx.supabase.co/functions/v1/api-chat"
MAX_MESSAGES, MAX_CHARS = 50, 20000          # server limits (413 beyond these)


class ApiError(Exception):
    def __init__(self, code, message, retry_after=None):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after

    def __str__(self):
        extra = f" (retry in {self.retry_after}s)" if self.retry_after else ""
        return f"[{self.code}] {super().__str__()}{extra}"


def load_config():
    cfg = {"api_url": DEFAULT_URL, "api_key": ""}
    for p in (user_config_path(), exe_dir() / "config.json"):       # later file wins
        if p.exists():
            try:
                cfg.update(json.loads(p.read_text(encoding="utf-8")))
            except ValueError:
                pass
    cfg["api_key"] = os.environ.get("SCISKETCH_KEY", cfg.get("api_key", ""))
    return cfg


def trim(messages):
    """Keep the newest messages within the server's count and character limits."""
    out, total = [], 0
    for m in reversed(messages[-MAX_MESSAGES:]):
        total += len(m["content"])
        if total > MAX_CHARS and out:
            break
        out.append(m)
    return list(reversed(out))


class SciSketchClient:
    def __init__(self, url, key):
        self.url, self.key = url.rstrip("/"), key

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    @staticmethod
    def _error(r):
        try:
            e = r.json()["error"]
            code, msg = e.get("code", str(r.status_code)), e.get("message", r.text[:200])
        except Exception:
            code, msg = str(r.status_code), r.text[:200]
        return ApiError(code, msg, r.headers.get("Retry-After"))

    def health(self):
        r = requests.get(self.url + "/health", timeout=10)
        if r.status_code != 200:
            raise self._error(r)
        return r.json()

    def chat(self, messages):
        r = requests.post(self.url, headers=self.headers, timeout=(10, 120),
                          json={"messages": trim(messages), "stream": False})
        if r.status_code != 200:
            raise self._error(r)
        return r.json()["content"]

    def stream(self, messages):
        with requests.post(self.url, headers=self.headers, stream=True, timeout=(10, 120),
                           json={"messages": trim(messages), "stream": True}) as r:
            if r.status_code != 200:
                raise self._error(r)
            r.encoding = "utf-8"
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content", "")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


class Worker(QThread):
    chunk = Signal(str)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            for piece in self.fn():
                self.chunk.emit(piece)
        except ApiError as e:
            self.failed.emit(str(e))
        except requests.RequestException as e:
            self.failed.emit(f"Network problem: {e.__class__.__name__}. Check your connection and endpoint URL.")
        except Exception as e:  # keep the UI alive no matter what
            self.failed.emit(f"Unexpected error: {e}")


try:
    from renderer import ChatView          # offline rich rendering (needs QtWebEngine)
except ImportError:
    ChatView = None

from exporter import tex_plain


class FallbackView(QTextBrowser):
    """Plain view used when Chromium cannot start on this machine. Formulas are shown as readable Unicode."""
    rich = False

    def __init__(self, *a):
        super().__init__(*a)
        self.setOpenExternalLinks(True)
        self._msgs, self._pal = [], None

    def set_theme(self, pal, dark):
        self._pal = pal
        self.setStyleSheet(f"QTextBrowser {{ background:{pal['bg']}; color:{pal['fg']}; border:none; padding:8px; font-size:10.5pt; }}")
        self.set_messages(self._msgs)

    def set_messages(self, messages, streaming=False):
        self._msgs = messages
        names = {"user": "You", "assistant": "Teacher", "system": "Note"}
        self.setMarkdown("\n\n---\n\n".join(f"**{names[m['role']]}:** {tex_plain(m['content'])}" for m in messages))
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def health(self, cb): cb(dict(ok=True, loaded=True, fallback=True))
    def collect_widgets(self, cb): cb([])
    def diagnostics(self, cb): cb("Compatibility view is active (Chromium renderer unavailable).", {"ok": False, "fallback": True})


ACTION_RE = re.compile(r"```scisketch-run\s*(\{.*?\})\s*```", re.S)

PREAMBLE = """[SciSketch live-teacher context - not part of the student's question]
%(persona)s
You are the live teacher inside SciSketch, a PySide6 lab that verifies every run against a high-precision
reference. Be concise (under 180 words unless asked), warm, and Socratic: end with ONE short question that makes the
student predict or try something. Use LaTeX ($...$) for equations. ONLY when the student asks you to run or set up a
simulation, include exactly one fenced block like this (JSON, double quotes, omit unused keys):

```scisketch-run
{"system": "Kepler orbit", "method": "RK4", "dt": 0.002, "T": 20, "params": {"e": 0.6}, "compare": false}
```
Use "compare": true to overlay all three integrators. When explaining results, use ONLY the verified numbers below
and never invent numbers.

%(context)s
[End of context]

Student: %(text)s"""


class ChatPanel(QFrame):
    """Card with the transcript, input box and the AI / offline-teacher routing."""
    user_text = Signal(str)            # the student typed something (main window decides how to answer)
    assistant_said = Signal(str)       # any teacher message (AI or offline) - used for speech
    health_changed = Signal(bool)

    def __init__(self, main):
        super().__init__()
        self.setObjectName("card")
        self.main, self.history, self.worker, self.streaming, self.level = main, [], None, False, "Undergraduate"
        self.cfg = load_config()
        self.client = SciSketchClient(self.cfg["api_url"], self.cfg["api_key"])
        lay = QVBoxLayout(self); lay.setContentsMargins(14, 12, 14, 14); lay.setSpacing(8)
        top = QHBoxLayout()
        t = QLabel("Live teacher"); t.setObjectName("h")
        self.mode = QLabel(); self.mode.setObjectName("badge")
        top.addWidget(t); top.addStretch(1); top.addWidget(self.mode)
        lay.addLayout(top)
        self.view = ChatView() if ChatView else FallbackView()
        lay.addWidget(self.view, 1)
        self.status = QLabel(); self.status.setObjectName("muted")
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.box = QLineEdit(); self.box.setPlaceholderText("Ask anything about this experiment...")
        self.send = QPushButton("Send"); self.send.setObjectName("primary")
        row.addWidget(self.box, 1); row.addWidget(self.send)
        lay.addLayout(row)
        self.send.clicked.connect(self.on_send)
        self.box.returnPressed.connect(self.on_send)
        self.refresh_mode()
        self._checks = 0
        if ChatView:
            QTimer.singleShot(2500, self.check_health)      # is Chromium alive? if not -> compatibility view

    # ---- status
    @property
    def ai_ready(self):
        return bool(self.cfg.get("api_key"))

    def refresh_mode(self):
        self.mode.setText("AI + offline teacher" if self.ai_ready else "Offline teacher")

    def set_theme(self, pal, dark):
        self._pal = (pal, dark)
        self.view.set_theme(pal, dark)

    # ---- renderer health
    def check_health(self):
        def got(h):
            if h.get("ok"):
                self.health_changed.emit(True); return
            self._checks += 1
            if self._checks < 6:
                QTimer.singleShot(2000, self.check_health)
            else:
                self.use_fallback("The rich renderer (Chromium) could not start on this computer, so the simple view is used. "
                                  "Everything still works; formulas appear as plain text. Try Appearance > Rendering > Compatible.")
        self.view.health(got)

    def use_fallback(self, why):
        old = self.view
        new = FallbackView()
        lay = self.layout(); lay.replaceWidget(old, new)
        old.hide(); old.deleteLater(); self.view = new
        if getattr(self, "_pal", None):
            new.set_theme(*self._pal)
        self.say("system", why); self.render(); self.health_changed.emit(False)

    # ---- transcript
    def say(self, who, text):
        self.history.append({"role": who, "content": text})
        if who == "assistant":
            self.assistant_said.emit(text)

    def render(self):
        self.view.set_messages(self.history, self.streaming)

    def teacher(self, md):
        """Post an offline-teacher message."""
        self.say("assistant", md); self.render()

    def busy(self, b):
        self.send.setEnabled(not b)
        self.status.setText("Thinking..." if b else "")

    # ---- AI key / health
    def on_key(self):
        key, ok = QInputDialog.getText(self, "SciSketch API key", "Paste your SciSketch API key:", QLineEdit.EchoMode.Password)
        if not ok or not key.strip():
            return
        path = user_config_path(); path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data["api_key"] = key.strip()
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self.cfg["api_key"] = self.client.key = key.strip()
        self.refresh_mode(); self.say("system", "API key saved on this computer. The AI teacher is on."); self.render()

    def on_check(self):
        self.busy(True)
        self.worker = Worker(lambda: [json.dumps(self.client.health())])
        self.worker.chunk.connect(lambda t: self.say("system", f"API healthy: {t}"))
        self.worker.failed.connect(lambda t: self.say("system", t))
        self.worker.finished.connect(lambda: (self.busy(False), self.render()))
        self.worker.start()

    # ---- sending
    def on_send(self):
        text = self.box.text().strip()
        if not text or (self.worker and self.worker.isRunning()):
            return
        self.box.clear()
        self.say("user", text); self.render()
        self.user_text.emit(text)

    def ask_ai(self, text, context, persona, on_fail=None, shown=True):
        """Stream an answer from the AI. `on_fail(msg)` lets the caller fall back to the offline teacher."""
        convo = [m for m in self.history if m["role"] in ("user", "assistant") and m["content"]]
        if convo and convo[-1]["role"] == "user" and convo[-1]["content"] == text:
            convo = convo[:-1]
        prompt = PREAMBLE % dict(persona=persona, context=context, text=text)
        self.say("assistant", ""); self.streaming = True; self.render(); self.busy(True)
        self._on_fail = on_fail
        self.worker = Worker(lambda: self.client.stream(convo + [{"role": "user", "content": prompt}]))
        self.worker.chunk.connect(self.on_chunk)
        self.worker.failed.connect(self.on_fail)
        self.worker.finished.connect(self.on_done)
        self.worker.start()

    def on_chunk(self, piece):
        self.history[-1]["content"] += piece
        self.render()

    def on_fail(self, msg):
        self.streaming = False
        if self.history and not self.history[-1]["content"]:
            self.history.pop()
        self.say("system", msg + " - answering with the offline teacher instead.")
        cb, self._on_fail = getattr(self, "_on_fail", None), None
        if cb:
            cb(msg)

    def on_done(self):
        self.streaming = False
        self.busy(False)
        if self.history and self.history[-1]["role"] == "assistant" and self.history[-1]["content"]:
            txt = self.history[-1]["content"]
            m = ACTION_RE.search(txt)
            if m:
                try:
                    self.say("system", f"Ran simulation: {self.main.apply_action(json.loads(m.group(1)))}")
                except Exception as e:  # noqa: BLE001
                    self.say("system", f"Could not apply the AI's simulation request: {e}")
            self.assistant_said.emit(ACTION_RE.sub("", txt))
        self.render()


ChatDock = ChatPanel          # backwards-compatible name
