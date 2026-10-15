import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import teacher
from systems import SYSTEMS, METHODS, reference, run_method

DEF = lambda s: {k: d for k, _, d, *_ in s.params}


def sim(system, p, dt=0.002, T=20, methods=METHODS):
    t = np.linspace(0, int(round(T / dt)) * dt, int(round(T / dt)) + 1)
    Yref = reference(system, p, t)
    return t, Yref, {m: run_method(system, p, m, dt, T, Yref) for m in methods}


def test_all():
    pend, dbl, kep, hen = SYSTEMS
    # ---- verification ranking: Euler worst, DOP853 best, verdict labels sensible
    p = DEF(pend); t, Yref, R = sim(pend, p, 0.01, 20)
    d = {m: R[m].max_drift for m in R}
    assert d["Euler (naive)"] > d["RK4 (fixed step)"] > d["DOP853 (adaptive)"], d
    assert R["Euler (naive)"].verdict.startswith("UNRELIABLE") and R["DOP853 (adaptive)"].verdict == "TRUSTWORTHY", (R["Euler (naive)"].verdict, d)
    print("verification ranking ok", {k: f"{v:.1e}" for k, v in d.items()})

    # ---- teacher numbers vs the reference (pendulum exact period, Kepler laws)
    m = teacher.measure(pend, p, Yref, t)
    assert abs(m["Tmeas"] - m["Texact"]) / m["Texact"] < 2e-3, m
    assert m["Texact"] > m["T0"]                       # large amplitude => slower than small-angle formula
    pk = dict(e=0.6); tk, Yk, Rk = sim(kep, pk, 0.002, 20)
    mk = teacher.measure(kep, pk, Yk, tk)
    assert abs(mk["rmin"] - 0.4) < 1e-3 and abs(mk["rmax"] - 1.6) < 1e-3, mk
    assert abs(mk["E"] - float(kep.energy(Yk[0], pk))) < 1e-9, mk                  # E = -1/(2a) with a = 1
    h = Yk[:, 0] * Yk[:, 3] - Yk[:, 1] * Yk[:, 2]
    assert abs(h[-1] - mk["h"]) < 1e-6, (h[-1], mk["h"])                           # angular momentum conserved
    print("teacher physics ok", f"T={m['Tmeas']:.4f} vs exact {m['Texact']:.4f}")

    # ---- convergence orders (the teacher's headline experiment)
    ref = lambda tt: reference(pend, dict(p, th0=40), tt)
    c1 = teacher.convergence_study(pend, dict(p, th0=40), "Euler (naive)", 0.02, 10, ref)   # starts in the nonlinear regime on purpose
    c4 = teacher.convergence_study(pend, dict(p, th0=40), "RK4 (fixed step)", 0.2, 10, ref)
    assert c1["refined"], c1
    p0 = dict(p, th0=120); cr = teacher.convergence_study(pend, p0, "RK4 (fixed step)", 0.002, 20, lambda tt: reference(pend, p0, tt))
    assert cr["refined"] and 3.3 < cr["order"] < 4.7, cr        # default dt sits at the round-off floor: must be coarsened, not mis-measured
    assert 0.8 < c1["order"] < 1.3, c1
    assert 3.3 < c4["order"] < 4.7, c4
    assert teacher.convergence_study(pend, p, "DOP853 (adaptive)", 0.01, 5, ref) is None
    assert "measured order" in teacher.convergence_text("Euler (naive)", c1)
    print("convergence ok: Euler order %.2f, RK4 order %.2f" % (c1["order"], c4["order"]))

    # ---- explanations for every system x level, quiz, events, live comment
    for s in SYSTEMS:
        ps = DEF(s); dt, T = s.default_dt, min(s.default_T, 12)
        tt, Yr, Rs = sim(s, ps, max(dt, 0.005), T)
        for lvl in teacher.LEVELS:
            md = teacher.explain(s, ps, Rs, "Euler (naive)", dt, T, Yr, tt, lvl)
            assert len(md) > 300 and "nan" not in md.lower().replace("n/a", "") and "TRUSTWORTHY" in md or "UNRELIABLE" in md or "USABLE" in md, (s.name, lvl)
        ev = teacher.detect_events(s, ps, Yr, tt)
        assert ev and ev[0][0] == 0 and all(0 <= i < len(tt) for i, _ in ev), (s.name, ev[:3])
        assert teacher.lesson_plan(s.name) and teacher.live_comment(s, ps, Rs, "RK4 (fixed step)", dt, T, Yr, tt)
        assert teacher.live_comment(s, ps, {"RK4 (fixed step)": Rs["RK4 (fixed step)"]}, "RK4 (fixed step)", dt, T, Yr, tt)
    q = teacher.make_quiz(R, "Euler (naive)")
    assert len(q) == 3 and q[0]["options"][q[0]["answer"]] == "DOP853 (adaptive)", q[0]
    assert all(0 <= x["answer"] < len(x["options"]) for x in q)
    print("explain/events/quiz ok for", len(SYSTEMS), "systems x", len(teacher.LEVELS), "levels")
    print("CORE OK")


if __name__ == "__main__":
    test_all()
