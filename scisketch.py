"""SciSketch - a trust-checked physics & science simulator with an offline AI tutor.

PROBLEM: most educational/hobby simulators use naive integrators (Euler) that
silently violate conservation laws, so the animation looks plausible but is
physically wrong. SciSketch runs every system against a high-precision
reference solution, measures energy drift and trajectory error, and gives a
verdict on whether the simulation can be trusted.
"""
import csv
import json
import sys
import time
from dataclasses import dataclass

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QMainWindow, QPlainTextEdit, QPushButton, QSlider, QSplitter,
    QVBoxLayout, QWidget,
)
from scipy.integrate import solve_ivp

from paths import resource_path

# --------------------------------------------------------------------------
# Physical systems (all conservative, so energy is a built-in correctness test)
# param spec: (key, label, default, lo, hi, step, decimals)
# --------------------------------------------------------------------------


class System:
    name = ""
    params = []
    phase = (0, 1)          # state indices for the phase plot
    phase_labels = ("", "")
    default_T = 20.0
    default_dt = 0.002

    def y0(self, p): raise NotImplementedError
    def rhs(self, t, y, p): raise NotImplementedError
    def energy(self, y, p): raise NotImplementedError
    def xy(self, y, p): raise NotImplementedError   # (k,2) points; last = traced


class Pendulum(System):
    name = "Nonlinear pendulum"
    params = [("L", "Length L (m)", 1.0, 0.1, 10, 0.1, 2),
              ("g", "Gravity g (m/s²)", 9.81, 0.1, 30, 0.1, 2),
              ("th0", "Release angle (deg)", 120, 1, 179, 1, 1)]
    phase = (0, 1)
    phase_labels = ("θ (rad)", "ω (rad/s)")

    def y0(self, p): return np.array([np.radians(p["th0"]), 0.0])

    def rhs(self, t, y, p): return np.array([y[1], -p["g"] / p["L"] * np.sin(y[0])])

    def energy(self, y, p):
        return 0.5 * p["L"] ** 2 * y[..., 1] ** 2 + p["g"] * p["L"] * (1 - np.cos(y[..., 0]))

    def xy(self, y, p):
        return np.array([[0, 0], [p["L"] * np.sin(y[0]), -p["L"] * np.cos(y[0])]])


class DoublePendulum(System):
    name = "Double pendulum (chaotic)"
    params = [("L1", "Length L1 (m)", 1.0, 0.1, 5, 0.1, 2),
              ("L2", "Length L2 (m)", 1.0, 0.1, 5, 0.1, 2),
              ("m1", "Mass m1 (kg)", 1.0, 0.1, 10, 0.1, 2),
              ("m2", "Mass m2 (kg)", 1.0, 0.1, 10, 0.1, 2),
              ("g", "Gravity g (m/s²)", 9.81, 0.1, 30, 0.1, 2),
              ("a1", "Angle 1 (deg)", 120, -179, 179, 1, 1),
              ("a2", "Angle 2 (deg)", -10, -179, 179, 1, 1)]
    phase = (0, 2)
    phase_labels = ("θ1 (rad)", "ω1 (rad/s)")
    default_T = 15.0

    def y0(self, p): return np.array([np.radians(p["a1"]), np.radians(p["a2"]), 0.0, 0.0])

    def rhs(self, t, y, p):
        t1, t2, w1, w2 = y
        L1, L2, m1, m2, g = p["L1"], p["L2"], p["m1"], p["m2"], p["g"]
        d = t1 - t2
        den = 2 * m1 + m2 - m2 * np.cos(2 * d)
        a1 = (-g * (2 * m1 + m2) * np.sin(t1) - m2 * g * np.sin(t1 - 2 * t2)
              - 2 * np.sin(d) * m2 * (w2 ** 2 * L2 + w1 ** 2 * L1 * np.cos(d))) / (L1 * den)
        a2 = (2 * np.sin(d) * (w1 ** 2 * L1 * (m1 + m2) + g * (m1 + m2) * np.cos(t1)
              + w2 ** 2 * L2 * m2 * np.cos(d))) / (L2 * den)
        return np.array([w1, w2, a1, a2])

    def energy(self, y, p):
        L1, L2, m1, m2, g = p["L1"], p["L2"], p["m1"], p["m2"], p["g"]
        t1, t2, w1, w2 = y[..., 0], y[..., 1], y[..., 2], y[..., 3]
        T = (0.5 * m1 * (L1 * w1) ** 2
             + 0.5 * m2 * ((L1 * w1) ** 2 + (L2 * w2) ** 2 + 2 * L1 * L2 * w1 * w2 * np.cos(t1 - t2)))
        V = -(m1 + m2) * g * L1 * np.cos(t1) - m2 * g * L2 * np.cos(t2)
        return T + V

    def xy(self, y, p):
        x1, y1 = p["L1"] * np.sin(y[0]), -p["L1"] * np.cos(y[0])
        return np.array([[0, 0], [x1, y1], [x1 + p["L2"] * np.sin(y[1]), y1 - p["L2"] * np.cos(y[1])]])


