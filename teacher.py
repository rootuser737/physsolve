"""Offline teaching engine (no network, no API key). Every number quoted comes from the verified simulation.

explain()            markdown explanation at a chosen level (School / Undergraduate / Advanced)
live_comment()       short auto-comment after each run ("watch my experiment" mode)
detect_events()      moments to narrate while the animation plays (turning points, periapsis, flips ...)
make_quiz()          questions generated from the student's own results
convergence_study()  runs dt, dt/2, dt/4 and measures the method's real order
lesson_plan()        guided experiments with ready-to-apply actions
"""
import math
import random

import numpy as np
from scipy.integrate import solve_ivp
from scipy.special import ellipk

from systems import METHODS, SYSTEMS, run_method

LEVELS = ["School", "Undergraduate", "Advanced"]
_CACHE = {}


def sci(x, d=2):
    return "n/a" if x is None or not np.isfinite(x) else f"{x:.{d}e}"


def g4(x):
    return "n/a" if x is None or not np.isfinite(x) else f"{x:.4g}"


def _crossings(t, y, direction):
    """Times where y crosses 0 (direction -1 = downward, +1 = upward), linearly interpolated."""
    s = np.sign(y)
    idx = np.where((s[:-1] * s[1:] < 0) | (s[:-1] == 0) & (s[1:] != 0))[0]
    out = []
    for i in idx:
        if direction * (y[i + 1] - y[i]) > 0:
            out.append(t[i] - y[i] * (t[i + 1] - t[i]) / (y[i + 1] - y[i]))
    return out


# ------------------------------------------------------------------ measured physics (from the reference)
def measure(system, p, Yref, t):
    n, m = system.name, {}
    if n.startswith("Nonlinear"):
        L, g, th0 = p["L"], p["g"], math.radians(p["th0"])
        m["T0"] = 2 * math.pi * math.sqrt(L / g)
        m["Texact"] = 4 * math.sqrt(L / g) * float(ellipk(math.sin(th0 / 2) ** 2))
        down = _crossings(t, Yref[:, 0], -1)
        m["Tmeas"] = down[1] - down[0] if len(down) >= 2 else None
        m["vmax"] = math.sqrt(2 * g * L * (1 - math.cos(th0)))
    elif n.startswith("Kepler"):
        e = p["e"]
        m.update(rp=1 - e, ra=1 + e, vp=math.sqrt((1 + e) / (1 - e)), va=math.sqrt((1 - e) / (1 + e)),
                 h=math.sqrt(1 - e * e), T=2 * math.pi, E=-0.5)
        r = np.hypot(Yref[:, 0], Yref[:, 1])
        m["rmin"], m["rmax"] = float(np.nanmin(r)), float(np.nanmax(r))
    elif n.startswith("Double"):
        key = (n, tuple(sorted(p.items())), round(float(t[-1]), 3))
        if key not in _CACHE:
            d = 1e-8
            p2 = dict(p, a1=p["a1"] + math.degrees(d))
            Yp = solve_ivp(lambda tt, yy: system.rhs(tt, yy, p2), (0, t[-1]), system.y0(p2), method="DOP853",
                           t_eval=t, rtol=1e-12, atol=1e-13).y.T
            sep = np.linalg.norm(Yref[:len(Yp)] - Yp, axis=1)
            grow = np.where(sep > 1e-3)[0]
            _CACHE[key] = dict(delta=d, sep_end=float(sep[-1]), t_grow=float(t[grow[0]]) if len(grow) else None,
                               lam=math.log(max(sep[-1], d) / d) / float(t[-1]))
        m.update(_CACHE[key])
    else:
        x, px = Yref[:, 0], Yref[:, 2]
        up = _crossings(t, x, +1)
        m["crossings"] = len(up)
        m["E"] = float(system.energy(Yref[0], p))
    return m


def method_note(method, dt):
    if method.startswith("Euler"):
        return (f"Euler is first-order: each step of {dt:g} s takes a straight-line guess along the slope, which for "
                "swinging or orbiting systems pushes the state slightly *outward* every step - so energy creeps up.")
    if method.startswith("RK4"):
        return ("RK4 is fourth-order: it samples the slope four times per step, so its error shrinks like dt⁴. "
                "Far better than Euler, but a long run can still show slow drift.")
    return ("DOP853 is an adaptive 8th-order method: it chooses its own step size to meet a tolerance, which is why it is "
            "used as the trusted reference.")


