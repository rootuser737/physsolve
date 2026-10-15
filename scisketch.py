"""SciSketch - a trust-checked physics lab with a live teacher.

Every simulation is verified against a high-precision reference (energy drift, trajectory error, verdict). A built-in
offline teacher explains, narrates, quizzes and runs experiments; an optional AI teacher joins when an API key is set.
Results can be saved as images, videos (MP4/GIF, with narration), documents (DOCX/PDF/HTML/Markdown) and sessions.
"""
import csv
import ctypes
import json
import os
import re
import sys
import time

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QSplitter, QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QButtonGroup, QSlider)

import exporter
import speech
import teacher
from paths import resource_path
from plots import LabPlots
from systems import COLORS, METHODS, SYSTEMS, reference, run_method
from theme import ACCENT_PRESETS, THEMES, apply_theme, mpl_colors
from ui_kit import card, combo, dspin, note, page, row, slider
from version import VERSION

PRESETS = {
    "Experiments...": None,
    "Pendulum - gentle swing (20°)": dict(system="Nonlinear", method="DOP853", params={"th0": 20}, dt=0.002, T=20),
    "Pendulum - big swing (150°)": dict(system="Nonlinear", method="DOP853", params={"th0": 150}, dt=0.002, T=20),
    "Pendulum - Euler vs the truth": dict(system="Nonlinear", params={"th0": 150}, dt=0.05, T=20, compare=True),
    "Planet - circular orbit": dict(system="Kepler", method="DOP853", params={"e": 0.0}, dt=0.002, T=20),
    "Comet - eccentric orbit (compare)": dict(system="Kepler", params={"e": 0.9}, dt=0.002, T=20, compare=True),
    "Double pendulum - chaos": dict(system="Double", params={"a1": 120, "a2": -10}, dt=0.002, T=15, compare=True),
    "Hénon-Heiles - regular (E=0.05)": dict(system="Hénon", method="DOP853", params={"E": 0.05}, dt=0.01, T=60),
    "Hénon-Heiles - chaotic (E=0.16)": dict(system="Hénon", params={"E": 0.16}, dt=0.01, T=60, compare=True),
}
VERDICT_COL = {"TRUSTWORTHY": "#3FD08A", "USABLE": "#F5B83D", "UNRELIABLE": "#F0506E"}


def verdict_chip(v):
    key = next((k for k in VERDICT_COL if v.startswith(k)), "UNRELIABLE")
    c = QColor(VERDICT_COL[key])
    return key, f"background: rgba({c.red()},{c.green()},{c.blue()},46); color:{VERDICT_COL[key]}; border-radius:10px; padding:4px 12px; font-weight:700;"