class Kepler(System):
    name = "Kepler orbit (two-body)"
    params = [("e", "Eccentricity e", 0.6, 0.0, 0.95, 0.05, 2)]
    phase = (0, 2)
    phase_labels = ("x", "vx")
    default_T = 20.0

    def y0(self, p):
        e = p["e"]
        return np.array([1 - e, 0.0, 0.0, np.sqrt((1 + e) / (1 - e))])

    def rhs(self, t, y, p):
        r3 = (y[0] ** 2 + y[1] ** 2) ** 1.5
        return np.array([y[2], y[3], -y[0] / r3, -y[1] / r3])

    def energy(self, y, p):
        r = np.hypot(y[..., 0], y[..., 1])
        return 0.5 * (y[..., 2] ** 2 + y[..., 3] ** 2) - 1.0 / r

    def xy(self, y, p): return np.array([[y[0], y[1]]])


class HenonHeiles(System):
    name = "Hénon–Heiles (stellar orbit)"
    params = [("E", "Energy E", 0.125, 0.01, 0.16, 0.005, 3),
              ("y0", "Initial y", 0.1, -0.5, 0.5, 0.05, 2),
              ("px", "Initial px", 0.1, -0.5, 0.5, 0.05, 2)]
    phase = (0, 2)
    phase_labels = ("x", "px")
    default_T = 60.0
    default_dt = 0.01

    def y0(self, p):
        V = 0.5 * p["y0"] ** 2 - p["y0"] ** 3 / 3
        py2 = 2 * (p["E"] - V) - p["px"] ** 2
        if py2 < 0:
            raise ValueError("Initial conditions exceed the chosen energy; lower |y| or |px|.")
        return np.array([0.0, p["y0"], p["px"], np.sqrt(py2)])

    def rhs(self, t, y, p):
        x, yy = y[0], y[1]
        return np.array([y[2], y[3], -x - 2 * x * yy, -yy - x ** 2 + yy ** 2])

    def energy(self, y, p):
        x, yy = y[..., 0], y[..., 1]
        return 0.5 * (y[..., 2] ** 2 + y[..., 3] ** 2) + 0.5 * (x ** 2 + yy ** 2) + x ** 2 * yy - yy ** 3 / 3

    def xy(self, y, p): return np.array([[y[0], y[1]]])


SYSTEMS = [Pendulum(), DoublePendulum(), Kepler(), HenonHeiles()]
METHODS = ["Euler (naive)", "RK4 (fixed step)", "DOP853 (adaptive)"]
COLORS = {"Euler (naive)": "#d62728", "RK4 (fixed step)": "#ff7f0e", "DOP853 (adaptive)": "#2ca02c"}

# --------------------------------------------------------------------------
# Solvers + verification
# --------------------------------------------------------------------------