# ------------------------------------------------------------------ explanations
def _story(system, p, m, level):
    n = system.name
    L = level
    if n.startswith("Nonlinear"):
        base = (f"Released from **{p['th0']:g}°**, the pendulum swings with a measured period of **{g4(m['Tmeas'])} s**."
                if m.get("Tmeas") else f"Released from **{p['th0']:g}°**, one full swing takes about **{g4(m['Texact'])} s**.")
        if L == "School":
            return (base + f" For a tiny swing it would take only {g4(m['T0'])} s - so the bigger you swing, the *slower* it gets. "
                    "At the bottom it moves fastest because all the height energy has turned into speed.")
        s = (base + f"\n\nSmall-angle theory gives $T_0 = 2\\pi\\sqrt{{L/g}} = {g4(m['T0'])}\\,\\text{{s}}$. The exact result is "
             f"$T = 4\\sqrt{{L/g}}\\,K\\!\\left(\\sin^2\\tfrac{{\\theta_0}}{{2}}\\right) = {g4(m['Texact'])}\\,\\text{{s}}$ "
             f"({100 * (m['Texact'] / m['T0'] - 1):+.1f}% vs small-angle). Speed at the bottom: "
             f"$v_{{max}}=\\sqrt{{2gL(1-\\cos\\theta_0)}} = {g4(m['vmax'])}\\,\\text{{m/s}}$.")
        if L == "Advanced":
            s += ("\n\nThe conserved energy $E=\\tfrac12 L^2\\omega^2+gL(1-\\cos\\theta)$ labels each closed curve in phase space; "
                  "the separatrix at $E=2gL$ divides swinging from rotating, and the period diverges logarithmically as $\\theta_0\\to\\pi$.")
        return s
    if n.startswith("Kepler"):
        if L == "School":
            return (f"The planet speeds up when close to the Sun (**{g4(m['vp'])}** at the closest point) and slows down far away "
                    f"(**{g4(m['va'])}** at the farthest). One orbit takes the same time no matter how stretched the ellipse is.")
        s = (f"With $GM=1$ and eccentricity $e={p['e']:g}$: semi-major axis $a=1$, period $T=2\\pi a^{{3/2}}={g4(m['T'])}$, "
             f"energy $E=-\\tfrac{{1}}{{2a}}={m['E']:g}$, angular momentum $h=\\sqrt{{1-e^2}}={g4(m['h'])}$. "
             f"Periapsis $r_p=1-e={g4(m['rp'])}$ (speed {g4(m['vp'])}), apoapsis $r_a=1+e={g4(m['ra'])}$ (speed {g4(m['va'])}). "
             f"Measured from the reference: $r\\in[{g4(m['rmin'])},\\,{g4(m['rmax'])}]$.")
        if L == "Advanced":
            s += (" Kepler's second law ($\\dot A = h/2$) and the Laplace-Runge-Lenz vector are extra conserved quantities - "
                  "which is why a *non-symplectic* integrator makes the orbit slowly precess or spiral.")
        return s
    if n.startswith("Double"):
        gt = f"about **{g4(m['t_grow'])} s**" if m.get("t_grow") else "later than this run"
        if L == "School":
            return ("This double pendulum is **chaotic**: a tiny change at the start leads to a completely different motion later. "
                    f"We nudged the start by a billionth of a radian; the two runs drifted visibly apart after {gt}.")
        s = (f"Two starts differing by only $\\delta={m['delta']:g}$ rad end up $\\Delta\\approx{sci(m['sep_end'])}$ apart "
             f"(visible separation after {gt}). That is exponential growth, $\\Delta\\sim\\delta\\,e^{{\\lambda t}}$, with "
             f"$\\lambda\\approx{g4(m['lam'])}\\,\\text{{s}}^{{-1}}$ - the finite-time Lyapunov exponent.")
        if L == "Advanced":
            s += " Energy is conserved but nothing else is, so a numerical error in the trajectory grows at the same exponential rate: only *energy drift*, not the path, remains checkable."
        return s
    if L == "School":
        return (f"A star moving in a lumpy galaxy potential. Over this run it crossed the $x=0$ line {m['crossings']} times going the same way. "
                "At low energy the path is tidy; at high energy it becomes chaotic.")
    s = (f"Hénon-Heiles: $H=\\tfrac12(p_x^2+p_y^2)+\\tfrac12(x^2+y^2)+x^2y-\\tfrac13y^3$, energy $E={g4(m['E'])}$ "
         f"(escape threshold $E=1/6\\approx0.1667$). The orbit crossed the Poincaré section $x=0,\\,p_x>0$ **{m['crossings']}** times.")
    if L == "Advanced":
        s += " Closed curves of section points mean regular (KAM) motion; scattered points mean chaos - it appears as E approaches 1/6."
    return s