class Task(QThread):
    """Run a callable off the UI thread."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__(); self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class VideoTask(QThread):
    progress = Signal(float)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, state, colors, path, **kw):
        super().__init__(); self.state, self.colors, self.path, self.kw, self.stop = state, colors, path, kw, False

    def run(self):
        try:
            note_ = exporter.render_video(self.state, self.colors, self.path, progress=self.progress.emit,
                                          cancelled=lambda: self.stop, **self.kw)
            self.done.emit(note_)
        except exporter.Cancelled:
            self.failed.emit("Cancelled")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self, app=None):
        super().__init__()
        self.app = app or QApplication.instance()
        self.qs = QSettings("SciSketch", "SciSketch-test" if os.environ.get("QT_QPA_PLATFORM") == "offscreen" else "SciSketch")
        self.setWindowTitle("SciSketch - verified physics lab & live teacher")
        self.setWindowIcon(QIcon(str(resource_path("assets/scisketch.png"))))
        scr = self.app.primaryScreen().availableGeometry()
        self.resize(min(1560, scr.width() - 40), min(920, scr.height() - 60))
        self.setMinimumSize(1060, 660)
        self.spins, self.results, self.Yref, self.sel = {}, {}, None, None
        self.frame, self.prev_frame, self.bg = 0, 0, None
        self.compare, self.dt_, self.T_ = False, 0.002, 20.0
        self.speaker, self.pal = speech.Speaker(), THEMES["Ocean"]
        self.quiz, self.quiz_i, self.score, self.tasks, self.video = [], 0, [0, 0], [], None
        self.lesson_i, self.run_token, self.user_run = {}, 0, False
        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self.tick)

        from aichat import ChatPanel
        self.chat = ChatPanel(self)
        self.chat.user_text.connect(self.handle_user_text)
        self.chat.assistant_said.connect(self.on_assistant_said)

        root = QWidget(); root.setObjectName("root"); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(18, 14, 18, 16); outer.setSpacing(12)
        outer.addWidget(self._header())
        body = QHBoxLayout(); body.setSpacing(12)
        left = QVBoxLayout(); left.setSpacing(10)
        tabs = QFrame(); tabs.setObjectName("card"); th = QHBoxLayout(tabs); th.setContentsMargins(6, 6, 6, 6); th.setSpacing(2)
        self.stack = QStackedWidget(); self.tab_btns = []
        group = QButtonGroup(self); group.setExclusive(True)
        for i, (name, pg) in enumerate((("Experiment", self.page_experiment()), ("Teacher", self.page_teacher()),
                                        ("Export", self.page_export()), ("Library", self.page_library()),
                                        ("Appearance", self.page_appearance()))):
            b = QPushButton(name); b.setObjectName("tab"); b.setCheckable(True); group.addButton(b, i); th.addWidget(b)
            self.stack.addWidget(pg); self.tab_btns.append(b)
        group.idClicked.connect(self.set_page)
        left.addWidget(tabs); left.addWidget(self.stack, 1)
        leftw = QWidget(); leftw.setLayout(left); leftw.setFixedWidth(412)
        body.addWidget(leftw)
        self.set_page(0)
        split = QSplitter(); split.setChildrenCollapsible(False)
        split.addWidget(self._plot_card()); split.addWidget(self.chat)
        split.setStretchFactor(0, 3); split.setStretchFactor(1, 2); split.setSizes([700, 460])
        self.chat.setMinimumWidth(340)
        body.addWidget(split, 1)
        outer.addLayout(body, 1)
        self.sys_box.currentIndexChanged.connect(self.on_system)
        self.restyle()
        self.build_form()
        self.run(False, user=False)
        self.chat.say("assistant", "### Welcome to SciSketch\nI'm your **live teacher**. Press *Explain what I see*, choose an experiment "
                      "from the list at the top, or just ask me a question. " +
                      ("The AI teacher is connected." if self.chat.ai_ready else "I work fully offline; add an API key in the Live teacher tab for AI answers."))
        self.chat.render()

    # ------------------------------------------------------------------ layout
    def _header(self):
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(2, 0, 2, 0)
        logo = QLabel(); logo.setPixmap(QPixmap(str(resource_path("assets/scisketch.png"))).scaled(46, 46, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        col = QVBoxLayout(); col.setSpacing(0)
        t = QLabel("SciSketch"); t.setObjectName("title")
        st = QLabel("Verified physics lab & live teacher"); st.setObjectName("muted")
        col.addWidget(t); col.addWidget(st)
        badge = QLabel(f"v{VERSION}"); badge.setObjectName("badge")
        h.addWidget(logo); h.addLayout(col); h.addWidget(badge, 0, Qt.AlignVCenter); h.addStretch(1)
        h.addWidget(QLabel("Quick experiment"))
        self.preset = combo(PRESETS); self.preset.setMinimumWidth(270)
        self.preset.activated.connect(self.apply_preset)
        h.addWidget(self.preset)
        return w

    def _plot_card(self):
        f, lay = card(); lay.setContentsMargins(10, 10, 10, 12)
        self.fig = Figure(constrained_layout=True)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.plots = None
        self.canvas.mpl_connect("draw_event", self._on_draw)
        self._rl = QTimer(self, singleShot=True, interval=220); self._rl.timeout.connect(self._relayout)
        self.canvas.mpl_connect("resize_event", lambda ev: self._rl.start())
        lay.addWidget(self.canvas, 1)
        bar = QHBoxLayout()
        self.btn_play = QPushButton("Pause"); self.btn_play.setFixedWidth(84); self.btn_play.clicked.connect(self.toggle)
        self.btn_restart = QPushButton("Restart"); self.btn_restart.clicked.connect(lambda: (self.set_frame(0), None))
        self.scrub = QSlider(Qt.Horizontal)
        self.scrub.setRange(0, 1000); self.scrub.sliderMoved.connect(self.on_scrub)
        sw, self.speed = slider(1, 40, 6, lambda v: f"{v}x")
        self.speed_w = sw; sw.setFixedWidth(150)
        self.t_label = QLabel("t = 0.00 s"); self.t_label.setObjectName("muted"); self.t_label.setMinimumWidth(80)
        for w_ in (self.btn_play, self.btn_restart, self.scrub, QLabel("Speed"), sw, self.t_label):
            bar.addWidget(w_, 1 if w_ is self.scrub else 0)
        lay.addLayout(bar)
        return f

    # ------------------------------------------------------------------ pages
    def page_experiment(self):
        f, l = card("Experiment")
        self.sys_box = combo([s.name for s in SYSTEMS]); self.method_box = combo(METHODS)
        self.dt = dspin(0.002, 1e-5, 0.5, 0.001, 5); self.T = dspin(20, 0.5, 500, 1, 1)
        for lab, w in (("System", self.sys_box), ("Method", self.method_box), ("Time step dt (s)", self.dt), ("Duration T (s)", self.T)):
            l.addWidget(row(lab, w))
        f2, self.param_lay = card("Parameters")
        f3, l3 = card("Run")
        b = QHBoxLayout()
        self.btn_run = QPushButton("Run simulation"); self.btn_run.setObjectName("primary")
        self.btn_cmp = QPushButton("Compare all methods")
        self.btn_run.clicked.connect(lambda: self.run(False)); self.btn_cmp.clicked.connect(lambda: self.run(True))
        b.addWidget(self.btn_run, 3); b.addWidget(self.btn_cmp, 2); l3.addLayout(b)
        f4, l4 = card("Verification", "Checked against a DOP853 reference, rtol 1e-12.")
        self.verdict = QLabel("-"); self.verdict.setAlignment(Qt.AlignCenter)
        self.tiles = QLabel(); self.tiles.setTextFormat(Qt.RichText); self.tiles.setWordWrap(True)
        self.report = QTextEdit(); self.report.setReadOnly(True); self.report.setMinimumHeight(170)
        l4.addWidget(self.verdict); l4.addWidget(self.tiles); l4.addWidget(self.report)
        return page(f, f2, f3, f4)

    def page_teacher(self):
        f, l = card("Your live teacher", "Explains, narrates and quizzes using the verified numbers of your own run - no internet needed.")
        self.level = combo(teacher.LEVELS, "Undergraduate")
        self.level.currentTextChanged.connect(lambda t: setattr(self.chat, "level", t))
        self.c_watch = QCheckBox("Watch my experiment (comment after each run)"); self.c_watch.setChecked(True)
        self.c_caps = QCheckBox("Live captions while the animation plays"); self.c_caps.setChecked(True)
        self.c_speak = QCheckBox("Speak aloud" + ("" if speech.available() else " (no voice found on this PC)"))
        self.c_speak.setEnabled(speech.available())
        sw, self.rate = slider(-5, 5, 0, lambda v: f"{v:+d}"); self.rate.valueChanged.connect(lambda v: setattr(self.speaker, "rate", v))
        l.addWidget(row("Explain at level", self.level))
        for c in (self.c_watch, self.c_caps, self.c_speak):
            l.addWidget(c)
        l.addWidget(row("Voice speed", sw))
        for text, fn in (("Explain what I see", self.act_explain), ("Why does this drift?", self.act_why),
                         ("Check convergence order", self.act_convergence), ("Next lesson step", self.act_lesson),
                         ("Suggest an experiment", self.act_suggest), ("Ask the AI teacher to explain", self.act_ai)):
            b = QPushButton(text); b.clicked.connect(fn); l.addWidget(b)
            if text.startswith("Ask the AI"):
                self.btn_ai = b
        stop = QPushButton("Stop speaking"); stop.setObjectName("ghost"); stop.clicked.connect(self.speaker.stop); l.addWidget(stop)
        f2, l2 = card("Quiz me", "Questions are built from your own results.")
        self.q_text = QLabel("Press the button to start."); self.q_text.setWordWrap(True)
        self.q_btns = [QPushButton() for _ in range(4)]
        for i, b in enumerate(self.q_btns):
            b.setVisible(False); b.clicked.connect(lambda _=False, i=i: self.answer(i))
        self.q_fb = QLabel(""); self.q_fb.setWordWrap(True); self.q_score = QLabel(""); self.q_score.setObjectName("muted")
        start = QPushButton("Quiz me"); start.setObjectName("primary"); start.clicked.connect(self.act_quiz)
        self.q_next = QPushButton("Next question"); self.q_next.setVisible(False); self.q_next.clicked.connect(self.next_q)
        l2.addWidget(start); l2.addWidget(self.q_text)
        for b in self.q_btns:
            l2.addWidget(b)
        l2.addWidget(self.q_fb); l2.addWidget(self.q_next); l2.addWidget(self.q_score)
        f3, l3 = card("AI & rendering", "Optional AI teacher, and a check that the rich renderer works on this PC.")
        for text, fn in (("API key...", self.chat.on_key), ("Test AI connection", self.chat.on_check), ("Check rendering", self.act_diag)):
            b = QPushButton(text); b.clicked.connect(fn); l3.addWidget(b)
        return page(f, f2, f3)

    def page_export(self):
        f, l = card("Images", "Everything saves into your SciSketch notebook folder by default.")
        for text, fn in (("Save plot as PNG / SVG / PDF...", self.save_image), ("Copy plot to clipboard", self.copy_plot),
                         ("Save teacher chat as PDF...", self.save_chat_pdf), ("Save AI visuals (SVG / HTML)...", self.save_visuals)):
            b = QPushButton(text); b.clicked.connect(fn); l.addWidget(b)
        f2, l2 = card("Video", "Renders the full animation - all panels and live captions.")
        self.v_fmt = combo(["MP4 (video)", "GIF (animated image)"]); self.v_res = combo(["1280 x 720 (HD)", "1920 x 1080 (Full HD)", "854 x 480"])
        self.v_fps = combo(["24", "30", "60"], "30")
        lw, self.v_len = slider(5, 90, 20, lambda v: f"{v} s")
        self.v_nar = QCheckBox("Teacher speaks the explanation (voice narration)")
        self.v_nar.setEnabled(speech.available()); self.v_nar.setChecked(speech.available())
        for lab, w in (("Format", self.v_fmt), ("Size", self.v_res), ("Frame rate", self.v_fps), ("Length", lw)):
            l2.addWidget(row(lab, w))
        l2.addWidget(self.v_nar)
        self.v_bar = QProgressBar(); self.v_bar.setValue(0)
        self.btn_video = QPushButton("Render video..."); self.btn_video.setObjectName("primary"); self.btn_video.clicked.connect(self.save_video)
        self.btn_vcancel = QPushButton("Cancel"); self.btn_vcancel.setEnabled(False); self.btn_vcancel.clicked.connect(lambda: self.video and setattr(self.video, "stop", True))
        vb = QHBoxLayout(); vb.addWidget(self.btn_video, 3); vb.addWidget(self.btn_vcancel, 1)
        l2.addWidget(self.v_bar); l2.addLayout(vb)
        f3, l3 = card("Lab report", "A document with setup, verification table, figures and the teacher's explanation.")
        self.d_fmt = {k: QCheckBox(v) for k, v in (("docx", "Word (.docx)"), ("pdf", "PDF"), ("html", "Web page (.html)"), ("md", "Markdown (.md)"))}
        self.d_fmt["docx"].setChecked(True)
        for c in self.d_fmt.values():
            l3.addWidget(c)
        self.d_chat = QCheckBox("Include the teacher conversation"); self.d_chat.setChecked(True)
        self.d_notes = QLineEdit(); self.d_notes.setPlaceholderText("Your own notes / conclusion (optional)")
        l3.addWidget(self.d_chat); l3.addWidget(self.d_notes)
        b = QPushButton("Create report"); b.setObjectName("primary"); b.clicked.connect(self.save_report); l3.addWidget(b)
        f4, l4 = card("Session & data")
        for text, fn in (("Save lab session...", self.save_session), ("Open lab session...", self.open_session), ("Export data as CSV...", self.export_csv)):
            b = QPushButton(text); b.clicked.connect(fn); l4.addWidget(b)
        self.export_status = note(""); l4.addWidget(self.export_status)
        return page(f, f2, f3, f4)

    def page_library(self):
        f, l = card("Notebook", f"Everything you save lands in {exporter.notebook_dir()}")
        self.lib = QListWidget(); self.lib.setObjectName("queue"); self.lib.setMinimumHeight(420)
        self.lib.itemDoubleClicked.connect(lambda it: QDesktopServices.openUrl(QUrl.fromLocalFile(it.data(Qt.UserRole))))
        l.addWidget(self.lib)
        r = QHBoxLayout()
        for text, fn in (("Open", lambda: self.lib.currentItem() and QDesktopServices.openUrl(QUrl.fromLocalFile(self.lib.currentItem().data(Qt.UserRole)))),
                         ("Show folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(exporter.notebook_dir())))), ("Refresh", self.refresh_library)):
            b = QPushButton(text); b.clicked.connect(fn); r.addWidget(b)
        l.addLayout(r)
        return page(f)

    def page_appearance(self):
        f, l = card("Theme", "Changes apply instantly and are remembered.")
        self.o_theme = combo(THEMES, self.qs.value("theme", "Ocean")); self.o_theme.currentIndexChanged.connect(lambda _: self.restyle(True))
        l.addWidget(row("Colour theme", self.o_theme))
        f2, l2 = card("Accent colours")
        sw = QHBoxLayout()
        for name, col in ACCENT_PRESETS.items():
            b = QPushButton(); b.setFixedSize(30, 30); b.setToolTip(name)
            b.setStyleSheet(f"background:{col}; border-radius:15px; border:2px solid transparent;")
            b.clicked.connect(lambda _=False, c=col: self.set_accent(c)); sw.addWidget(b)
        sw.addStretch(1); w = QWidget(); w.setLayout(sw); l2.addWidget(w)
        r = QHBoxLayout()
        for text, fn in (("Custom accent...", lambda: self.custom_color("accent")), ("Custom highlight...", lambda: self.custom_color("accent2")),
                         ("Reset", self.reset_accent)):
            b = QPushButton(text); b.clicked.connect(fn); r.addWidget(b)
        w2 = QWidget(); w2.setLayout(r); l2.addWidget(w2)
        f3, l3 = card("Text size & rendering")
        self.o_size = combo(["Compact", "Normal", "Large"], self.qs.value("size", "Normal")); self.o_size.currentIndexChanged.connect(lambda _: self.restyle(True))
        self.o_render = combo(["Compatible (works everywhere)", "Fast (use graphics card)"],
                              "Fast (use graphics card)" if self.qs.value("render", "compatible") == "fast" else "Compatible (works everywhere)")
        self.o_render.currentIndexChanged.connect(lambda i: (self.qs.setValue("render", "fast" if i else "compatible"),
                                                             self.export_status.setText("Rendering mode changes after you restart SciSketch.")))
        l3.addWidget(row("Interface size", self.o_size)); l3.addWidget(row("Chat rendering", self.o_render))
        l3.addWidget(note("Compatible mode draws diagrams and formulas in software, so it works on every PC, virtual machine and remote desktop."))
        return page(f, f2, f3)

    # ------------------------------------------------------------------ theme
    def restyle(self, save=False):
        pt = {"Compact": 9, "Normal": 10, "Large": 12}[self.o_size.currentText()]
        self.pal = apply_theme(self.app, self.o_theme.currentText(), self.qs.value("accent", ""), self.qs.value("accent2", ""), pt)
        if save:
            self.qs.setValue("theme", self.o_theme.currentText()); self.qs.setValue("size", self.o_size.currentText())
        p = self.pal
        c = QColor(p["accent"])
        dark = QColor(p["bg"]).lightness() < 128
        self.chat.set_theme(dict(bg=p["panel"], fg=p["text"], muted=p["muted"], card=p["card"], line=p["border"], accent=p["accent"],
                                 user=f"rgba({c.red()},{c.green()},{c.blue()},.22)"), dark)
        if self.results:
            self.build_plots()
        self.update_report()

    def set_accent(self, col):
        self.qs.setValue("accent", col); self.qs.setValue("accent2", ""); self.restyle()

    def custom_color(self, key):
        c = QColorDialog.getColor(QColor(self.qs.value(key, "#7C6CFF") or "#7C6CFF"), self)
        if c.isValid():
            self.qs.setValue(key, c.name()); self.restyle()

    def reset_accent(self):
        self.qs.setValue("accent", ""); self.qs.setValue("accent2", ""); self.restyle()

    def set_page(self, i):
        self.stack.setCurrentIndex(i); self.tab_btns[i].setChecked(True)
        if i == 3:
            self.refresh_library()

    # ------------------------------------------------------------------ helpers
    @property
    def system(self):
        return SYSTEMS[self.sys_box.currentIndex()]

    def params(self):
        return {k: s.value() for k, s in self.spins.items()}

    def build_form(self):
        self.timer.stop()
        while self.param_lay.count() > 1:
            it = self.param_lay.takeAt(self.param_lay.count() - 1)
            if it.widget():
                it.widget().deleteLater()
        self.spins = {}
        for key, label, d, lo, hi, st, dec in self.system.params:
            self.spins[key] = dspin(d, lo, hi, st, dec)
            self.param_lay.addWidget(row(label, self.spins[key]))
        self.dt.setValue(self.system.default_dt); self.T.setValue(self.system.default_T)

    def on_system(self):
        self.build_form(); self.run(False)

    def apply_preset(self):
        a = PRESETS.get(self.preset.currentText())
        if a:
            self.apply_action(a)
        self.preset.setCurrentIndex(0)

    # ------------------------------------------------------------------ AI hooks (kept compatible with the original app)
    def sim_context(self):
        lines = ["Systems and parameter ranges:"]
        for s in SYSTEMS:
            lines.append(f'- "{s.name}": ' + ", ".join(f"{k} [{lo}..{hi}]" for k, _, _, lo, hi, _, _ in s.params))
        lines.append("Methods: " + ", ".join(f'"{m}"' for m in METHODS))
        lines.append("\nCurrent simulation state:\n" + self.report.toPlainText())
        if self.results:
            m = teacher.measure(self.system, self.params(), self.Yref, self.t)
            lines.append("\nVerified physical quantities: " + json.dumps({k: (round(v, 6) if isinstance(v, float) else v) for k, v in m.items()}))
            lines.append("Events the student sees: " + "; ".join(s for _, s in self.events[:6]))
        return "\n".join(lines)

    def apply_action(self, a):
        """Apply a validated action dict (from the AI, a preset or a lesson). Returns a human-readable summary."""
        if not isinstance(a, dict):
            raise ValueError("action must be an object")
        if a.get("system"):
            want = str(a["system"]).lower()
            idx = next((i for i, s in enumerate(SYSTEMS) if want in s.name.lower() or s.name.lower().startswith(want)), None)
            if idx is None:
                raise ValueError(f"unknown system: {a['system']}")
            if idx != self.sys_box.currentIndex():
                self.sys_box.blockSignals(True); self.sys_box.setCurrentIndex(idx); self.sys_box.blockSignals(False)
                self.build_form()
        if a.get("method"):
            want = str(a["method"]).lower()
            m = next((m for m in METHODS if m.lower().startswith(want.split()[0])), None)
            if m is None:
                raise ValueError(f"unknown method: {a['method']}")
            self.method_box.setCurrentText(m)
        for key, val in (a.get("params") or {}).items():
            if key in self.spins:
                self.spins[key].setValue(float(val))
        if "T" in a:
            self.T.setValue(float(a["T"]))
        if "dt" in a:
            self.dt.setValue(float(a["dt"]))
        if self.T.value() / self.dt.value() > 100000:
            self.dt.setValue(self.T.value() / 100000)
        self.run(bool(a.get("compare")))
        return f"{self.system.name} | {self.method_box.currentText()} | dt={self.dt.value():g} T={self.T.value():g}" + (" | compare all" if a.get("compare") else "")

    # ------------------------------------------------------------------ simulation
    def run(self, compare, user=True):
        sysm, p = self.system, self.params()
        try:
            sysm.y0(p)
        except ValueError as e:
            self.report.setPlainText(str(e)); return
        self.timer.stop()
        dt, T = self.dt.value(), self.T.value()
        n = int(round(T / dt))
        t = np.linspace(0, n * dt, n + 1)
        self.Yref = reference(sysm, p, t)
        self.sel = self.method_box.currentText()
        methods = METHODS if compare else [self.sel]
        self.results = {m: run_method(sysm, p, m, dt, T, self.Yref) for m in methods}
        r = self.results[self.sel]
        self.t, self.compare, self.dt_, self.T_ = r.t, compare, dt, T
        self.P = np.array([sysm.xy(y, p) if np.all(np.isfinite(y)) else np.full_like(sysm.xy(self.Yref[0], p), np.nan) for y in r.Y])
        self.Pref = np.array([sysm.xy(y, p) for y in np.nan_to_num(self.Yref)])
        self.events = teacher.detect_events(sysm, p, self.Yref, self.t)
        self.frame = self.prev_frame = 0
        self.build_plots()
        self.update_report()
        self.timer.start(); self.btn_play.setText("Pause")
        self.run_token += 1
        if user and self.c_watch.isChecked():
            tok = self.run_token
            QTimer.singleShot(1200, lambda: tok == self.run_token and self.auto_comment())

    def build_plots(self):
        colors = mpl_colors(self.pal)
        self.fig.set_facecolor(colors["fig"])
        self.canvas.setStyleSheet(f"background:{colors['fig']}; border-radius:8px;")
        self.plots = LabPlots(self.fig, colors)
        self.plots.build(self.system, self.params(), self.results, self.sel, self.Yref, self.t, self.P, self.Pref)
        self.plots.set_events(self.events, max(1, int(len(self.t) * 0.06)))
        self.plots.set_animated(True)
        self.bg = None
        self.canvas.draw()

    def _relayout(self):
        """Window was resized: switch between the wide and the stacked plot layout when the shape changes."""
        if self.plots is not None and self.plots.ready and LabPlots.mode_for(self.fig) != self.plots.mode:
            self.build_plots(); self.draw_frame()

    def _on_draw(self, ev):
        if self.plots is None or not self.plots.ready:
            return
        self.bg = self.canvas.copy_from_bbox(self.fig.bbox)
        for a in self.plots.animated:
            a.axes.draw_artist(a)

    def draw_frame(self):
        if self.plots is None or not self.plots.ready:
            return
        self.plots.update(self.frame, self.c_caps.isChecked() if hasattr(self, "c_caps") else True)
        if self.bg is None:
            self.canvas.draw(); return
        self.canvas.restore_region(self.bg)
        for a in self.plots.animated:
            a.axes.draw_artist(a)
        self.canvas.blit(self.fig.bbox)

    def set_frame(self, f):
        self.frame = int(max(0, min(len(self.t) - 1, f))); self.prev_frame = self.frame
        self.scrub.setValue(int(1000 * self.frame / max(1, len(self.t) - 1)))
        self.t_label.setText(f"t = {self.t[self.frame]:.2f} s")
        self.draw_frame()

    def on_scrub(self, v):
        self.timer.stop(); self.btn_play.setText("Play")
        self.set_frame(int(v / 1000 * (len(self.t) - 1)))

    def tick(self):
        t0 = time.perf_counter()
        n = len(self.t)
        step = self.speed.value() * max(1, int(getattr(self, "_slow", 1)))
        self.prev_frame = self.frame
        self.frame = (self.frame + step) % n
        self.scrub.blockSignals(True); self.scrub.setValue(int(1000 * self.frame / max(1, n - 1))); self.scrub.blockSignals(False)
        self.t_label.setText(f"t = {self.t[self.frame]:.2f} s")
        self.draw_frame()
        self.narrate()
        self._slow = 1 + int((time.perf_counter() - t0) * 1000 // 45)      # slow PC: skip frames to keep real-time pace

    def narrate(self):
        if not (self.c_speak.isChecked() and self.c_caps.isChecked()) or self.speaker.speaking:
            return
        a, b = (self.prev_frame, self.frame) if self.frame >= self.prev_frame else (-1, self.frame)
        for idx, text in self.events:
            if a < idx <= b:
                self.speaker.speak(text); break

    def toggle(self):
        if self.timer.isActive():
            self.timer.stop(); self.btn_play.setText("Play")
        else:
            self.timer.start(); self.btn_play.setText("Pause")

    def update_report(self):
        if not self.results:
            return
        r = self.results[self.sel]
        key, css = verdict_chip(r.verdict)
        self.verdict.setText(f"{key}  -  {self.sel}"); self.verdict.setStyleSheet(css)
        me = np.nanmax(r.err) if not np.all(np.isnan(r.err)) else float("nan")
        muted = self.pal["muted"]
        tile = lambda v, k: f"<td style='padding:4px 10px'><span style='font-size:15pt;font-weight:700'>{v}</span><br><span style='color:{muted}'>{k}</span></td>"
        self.tiles.setText(f"<table><tr>{tile(f'{r.max_drift:.2e}', 'energy drift')}{tile(f'{me:.2e}', 'state error')}{tile(f'{r.runtime * 1000:.0f} ms', 'runtime')}</tr></table>")
        lines = [f"{self.system.name}", f"dt={self.dt_:g}s  T={self.T_:g}s  steps={len(self.t) - 1}", "Reference: DOP853, rtol=1e-12", "-" * 38]
        for m, rr in self.results.items():
            e = np.nanmax(rr.err) if not np.all(np.isnan(rr.err)) else float("nan")
            lines += [m, f"  max energy drift : {rr.max_drift:.3e}", f"  max state error  : {e:.3e}", f"  runtime          : {rr.runtime * 1000:.0f} ms", f"  VERDICT: {rr.verdict}", ""]
        lines.append("Green line = 1e-6 (trustworthy)\nRed line   = 1e-2 (unreliable)")
        self.report.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------ live teacher
    def level_name(self):
        return self.level.currentText()

    def post(self, md):
        self.chat.teacher(md)

    def act_explain(self):
        self.post(teacher.explain(self.system, self.params(), self.results, self.sel, self.dt_, self.T_, self.Yref, self.t, self.level_name()))

    def auto_comment(self):
        if self.results:
            self.post("*Live comment:* " + teacher.live_comment(self.system, self.params(), self.results, self.sel, self.dt_, self.T_, self.Yref, self.t))

    def act_why(self):
        out = ["### Why does the energy drift?", teacher.method_note(self.sel, self.dt_)]
        if len(self.results) < 2:
            out.append("Press **Compare all methods** to see Euler, RK4 and DOP853 side by side - the difference is the lesson.")
        else:
            for m, r in self.results.items():
                out.append(f"- **{m}**: drift `{teacher.sci(r.max_drift)}` → {r.verdict}")
            out.append("The *energy* of these systems must stay constant in real physics, so any growth is purely a numerical artefact.")
        self.post("\n\n".join(out))

    def act_convergence(self):
        if self.sel.startswith("DOP"):
            self.post(teacher.convergence_text(self.sel, None)); return
        self.post("*Running a convergence experiment (three step sizes)...*")
        sysm, p, sel, dt, T = self.system, self.params(), self.sel, self.dt_, self.T_
        tk = Task(lambda: teacher.convergence_study(sysm, p, sel, dt, T, lambda tt: reference(sysm, p, tt)))
        tk.done.connect(lambda res: self.post(teacher.convergence_text(sel, res)))
        tk.failed.connect(lambda m: self.post(f"The convergence experiment failed: {m}"))
        self._keep(tk); tk.start()

    def _keep(self, t):
        self.tasks = [x for x in self.tasks if x.isRunning()] + [t]

    def act_lesson(self):
        steps = teacher.lesson_plan(self.system.name)
        i = self.lesson_i.get(self.system.name, 0) % len(steps)
        title, text, action = steps[i]
        self.lesson_i[self.system.name] = i + 1
        self.post(f"### Lesson step {i + 1} of {len(steps)}: {title}\n\n{text}")
        self.apply_action(action)

    def act_suggest(self):
        r = self.results[self.sel]
        if not self.compare:
            s = "Run **Compare all methods** - seeing Euler, RK4 and DOP853 together is the quickest way to learn which one to trust."
        elif r.max_drift > 1e-2:
            s = f"{self.sel} is unreliable here. Halve `dt` (to {self.dt_ / 2:g}) and compare again - how much does the drift fall?"
        else:
            s = "Everything is accurate at this step. Make it harder: raise the amplitude / eccentricity / energy, or lengthen the run, and see which method breaks first."
        steps = teacher.lesson_plan(self.system.name)
        self.post(f"### Experiment idea\n{s}\n\nOr press **Next lesson step** for a guided path ({len(steps)} steps for this system).")

    def act_ai(self):
        if not self.chat.ai_ready:
            self.post("The AI teacher needs an API key - use **API key...** below. Meanwhile I'm answering offline."); self.act_explain(); return
        self.ask("Explain what I'm seeing in this simulation, using the verified numbers.")

    def ask(self, text):
        self.chat.say("user", text); self.chat.render()
        self.handle_user_text(text)

    def handle_user_text(self, text):
        if self.chat.ai_ready:
            self.chat.ask_ai(text, self.sim_context(), teacher.PERSONA[self.level_name()], on_fail=lambda m: self.offline_reply(text))
        else:
            self.offline_reply(text)

    def offline_reply(self, text):
        t = text.lower()
        if re.search(r"\b(quiz|test me|question)", t):
            self.act_quiz(); self.post("Quiz started - answer in the **Live teacher** tab."); return
        if re.search(r"converg|order of|how accurate", t):
            self.act_convergence(); return
        if re.search(r"next (step|lesson)|lesson|teach me", t):
            self.act_lesson(); return
        if re.search(r"suggest|what should i try|idea", t):
            self.act_suggest(); return
        if re.search(r"why.*(drift|euler|energy|wrong|blow)|drift|euler|rk4|dop|integrator|method", t):
            self.act_why(); return
        if re.search(r"compare", t):
            self.apply_action({"compare": True}); return
        m = re.findall(r"([a-z0-9]+)\s*=\s*(-?\d+\.?\d*)", t)
        keys = {k.lower(): k for k in self.spins}
        ps = {keys[k]: float(v) for k, v in m if k in keys}
        sysname = next((s.name for s in SYSTEMS if s.name.split()[0].lower() in t or s.name.split()[0].lower()[:5] in t), None)
        if ps or (re.search(r"\b(run|simulate|set up|show)\b", t) and sysname):
            a = dict(params=ps)
            if sysname:
                a["system"] = sysname
            self.post("Setting that up: " + self.apply_action(a)); return
        if re.search(r"explain|what|see|happen|mean|how|period|orbit|chaos|why", t) or True:
            self.act_explain()

    def on_assistant_said(self, text):
        if self.c_speak.isChecked():
            self.speaker.speak(text)

    # ---- quiz
    def act_quiz(self):
        self.quiz = teacher.make_quiz(self.results, self.sel, seed=int(time.time()) % 7)
        self.quiz_i, self.score = 0, [0, 0]
        self.show_q()

    def show_q(self):
        q = self.quiz[self.quiz_i]
        self.q_text.setText(f"**Question {self.quiz_i + 1} of {len(self.quiz)}**\n\n" + q["q"].replace("*", ""))
        self.q_text.setTextFormat(Qt.PlainText)
        self.q_text.setText(f"Question {self.quiz_i + 1} of {len(self.quiz)}\n{q['q'].replace('*', '')}")
        for i, b in enumerate(self.q_btns):
            b.setVisible(i < len(q["options"])); b.setEnabled(True)
            if i < len(q["options"]):
                b.setText(q["options"][i])
        self.q_fb.setText(""); self.q_next.setVisible(False)
        self.q_score.setText(f"Score: {self.score[0]} / {self.score[1]}")

    def answer(self, i):
        q = self.quiz[self.quiz_i]
        ok = i == q["answer"]
        self.score[0] += ok; self.score[1] += 1
        for b in self.q_btns:
            b.setEnabled(False)
        self.q_fb.setText(("Correct! " if ok else f"Not quite - the answer is: {q['options'][q['answer']]}. ") + q["why"].replace("*", ""))
        self.q_score.setText(f"Score: {self.score[0]} / {self.score[1]}")
        last = self.quiz_i == len(self.quiz) - 1
        self.q_next.setText("Finish" if last else "Next question"); self.q_next.setVisible(True)

    def next_q(self):
        if self.quiz_i == len(self.quiz) - 1:
            self.post(f"### Quiz finished\nYou scored **{self.score[0]} / {self.score[1]}**. " + ("Excellent!" if self.score[0] == self.score[1] else "Run the experiment again and watch the numbers - then retry."))
            self.q_next.setVisible(False); return
        self.quiz_i += 1; self.show_q()

    def act_diag(self):
        self.chat.view.diagnostics(lambda text, h: self.post("```\n" + text + "\n```\n" + ("**The renderer is healthy.**" if h.get("ok") else "**The rich renderer is not running** - the simple view is in use. Set Appearance > Chat rendering to *Compatible* and restart.")))

    # ------------------------------------------------------------------ saving
    def state(self):
        return dict(system=self.system, p=self.params(), results=self.results, sel=self.sel, Yref=self.Yref, t=self.t, P=self.P, Pref=self.Pref,
                    dt=self.dt_, T=self.T_, events=self.events, hold=max(1, int(len(self.t) * 0.06)))

    def colors(self):
        return mpl_colors(self.pal)

    def say_saved(self, path, what="Saved"):
        self.export_status.setText(f"{what}: {path}")
        self.chat.say("system", f"{what}: {path}"); self.chat.render()

    def ask_path(self, title, name, filters):
        path, flt = QFileDialog.getSaveFileName(self, title, exporter.default_name(name, filters.split("(*.")[1].split(")")[0].split(" ")[0]), filters)
        return path

    def save_image(self):
        p = self.ask_path("Save plot", self.system.name, "PNG image (*.png);;SVG vector (*.svg);;PDF (*.pdf)")
        if p:
            self._guard(lambda: self.say_saved(exporter.save_figure(p, self.state(), self.colors(), self.frame)))

    def copy_plot(self):
        QApplication.clipboard().setPixmap(self.canvas.grab()); self.export_status.setText("Plot copied to the clipboard.")

    def save_chat_pdf(self):
        p = self.ask_path("Save teacher chat", "teacher_chat", "PDF (*.pdf)")
        if not p:
            return
        if not hasattr(self.chat.view, "save_pdf"):
            self.post("Chat PDF needs the rich renderer. Use **Create report** instead - it includes the conversation."); return
        self.chat.view.save_pdf(p, lambda path, ok: self.say_saved(path) if ok else self.export_status.setText("Could not save the chat PDF."))

    def save_visuals(self):
        def got(items):
            if not items:
                QMessageBox.information(self, "SciSketch", "No AI illustrations, diagrams or simulations in the chat yet."); return
            folder = QFileDialog.getExistingDirectory(self, "Save visuals into folder", str(exporter.notebook_dir()))
            if not folder:
                return
            for i, it in enumerate(items, 1):
                ext = "svg" if it["kind"] == "svg" else "html"
                name = re.sub(r"[^A-Za-z0-9]+", "_", it["title"]).strip("_")
                open(os.path.join(folder, f"{i:02d}_{name}.{ext}"), "w", encoding="utf-8").write(it["data"])
            self.say_saved(folder, f"Saved {len(items)} visual(s) to")
        self.chat.view.collect_widgets(got)

    def save_video(self):
        if not self.results:
            return
        gif = self.v_fmt.currentIndex() == 1
        p = self.ask_path("Render video", self.system.name, "GIF (*.gif)" if gif else "MP4 video (*.mp4)")
        if not p:
            return
        w, h = [int(x) for x in self.v_res.currentText().split(" ")[0::2][:2]]
        nar = None
        if self.v_nar.isChecked() and not gif:
            nar = teacher.explain(self.system, self.params(), self.results, self.sel, self.dt_, self.T_, self.Yref, self.t, self.level_name())
        self.video = VideoTask(self.state(), self.colors(), p, size=(w, h), fps=int(self.v_fps.currentText()), seconds=self.v_len.value(), narration=nar)
        self.video.progress.connect(lambda f: self.v_bar.setValue(int(f * 100)))
        self.video.done.connect(lambda n: (self._video_end(), self.say_saved(p, f"Video saved ({n})")))
        self.video.failed.connect(lambda m: (self._video_end(), self.export_status.setText(f"Video: {m}")))
        self.btn_video.setEnabled(False); self.btn_vcancel.setEnabled(True); self.v_bar.setValue(0)
        self.video.start()

    def _video_end(self):
        self.btn_video.setEnabled(True); self.btn_vcancel.setEnabled(False)

    def save_report(self):
        fmts = [k for k, c in self.d_fmt.items() if c.isChecked()]
        if not fmts or not self.results:
            self.export_status.setText("Tick at least one document format."); return
        folder = str(exporter.notebook_dir())
        base = os.path.splitext(os.path.basename(exporter.default_name(self.system.name, "x")))[0]
        expl = teacher.explain(self.system, self.params(), self.results, self.sel, self.dt_, self.T_, self.Yref, self.t, self.level_name())
        notes = self.d_notes.text().strip() + (f"  Quiz score: {self.score[0]}/{self.score[1]}." if self.score[1] else "")
        rep = exporter.make_report(self.state(), expl, self.chat.history, self.d_chat.isChecked(), self.colors(), notes.strip())
        done = []
        for k in fmts:
            self._guard(lambda k=k: done.append(exporter.SAVERS[k](rep, os.path.join(folder, f"{base}.{k}"))))
        if done:
            self.say_saved(", ".join(os.path.basename(d) for d in done) + f"  (in {folder})", "Report created")

    def save_session(self):
        p = self.ask_path("Save lab session", self.system.name, "SciSketch session (*.sciproj)")
        if p:
            self._guard(lambda: self.say_saved(exporter.save_session(p, dict(
                system=self.system.name, method=self.sel, dt=self.dt_, T=self.T_, params=self.params(), compare=self.compare,
                level=self.level_name(), chat=self.chat.history))))

    def open_session(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open lab session", str(exporter.notebook_dir()), "SciSketch session (*.sciproj *.json)")
        if not p:
            return
        try:
            d = exporter.load_session(p)
            self.chat.history = [m for m in d.get("chat", []) if m.get("role") in ("user", "assistant", "system")]
            self.level.setCurrentText(d.get("level", "Undergraduate"))
            self.apply_action(dict(system=d["system"], method=d.get("method"), dt=d["dt"], T=d["T"], params=d["params"], compare=d.get("compare", False)))
            self.chat.render(); self.say_saved(p, "Opened session")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "SciSketch", f"Could not open the session:\n{e}")

    def export_csv(self):
        if not self.results:
            return
        p = self.ask_path("Export CSV", self.system.name, "CSV (*.csv)")
        if not p:
            return
        r = self.results[self.sel]; n = r.Y.shape[1]
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["t"] + [f"y{k}" for k in range(n)] + [f"ref_y{k}" for k in range(n)] + ["energy", "energy_drift", "state_error"])
            for k in range(len(r.t)):
                w.writerow([r.t[k], *r.Y[k], *self.Yref[k], r.E[k], r.drift[k], r.err[k]])
        self.say_saved(p)

    def _guard(self, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self.export_status.setText(f"Could not save: {e}")
            QMessageBox.warning(self, "SciSketch", f"Could not save:\n{e}")

    def refresh_library(self):
        self.lib.clear()
        files = sorted((p for p in exporter.notebook_dir().iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)[:300]
        kinds = {".png": "Image", ".svg": "Image", ".mp4": "Video", ".gif": "Video", ".docx": "Document", ".pdf": "Document", ".html": "Document",
                 ".md": "Document", ".sciproj": "Session", ".csv": "Data"}
        for p in files:
            it = QListWidgetItem(f"{kinds.get(p.suffix.lower(), 'File'):9s}  {p.name}   ({p.stat().st_size // 1024} KB)")
            it.setData(Qt.UserRole, str(p)); self.lib.addItem(it)

    def closeEvent(self, e):
        self.timer.stop(); self.speaker.stop()
        if self.video and self.video.isRunning():
            self.video.stop = True; self.video.wait(4000)
        super().closeEvent(e)


def selftest():
    """Headless smoke test used by CI and the packaged build: simulation, offline renderer, teacher, saving."""
    import tempfile
    app = QApplication(sys.argv)
    w = MainWindow(app); w.show()
    out = {"simulation": bool(w.results) and bool(np.isfinite(w.results[w.sel].max_drift))}
    try:
        out["teacher"] = len(teacher.explain(w.system, w.params(), w.results, w.sel, w.dt_, w.T_, w.Yref, w.t)) > 300
        d = tempfile.mkdtemp()
        exporter.save_figure(os.path.join(d, "t.png"), w.state(), w.colors(), size=(640, 360))
        out["export_image"] = os.path.getsize(os.path.join(d, "t.png")) > 5000
        rep = exporter.make_report(w.state(), "### x\ntext", [], False, w.colors())
        exporter.save_docx(rep, os.path.join(d, "t.docx")); exporter.save_pdf(rep, os.path.join(d, "t.pdf"))
        out["export_docs"] = os.path.getsize(os.path.join(d, "t.docx")) > 5000 and os.path.getsize(os.path.join(d, "t.pdf")) > 5000
        if exporter.find_ffmpeg():
            small = w.state()
            exporter.render_video(small, w.colors(), os.path.join(d, "t.mp4"), size=(320, 180), fps=8, seconds=1)
            out["export_video"] = os.path.getsize(os.path.join(d, "t.mp4")) > 1000
        else:
            out["export_video"] = False
    except Exception as e:  # noqa: BLE001
        out["_error"] = str(e)
    view = getattr(w.chat, "view", None)

    def finish():
        print("SELFTEST", json.dumps(out), flush=True)
        if os.environ.get("SCISKETCH_SELFTEST_OUT"):
            open(os.environ["SCISKETCH_SELFTEST_OUT"], "w", encoding="utf-8").write(json.dumps(out))
        app.exit(0 if all(v for k, v in out.items() if not k.startswith("_")) else 1)

    if view is None or not hasattr(view, "page"):
        out["offline_renderer"] = False
        QTimer.singleShot(0, finish)
    else:
        view.set_messages([{"role": "assistant", "content": "$E=mc^2$\n```mermaid\ngraph TD; A-->B\n```"}], False)
        tries = {"n": 0}
        probe = "JSON.stringify({katex: document.querySelectorAll('.katex').length, svg: document.querySelectorAll('.widget svg').length})"

        def poll():
            def got(r):
                try:
                    d = json.loads(r)
                except (TypeError, ValueError):
                    d = {}
                tries["n"] += 1
                if (d.get("katex") and d.get("svg")) or tries["n"] >= 25:
                    out["offline_renderer"] = bool(d.get("katex") and d.get("svg"))
                    out["_detail"] = {"loaded": view._ready, **d}
                    finish()
                else:
                    QTimer.singleShot(1000, poll)
            view.page().runJavaScript(probe, got)
        QTimer.singleShot(2000, poll)
    sys.exit(app.exec())


def main():
    import renderer
    mode = QSettings("SciSketch", "SciSketch").value("render", "compatible")
    renderer.configure_chromium("compatible" if "--selftest" in sys.argv else mode)
    if "--selftest" in sys.argv:
        selftest()
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SciSketch.Lab")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("SciSketch"); app.setOrganizationName("SciSketch")
    icon = resource_path("assets/scisketch.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    w = MainWindow(app)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