def integrate(system, p, method, dt, T):
    n = int(round(T / dt))
    t = np.linspace(0.0, n * dt, n + 1)
    y0 = system.y0(p)
    f = lambda tt, yy: system.rhs(tt, yy, p)
    Y = np.full((n + 1, len(y0)), np.nan)
    if method.startswith("DOP853"):
        s = solve_ivp(f, (0, t[-1]), y0, method="DOP853", t_eval=t, rtol=1e-10, atol=1e-12)
        Y[:s.y.shape[1]] = s.y.T
        return t, Y
    Y[0] = y0
    y = y0.copy()
    with np.errstate(all="ignore"):
        for i in range(n):
            if method.startswith("Euler"):
                y = y + dt * f(t[i], y)
            else:
                k1 = f(t[i], y); k2 = f(t[i], y + dt / 2 * k1)
                k3 = f(t[i], y + dt / 2 * k2); k4 = f(t[i], y + dt * k3)
                y = y + dt / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
            if not np.all(np.isfinite(y)) or np.max(np.abs(y)) > 1e8:
                break
            Y[i + 1] = y
    return t, Y


def reference(system, p, t):
    y0 = system.y0(p)
    s = solve_ivp(lambda tt, yy: system.rhs(tt, yy, p), (0, t[-1]), y0, method="DOP853",
                  t_eval=t, rtol=1e-12, atol=1e-13)
    Y = np.full((len(t), len(y0)), np.nan)
    Y[:s.y.shape[1]] = s.y.T
    return Y


@dataclass
class Result:
    method: str
    t: np.ndarray
    Y: np.ndarray
    E: np.ndarray
    drift: np.ndarray       # |E-E0| / max(|E0|,1)
    err: np.ndarray         # ||Y - Yref||
    runtime: float

    @property
    def max_drift(self): return np.nan if np.all(np.isnan(self.drift)) else np.nanmax(self.drift)
    @property
    def diverged(self): return bool(np.isnan(self.Y[-1]).any())

    @property
    def verdict(self):
        if self.diverged: return "UNRELIABLE (solution blew up)"
        d = self.max_drift
        if d < 1e-6: return "TRUSTWORTHY"
        if d < 1e-2: return "USABLE (visible numerical error)"
        return "UNRELIABLE (energy not conserved)"


