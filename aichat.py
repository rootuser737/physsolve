"""SciSketch AI desktop client + chat dock for PhysSolve.

The API key is read from the SCISKETCH_KEY environment variable or from a local
config.json (git-ignored). It is never hard-coded and never sent anywhere except
the configured endpoint.
"""
import json
import os
import re
from pathlib import Path

import requests
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDockWidget, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser,
    QVBoxLayout, QWidget,
)

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
    p = Path(__file__).with_name("config.json")
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
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


class FallbackView(QTextBrowser):
    """Plain markdown view used only if QtWebEngine is unavailable."""
    def set_messages(self, messages, streaming=False):
        names = {"user": "You", "assistant": "AI", "system": "Note"}
        self.setMarkdown("\n\n".join(f"**{names[m['role']]}:** {m['content']}" for m in messages))
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


ACTION_RE = re.compile(r"```physsolve\s*(\{.*?\})\s*```", re.S)

PREAMBLE = """[PhysSolve desktop context - not part of the student's question]
You are embedded in PhysSolve, a PySide6 simulator that verifies every run against a
high-precision reference. ONLY when the student asks you to run or set up a simulation,
include exactly one fenced block like this (JSON, double quotes, omit unused keys):

```physsolve
{"system": "Kepler orbit", "method": "RK4", "dt": 0.002, "T": 20, "params": {"e": 0.6}, "compare": false}
```
Use "compare": true to overlay all three integrators. When explaining results, use ONLY
the verified numbers below and never invent numbers.

%s
[End of context]

Student: %s"""


class ChatDock(QDockWidget):
    def __init__(self, main):
        super().__init__("AI tutor (SciSketch)", main)
        self.main, self.history, self.worker, self.streaming = main, [], None, False
        self.cfg = load_config()
        self.client = SciSketchClient(self.cfg["api_url"], self.cfg["api_key"])

        w = QWidget(); lay = QVBoxLayout(w)
        self.view = ChatView() if ChatView else FallbackView()
        self.status = QLabel()
        self.box = QLineEdit(); self.box.setPlaceholderText("Ask, or e.g. 'simulate a Kepler orbit with e=0.9 and compare methods'")
        self.send = QPushButton("Send"); self.check = QPushButton("Test connection")
        row = QHBoxLayout(); row.addWidget(self.box, 1); row.addWidget(self.send)
        lay.addWidget(self.view, 1); lay.addWidget(self.status); lay.addLayout(row); lay.addWidget(self.check)
        self.setWidget(w); self.setMinimumWidth(420)

        self.send.clicked.connect(self.on_send)
        self.box.returnPressed.connect(self.on_send)
        self.check.clicked.connect(self.on_check)
        if not self.cfg["api_key"]:
            self.say("system", "No API key found. Create `config.json` next to the app "
                               '(`{"api_key": "..."}`) or set the SCISKETCH_KEY environment variable, then restart.')
        self.render()

    # -- transcript
    def say(self, who, text):
        self.history.append({"role": who, "content": text})

    def render(self):
        self.view.set_messages(self.history, self.streaming)

    def busy(self, b):
        self.send.setEnabled(not b); self.check.setEnabled(not b)
        self.status.setText("Thinking..." if b else "")

    # -- actions
    def on_check(self):
        self.busy(True)
        self.worker = Worker(lambda: [json.dumps(self.client.health())])
        self.worker.chunk.connect(lambda t: self.say("system", f"API healthy: {t}"))
        self.worker.failed.connect(lambda t: self.say("system", t))
        self.worker.finished.connect(lambda: (self.busy(False), self.render()))
        self.worker.start()

    def on_send(self):
        text = self.box.text().strip()
        if not text or not self.cfg["api_key"] or (self.worker and self.worker.isRunning()):
            return
        self.box.clear()
        convo = [m for m in self.history if m["role"] in ("user", "assistant")]
        prompt = PREAMBLE % (self.main.sim_context(), text)
        self.say("user", text)
        self.say("assistant", "")
        self.streaming = True
        self.render(); self.busy(True)
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
        if not self.history[-1]["content"]:
            self.history.pop()
        self.say("system", msg)

    def on_done(self):
        self.streaming = False
        self.busy(False)
        if self.history and self.history[-1]["role"] == "assistant":
            m = ACTION_RE.search(self.history[-1]["content"])
            if m:
                try:
                    note = self.main.apply_action(json.loads(m.group(1)))
                    self.say("system", f"Ran simulation: {note}")
                except Exception as e:
                    self.say("system", f"Could not apply the AI's simulation request: {e}")
        self.render()
