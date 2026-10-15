"""Physics core (no GUI): systems, solvers and the verification against a high-precision reference."""
import time
from dataclasses import dataclass

import numpy as np
from scipy.integrate import solve_ivp

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