def explain(system, p, results, sel, dt, T, Yref, t, level="Undergraduate"):
    m = measure(system, p, Yref, t)
    out = [f"### Live teacher: {system.name}", _story(system, p, m, level), "#### Can you trust this simulation?"]
    for name, r in results.items():
        tag = "**" + r.verdict + "**"
        out.append(f"- **{name}** - max energy drift `{sci(r.max_drift)}`, max state error `{sci(np.nanmax(r.err) if not np.all(np.isnan(r.err)) else float('nan'))}` → {tag}")
    out.append(method_note(sel, dt))
    if len(results) > 1:
        ok = {k: v.max_drift for k, v in results.items() if not v.diverged and np.isfinite(v.max_drift)}
        if ok:
            best = min(ok, key=ok.get)
            out.append(f"Best energy conservation here: **{best}**. This is the point of the lab: the animations can look alike while the physics is different.")
    out.append("**Try next:** halve `dt` and re-run (does the error fall by 2×, 16×, or not at all?), or press *Check convergence order*.")
    return "\n\n".join(out)


def live_comment(system, p, results, sel, dt, T, Yref, t):
    r = results[sel]
    if len(results) > 1:
        ok = {k: v.max_drift for k, v in results.items() if np.isfinite(v.max_drift)}
        best, worst = min(ok, key=ok.get), max(ok, key=ok.get)
        return (f"Compared all three: **{best}** kept energy best (drift {sci(ok[best])}); **{worst}** was worst ({sci(ok[worst])}). "
                f"Verdict for {sel}: {r.verdict}.")
    m = measure(system, p, Yref, t)
    extra = ""
    if system.name.startswith("Nonlinear") and m.get("Tmeas"):
        extra = f" Period {g4(m['Tmeas'])} s vs {g4(m['T0'])} s for a tiny swing."
    elif system.name.startswith("Kepler"):
        extra = f" Closest approach speed {g4(m['vp'])}, farthest {g4(m['va'])}."
    elif system.name.startswith("Double") and m.get("t_grow"):
        extra = f" A 1e-8 rad nudge becomes visible after {g4(m['t_grow'])} s - chaos."
    elif system.name.startswith("H"):
        extra = f" {m['crossings']} section crossings."
    return f"**{sel}** finished: energy drift {sci(r.max_drift)} → **{r.verdict}**.{extra}"