def run_method(system, p, method, dt, T, Yref):
    t0 = time.perf_counter()
    t, Y = integrate(system, p, method, dt, T)
    rt = time.perf_counter() - t0
    E = system.energy(Y, p)
    E0 = system.energy(system.y0(p), p)
    drift = np.abs(E - E0) / max(abs(E0), 1.0)
    err = np.linalg.norm(Y - Yref, axis=1)
    return Result(method, t, Y, E, drift, err, rt)


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SciSketch - verified physics & science simulator")
        self.resize(1400, 850)
        self.spins = {}
        self.results = {}
        self.Yref = None
        self.P = self.Pref = None
        self.sel = None
        self.frame = 0
        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self.tick)

        # ---- controls
        panel = QWidget()
        pl = QVBoxLayout(panel)
        self.sys_box = QComboBox(); self.sys_box.addItems([s.name for s in SYSTEMS])
        self.method_box = QComboBox(); self.method_box.addItems(METHODS)
        self.dt = self._spin(0.002, 1e-5, 0.5, 0.001, 5)
        self.T = self._spin(20, 0.5, 500, 1, 1)
        top = QFormLayout()
        top.addRow("System", self.sys_box)
        top.addRow("Method", self.method_box)
        top.addRow("Time step dt (s)", self.dt)
        top.addRow("Duration T (s)", self.T)
        pl.addLayout(top)
        pl.addWidget(QLabel("<b>Parameters</b>"))
        self.form = QFormLayout()
        pl.addLayout(self.form)
        self.btn_run = QPushButton("Run simulation")
        self.btn_cmp = QPushButton("Compare all methods")
        self.btn_play = QPushButton("Pause")
        self.btn_csv = QPushButton("Export CSV")
        for b in (self.btn_run, self.btn_cmp, self.btn_play, self.btn_csv):
            pl.addWidget(b)
        pl.addWidget(QLabel("Playback speed"))
        self.speed = QSlider(Qt.Horizontal); self.speed.setRange(1, 40); self.speed.setValue(6)
        pl.addWidget(self.speed)
        self.report = QPlainTextEdit(readOnly=True)
        self.report.setStyleSheet("font-family: monospace; font-size: 12px;")
        pl.addWidget(self.report, 1)
        panel.setMaximumWidth(380)

        # ---- plots
        self.fig = Figure(constrained_layout=True)
        self.canvas = FigureCanvasQTAgg(self.fig)
        gs = self.fig.add_gridspec(2, 2, width_ratios=[1.25, 1])
        self.ax_a = self.fig.add_subplot(gs[:, 0])
        self.ax_e = self.fig.add_subplot(gs[0, 1])
        self.ax_p = self.fig.add_subplot(gs[1, 1])

        split = QSplitter()
        split.addWidget(panel); split.addWidget(self.canvas)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        self.sys_box.currentIndexChanged.connect(self.on_system)
        self.btn_run.clicked.connect(lambda: self.run(False))
        self.btn_cmp.clicked.connect(lambda: self.run(True))
        self.btn_play.clicked.connect(self.toggle)
        self.btn_csv.clicked.connect(self.export)
        self.on_system()
        try:
            from aichat import ChatDock
            self.chat = ChatDock(self)
            self.addDockWidget(Qt.RightDockWidgetArea, self.chat)
        except ImportError:
            pass

    # -- helpers
    @staticmethod
    def _spin(val, lo, hi, step, dec):
        s = QDoubleSpinBox(); s.setDecimals(dec); s.setRange(lo, hi); s.setSingleStep(step); s.setValue(val)
        return s

    @property
    def system(self): return SYSTEMS[self.sys_box.currentIndex()]

    def build_form(self):
        self.timer.stop()
        while self.form.rowCount():
            self.form.removeRow(0)
        self.spins = {}
        for key, label, d, lo, hi, st, dec in self.system.params:
            self.spins[key] = self._spin(d, lo, hi, st, dec)
            self.form.addRow(label, self.spins[key])
        self.dt.setValue(self.system.default_dt)
        self.T.setValue(self.system.default_T)

    def on_system(self):
        self.build_form()
        self.run(False)

    # -- AI hooks -----------------------------------------------------------
    def sim_context(self):
        lines = ["Systems and parameter ranges:"]
        for s in SYSTEMS:
            ps = ", ".join(f"{k} [{lo}..{hi}]" for k, _, _, lo, hi, _, _ in s.params)
            lines.append(f'- "{s.name}": {ps}')
        lines.append("Methods: " + ", ".join(f'"{m}"' for m in METHODS))
        lines.append("\nCurrent simulation state:\n" + self.report.toPlainText())
        return "\n".join(lines)

    def apply_action(self, a):
        """Apply a validated AI action dict. Returns a human-readable summary."""
        if not isinstance(a, dict):
            raise ValueError("action must be an object")
        if a.get("system"):
            want = str(a["system"]).lower()
            idx = next((i for i, s in enumerate(SYSTEMS)
                        if want in s.name.lower() or s.name.lower().startswith(want)), None)
            if idx is None:
                raise ValueError(f"unknown system: {a['system']}")
            if idx != self.sys_box.currentIndex():
                self.sys_box.blockSignals(True)
                self.sys_box.setCurrentIndex(idx)
                self.sys_box.blockSignals(False)
                self.build_form()
        if a.get("method"):
            want = str(a["method"]).lower()
            m = next((m for m in METHODS if m.lower().startswith(want.split()[0])), None)
            if m is None:
                raise ValueError(f"unknown method: {a['method']}")
            self.method_box.setCurrentText(m)
        for key, val in (a.get("params") or {}).items():
            if key in self.spins:
                self.spins[key].setValue(float(val))      # spin box clamps to its range
        if "T" in a:
            self.T.setValue(float(a["T"]))
        if "dt" in a:
            self.dt.setValue(float(a["dt"]))
        if self.T.value() / self.dt.value() > 100000:     # keep runs responsive
            self.dt.setValue(self.T.value() / 100000)
        self.run(bool(a.get("compare")))
        return f"{self.system.name} | {self.method_box.currentText()} | dt={self.dt.value():g} T={self.T.value():g}" \
               + (" | compare all" if a.get("compare") else "")

    def params(self): return {k: s.value() for k, s in self.spins.items()}

    # -- simulation
    def run(self, compare):
        sysm, p = self.system, self.params()
        try:
            sysm.y0(p)
        except ValueError as e:
            self.report.setPlainText(str(e)); return
        self.timer.stop()
        dt, T = self.dt.value(), self.T.value()
        t = np.linspace(0, int(round(T / dt)) * dt, int(round(T / dt)) + 1)
        self.Yref = reference(sysm, p, t)
        self.sel = self.method_box.currentText()
        methods = METHODS if compare else [self.sel]
        self.results = {m: run_method(sysm, p, m, dt, T, self.Yref) for m in methods}
        r = self.results[self.sel]
        self.t = r.t
        self.P = np.array([sysm.xy(y, p) if np.all(np.isfinite(y)) else np.full_like(sysm.xy(self.Yref[0], p), np.nan)
                           for y in r.Y])
        self.Pref = np.array([sysm.xy(y, p) for y in np.nan_to_num(self.Yref)])
        self.frame = 0
        self.build_plots()
        self.write_report(dt, T)
        self.timer.start()
        self.btn_play.setText("Pause")

    def build_plots(self):
        sysm = self.system
        for ax in (self.ax_a, self.ax_e, self.ax_p):
            ax.clear()
        a = self.ax_a
        ok = self.Pref[np.isfinite(self.Pref).all(axis=(1, 2))]
        lo, hi = ok.reshape(-1, 2).min(0), ok.reshape(-1, 2).max(0)
        pad = 0.15 * max(hi - lo) + 1e-6
        a.set_xlim(lo[0] - pad, hi[0] + pad); a.set_ylim(lo[1] - pad, hi[1] + pad)
        a.set_aspect("equal"); a.grid(alpha=0.3)
        a.set_title(sysm.name + "  (hollow = reference truth)")
        a.plot(self.Pref[:, -1, 0], self.Pref[:, -1, 1], color="0.8", lw=0.8)
        col = COLORS[self.sel]
        (self.l_links,) = a.plot([], [], "-o", color="k", lw=2)
        (self.l_trail,) = a.plot([], [], color=col, lw=1)
        (self.l_dot,) = a.plot([], [], "o", color=col, ms=9)
        (self.l_ref,) = a.plot([], [], "o", mfc="none", mec="k", ms=13)
        if sysm.name.startswith("Kepler"):
            a.plot([0], [0], "o", color="gold", ms=12)
        self.txt = a.text(0.02, 0.97, "", transform=a.transAxes, va="top", family="monospace")

        e = self.ax_e
        e.set_title("Energy drift  |ΔE| / max(|E₀|,1)"); e.set_yscale("log"); e.grid(alpha=0.3, which="both")
        e.set_xlabel("t (s)")
        for m, res in self.results.items():
            e.plot(res.t, np.maximum(res.drift, 1e-17), color=COLORS[m], label=m)
        e.axhline(1e-6, color="g", ls=":", lw=1); e.axhline(1e-2, color="r", ls=":", lw=1)
        e.legend(fontsize=8, loc="lower right")
        e.set_ylim(1e-17, 1e3)
        (self.e_cur,) = e.plot([], [], "k|", ms=18)

        p = self.ax_p
        i, j = sysm.phase
        p.set_title("Phase space"); p.grid(alpha=0.3)
        p.set_xlabel(sysm.phase_labels[0]); p.set_ylabel(sysm.phase_labels[1])
        p.plot(self.Yref[:, i], self.Yref[:, j], color="0.75", lw=0.7)
        r = self.results[self.sel]
        p.plot(r.Y[:, i], r.Y[:, j], color=COLORS[self.sel], lw=0.6, alpha=0.8)
        (self.p_dot,) = p.plot([], [], "ko", ms=6)
        self.canvas.draw_idle()

    def tick(self):
        n = len(self.t)
        self.frame = (self.frame + self.speed.value()) % n
        f = self.frame
        r = self.results[self.sel]
        P = self.P[f]
        self.l_links.set_data(P[:, 0], P[:, 1]) if len(P) > 1 else self.l_links.set_data([], [])
        tr = self.P[max(0, f - 400):f + 1, -1]
        self.l_trail.set_data(tr[:, 0], tr[:, 1])
        self.l_dot.set_data([P[-1, 0]], [P[-1, 1]])
        self.l_ref.set_data([self.Pref[f, -1, 0]], [self.Pref[f, -1, 1]])
        self.txt.set_text(f"t = {self.t[f]:6.2f} s\nE-drift = {r.drift[f]:.2e}\nstate err = {r.err[f]:.2e}")
        self.e_cur.set_data([self.t[f]], [1e-16])
        i, j = self.system.phase
        self.p_dot.set_data([r.Y[f, i]], [r.Y[f, j]])
        self.canvas.draw_idle()

    def toggle(self):
        if self.timer.isActive():
            self.timer.stop(); self.btn_play.setText("Play")
        else:
            self.timer.start(); self.btn_play.setText("Pause")

    def write_report(self, dt, T):
        lines = [f"{self.system.name}", f"dt={dt:g}s  T={T:g}s  steps={len(self.t) - 1}",
                 "Reference: DOP853, rtol=1e-12", "-" * 38]
        for m, r in self.results.items():
            me = np.nanmax(r.err) if not np.all(np.isnan(r.err)) else float("nan")
            lines += [m, f"  max energy drift : {r.max_drift:.3e}", f"  max state error  : {me:.3e}",
                      f"  runtime          : {r.runtime * 1000:.0f} ms", f"  VERDICT: {r.verdict}", ""]
        lines.append("Green line = 1e-6 (trustworthy)\nRed line   = 1e-2 (unreliable)")
        self.report.setPlainText("\n".join(lines))

    def export(self):
        if not self.results:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "simulation.csv", "CSV (*.csv)")
        if not path:
            return
        r = self.results[self.sel]
        n = r.Y.shape[1]
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["t"] + [f"y{k}" for k in range(n)] + [f"ref_y{k}" for k in range(n)]
                       + ["energy", "energy_drift", "state_error"])
            for k in range(len(r.t)):
                w.writerow([r.t[k], *r.Y[k], *self.Yref[k], r.E[k], r.drift[k], r.err[k]])


def selftest():
    """Headless smoke test used by CI and the packaged build: simulation + offline renderer."""
    app = QApplication(sys.argv)
    w = MainWindow(); w.show()
    out = {"simulation": bool(w.results) and bool(np.isfinite(w.results[w.sel].max_drift))}
    view = getattr(getattr(w, "chat", None), "view", None)

    def finish():
        print("SELFTEST", json.dumps(out), flush=True)
        app.exit(0 if all(v for k, v in out.items() if not k.startswith("_")) else 1)

    if view is None or not hasattr(view, "page"):
        out["offline_renderer"] = False
        QTimer.singleShot(0, finish)
    else:
        view.set_messages([{"role": "assistant",
                            "content": "$E=mc^2$\n```mermaid\ngraph TD; A-->B\n```"}], False)
        tries = {"n": 0}
        probe = ("JSON.stringify({katex: document.querySelectorAll('.katex').length, "
                 "svg: document.querySelectorAll('.widget svg').length})")

        def poll():                                  # the frozen build can take a few seconds to start Chromium
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
    if "--selftest" in sys.argv:
        selftest()
    app = QApplication(sys.argv)
    app.setApplicationName("SciSketch")
    app.setOrganizationName("SciSketch")
    icon = resource_path("assets/scisketch.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