# ------------------------------------------------------------------ live narration events
def detect_events(system, p, Yref, t, limit=18):
    n, ev = system.name, [(0, "Start - release the system. Watch the hollow ring: it is the reference truth.")]
    Y = np.nan_to_num(Yref)

    def idx(times):
        return [int(np.searchsorted(t, x)) for x in times]
    if n.startswith("Nonlinear"):
        for i in idx(_crossings(t, Y[:, 1], 1) + _crossings(t, Y[:, 1], -1))[:8]:
            ev.append((i, "Turning point: speed is zero, all energy is potential."))
        for i in idx(_crossings(t, Y[:, 0], -1) + _crossings(t, Y[:, 0], 1))[:8]:
            ev.append((i, "Lowest point: fastest speed, energy is all kinetic."))
    elif n.startswith("Kepler"):
        r = np.hypot(Y[:, 0], Y[:, 1])
        for i in range(1, len(r) - 1):
            if r[i] < r[i - 1] and r[i] <= r[i + 1]:
                ev.append((i, f"Periapsis: closest to the Sun (r = {r[i]:.2f}), moving fastest ({np.hypot(Y[i, 2], Y[i, 3]):.2f})."))
            elif r[i] > r[i - 1] and r[i] >= r[i + 1]:
                ev.append((i, f"Apoapsis: farthest (r = {r[i]:.2f}), moving slowest ({np.hypot(Y[i, 2], Y[i, 3]):.2f})."))
    elif n.startswith("Double"):
        th = Y[:, 1]
        k = np.floor((th + math.pi) / (2 * math.pi))
        for i in np.where(np.diff(k) != 0)[0][:8]:
            ev.append((int(i), "The outer arm flips over the top - tiny differences get amplified: chaos."))
    else:
        for j, i in enumerate(idx(_crossings(t, Y[:, 0], 1))[:10]):
            ev.append((i, f"Poincaré section crossing #{j + 1}: the orbit passes x = 0 moving right (y = {Y[i, 1]:+.2f})."))
    ev.sort()
    out, last = [], -10 ** 9
    for i, s in ev:
        if i - last >= max(1, len(t) // 40):
            out.append((i, s)); last = i
    return out[:limit]


# ------------------------------------------------------------------ convergence experiment
def convergence_study(system, p, method, dt, T, Yref_fn):
    """Measure the method's real order. Starts from your dt, refines until the drift is small (asymptotic regime)."""
    if method.startswith("DOP"):
        return None
    T = min(T, 6.0)                                   # a short horizon keeps the experiment fast and clean

    def run(d):
        t = np.linspace(0, int(round(T / d)) * d, int(round(T / d)) + 1)
        r = run_method(system, p, method, d, T, Yref_fn(t))
        return (d, r.max_drift, float(np.nanmax(r.err)))
    CAP = 120000
    rows = [run(dt)]
    for _ in range(10):                      # move into the measurable window: error between 1e-8 and 0.05
        d, _drift, err = rows[0]
        if (not np.isfinite(err)) or err > 0.05:          # too coarse / nonlinear regime -> refine
            if T / (d / 8) > CAP:
                break
            rows = [run(d / 2)]
        elif err < 1e-8 and d * 2 <= T / 20:                # at the round-off floor of the reference -> coarsen
            rows = [run(d * 2)]
        else:
            break
    refined = abs(rows[0][0] - dt) > 1e-15
    d0 = rows[0][0]
    for k in (2, 4):
        if T / (d0 / k) <= CAP:
            rows.append(run(d0 / k))
    orders = []
    for a, b in zip(rows[:-1], rows[1:]):
        ok = a[2] > 1e-14 and b[2] > 1e-14 and np.isfinite(a[2]) and np.isfinite(b[2])     # order = trajectory-error scaling
        orders.append(math.log2(a[2] / b[2]) if ok else None)
    ok = [o for o in orders if o is not None]
    return dict(rows=rows, order=float(np.mean(ok)) if ok else None, T=T, refined=refined, start_dt=dt,
                expected=1.0 if method.startswith("Euler") else 4.0)


def convergence_text(method, res):
    if res is None:
        return "DOP853 is adaptive: its accuracy is set by a tolerance, not by `dt`, so there is no fixed order to measure."
    lines = ["#### Convergence experiment", f"*(analysed on the first {res['T']:g} s)*" +
             (f" - your dt of {res['start_dt']:g} was adjusted to {res['rows'][0][0]:g} so the error is in a range that can be measured cleanly." if res["refined"] else ""),
             "", "| dt | max energy drift | max state error |", "|---|---|---|"]
    lines += [f"| {d:g} | {sci(a)} | {sci(b)} |" for d, a, b in res["rows"]]
    o = res["order"]
    if o is None:
        lines.append("\nThe error is too close to zero to measure an order - the method is already extremely accurate here.")
    else:
        lines.append(f"\nEach halving of dt shrank the **trajectory error** by about **{2 ** o:.1f}×**, i.e. measured order ≈ **{o:.1f}** "
                     f"(theory for {method.split()[0]}: {res['expected']:.0f}). Smaller steps cost more computing but buy real accuracy.")
    return "\n".join(lines)


# ------------------------------------------------------------------ quiz generated from the student's results
def make_quiz(results, sel, conv=None, seed=0):
    rnd, qs = random.Random(seed), []
    ok = {k: v.max_drift for k, v in results.items() if np.isfinite(v.max_drift)}
    if len(ok) >= 2:
        best = min(ok, key=ok.get)
        qs.append(dict(q="Which method conserved energy best in your comparison?", options=list(ok), answer=list(ok).index(best),
                       why=f"{best} had the smallest max energy drift ({sci(ok[best])})."))
    d = results[sel].max_drift
    if np.isfinite(d) and d > 0:
        e = int(math.floor(math.log10(d)))
        opts = sorted({e, e - 2, e + 2, e - 4} if rnd.random() < .5 else {e, e + 1, e - 3, e + 3})
        qs.append(dict(q=f"About how big was the max energy drift for {sel}? (order of magnitude)",
                       options=[f"~1e{x}" for x in opts], answer=opts.index(e),
                       why=f"The measured drift was {sci(d)}, i.e. about 1e{e}."))
    if sel.startswith("Euler"):
        opts, ans, why = ["it halves (÷2)", "it drops 16× (÷16)", "it stays the same", "it doubles"], 0, "Euler is first-order: error ∝ dt."
    elif sel.startswith("RK4"):
        opts, ans, why = ["it halves (÷2)", "it drops 16× (÷16)", "it stays the same", "it doubles"], 1, "RK4 is fourth-order: error ∝ dt⁴, so ÷16."
    else:
        opts, ans, why = ["it halves (÷2)", "it drops 16× (÷16)", "it barely changes - the tolerance sets accuracy", "it doubles"], 2, \
            "DOP853 is adaptive; its accuracy is controlled by tolerance, not by the nominal dt."
    qs.append(dict(q=f"If you halve dt, what happens to the *trajectory error* of {sel}?", options=opts, answer=ans, why=why))
    return qs


# ------------------------------------------------------------------ guided lessons
def lesson_plan(system_name):
    n = system_name
    if n.startswith("Nonlinear"):
        return [("Small swings", "Start with a gentle **10°** swing. For small angles the period barely depends on the angle - Galileo's discovery.",
                 dict(system="Nonlinear pendulum", method="RK4", params={"th0": 10}, T=20, dt=0.002)),
                ("A big swing", "Now release from **150°**. Watch how much slower each swing is than the small-angle formula predicts.",
                 dict(system="Nonlinear pendulum", method="RK4", params={"th0": 150}, T=20, dt=0.002)),
                ("Euler vs the truth", "Same big swing, but with a coarse step. All three methods are shown - which one lies?",
                 dict(system="Nonlinear pendulum", params={"th0": 150}, dt=0.05, T=20, compare=True)),
                ("Halve the step", "Halve `dt`. Predict: how much will Euler's drift fall?",
                 dict(system="Nonlinear pendulum", params={"th0": 150}, dt=0.025, T=20, compare=True))]
    if n.startswith("Kepler"):
        return [("A circular orbit", "With eccentricity **0** the planet moves at constant speed in a circle.",
                 dict(system="Kepler", method="DOP853", params={"e": 0.0}, T=20, dt=0.002)),
                ("An ellipse", "At **e = 0.6** the planet speeds up near the Sun and slows far away (Kepler's 2nd law).",
                 dict(system="Kepler", params={"e": 0.6}, T=20, dt=0.002)),
                ("Extreme orbit", "At **e = 0.9** the orbit is a thin ellipse. Compare the integrators: close passes are hard on Euler.",
                 dict(system="Kepler", params={"e": 0.9}, T=20, dt=0.002, compare=True))]
    if n.startswith("Double"):
        return [("Gentle start", "Small starting angles make the double pendulum behave almost regularly.",
                 dict(system="Double", method="DOP853", params={"a1": 10, "a2": 5}, T=15, dt=0.002)),
                ("Chaos", "Large angles: tiny differences now grow exponentially. Compare how each method copes.",
                 dict(system="Double", params={"a1": 120, "a2": -10}, T=15, dt=0.002, compare=True))]
    return [("Regular motion", "At low energy (**E = 0.05**) the orbit stays tidy.",
             dict(system="Hénon", method="DOP853", params={"E": 0.05}, T=60, dt=0.01)),
            ("Mixed", "At **E = 0.125** regular islands and chaotic seas coexist.",
             dict(system="Hénon", params={"E": 0.125}, T=60, dt=0.01)),
            ("Near escape", "At **E = 0.16**, just below the escape energy, chaos dominates.",
             dict(system="Hénon", params={"E": 0.16}, T=60, dt=0.01, compare=True))]


PERSONA = {
    "School": "Explain like a friendly secondary-school physics teacher: simple words, one idea at a time, everyday analogies, no heavy maths.",
    "Undergraduate": "Explain like a clear university physics tutor: use LaTeX for equations, connect to conservation laws and numerical methods.",
    "Advanced": "Explain like a graduate-level mentor: include Hamiltonian structure, error orders, symplecticity and chaos where relevant.",
}
