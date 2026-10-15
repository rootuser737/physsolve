"""Themed matplotlib views of a simulation. Used by the live window (with blitting) and by the exporters."""
import numpy as np

MCOL = {"Euler (naive)": "#F0506E", "RK4 (fixed step)": "#F5B83D", "DOP853 (adaptive)": "#3FD08A"}


class LabPlots:
    def __init__(self, fig, colors):
        self.fig, self.c = fig, colors
        self.events, self.hold = [], 1
        self.ready = False

    @staticmethod
    def mode_for(fig):
        w, h = fig.get_size_inches()
        return "stacked" if w / max(h, 1e-6) < 1.3 else "wide"

    # ---------------------------------------------------------------- build
    def _style(self, ax):
        c = self.c
        ax.set_facecolor(c["ax"]); ax.tick_params(colors=c["muted"], labelsize=8)
        for s in ax.spines.values():
            s.set_color(c["grid"])
        ax.grid(color=c["grid"], alpha=0.7, lw=0.6)
        ax.xaxis.label.set_color(c["muted"]); ax.yaxis.label.set_color(c["muted"]); ax.title.set_color(c["text"])
        ax.title.set_fontsize(10)

    def build(self, system, p, results, sel, Yref, t, P, Pref):
        self.system, self.p, self.results, self.sel, self.Yref, self.t, self.P, self.Pref = \
            system, p, results, sel, Yref, t, P, Pref
        fig, c = self.fig, self.c
        fig.clear(); fig.set_facecolor(c["fig"])
        self.mode = self.mode_for(fig)
        if self.mode == "wide":
            gs = fig.add_gridspec(2, 2, width_ratios=[1.25, 1])
            self.ax_a, self.ax_e, self.ax_p = fig.add_subplot(gs[:, 0]), fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[1, 1])
        else:                                    # narrow window: big view on top, the two analysis plots side by side below
            gs = fig.add_gridspec(2, 2, height_ratios=[1.7, 1])
            self.ax_a, self.ax_e, self.ax_p = fig.add_subplot(gs[0, :]), fig.add_subplot(gs[1, 0]), fig.add_subplot(gs[1, 1])
        for ax in (self.ax_a, self.ax_e, self.ax_p):
            self._style(ax)
        a = self.ax_a
        ok = Pref[np.isfinite(Pref).all(axis=(1, 2))]
        lo, hi = ok.reshape(-1, 2).min(0), ok.reshape(-1, 2).max(0)
        pad = 0.15 * max(hi - lo) + 1e-6
        a.set_xlim(lo[0] - pad, hi[0] + pad); a.set_ylim(lo[1] - pad, hi[1] + pad); a.set_aspect("equal")
        a.set_title(system.name + "   (hollow ring = reference truth)", fontsize=10)
        a.plot(Pref[:, -1, 0], Pref[:, -1, 1], color=c["grid"], lw=1.0)
        col = MCOL[sel]
        (self.l_links,) = a.plot([], [], "-o", color=c["text"], lw=2, ms=5)
        (self.l_trail,) = a.plot([], [], color=col, lw=1.2)
        (self.l_dot,) = a.plot([], [], "o", color=col, ms=10)
        (self.l_ref,) = a.plot([], [], "o", mfc="none", mec=c["text"], ms=15)
        if system.name.startswith("Kepler"):
            a.plot([0], [0], "o", color="#F5B83D", ms=13)
        self.txt = a.text(0.02, 0.97, "", transform=a.transAxes, va="top", family="monospace", fontsize=9, color=c["text"])
        self.cap = a.text(0.5, 0.025, "", transform=a.transAxes, ha="center", va="bottom", fontsize=10, color=c["text"],
                          wrap=True, bbox=dict(boxstyle="round,pad=0.5", fc=c["fig"], ec=c["accent"], alpha=0.92))
        e = self.ax_e
        e.set_title("Energy drift  |ΔE| / max(|E₀|,1)", fontsize=9); e.set_yscale("log"); e.set_xlabel("t (s)")
        for m, res in results.items():
            e.plot(res.t, np.maximum(res.drift, 1e-17), color=MCOL[m], label=m, lw=1.4)
        e.axhline(1e-6, color="#3FD08A", ls=":", lw=1); e.axhline(1e-2, color="#F0506E", ls=":", lw=1)
        e.set_ylim(1e-17, 1e3)
        lg = e.legend(fontsize=7, loc="lower right", facecolor=c["ax"], edgecolor=c["grid"])
        for tx in lg.get_texts():
            tx.set_color(c["text"])
        (self.e_cur,) = e.plot([], [], "|", color=c["text"], ms=18)
        pp = self.ax_p
        i, j = system.phase
        pp.set_title("Phase space"); pp.set_xlabel(system.phase_labels[0]); pp.set_ylabel(system.phase_labels[1])
        pp.plot(Yref[:, i], Yref[:, j], color=c["grid"], lw=0.9)
        r = results[sel]
        pp.plot(r.Y[:, i], r.Y[:, j], color=col, lw=0.7, alpha=0.85)
        (self.p_dot,) = pp.plot([], [], "o", color=c["text"], ms=6)
        self.animated = [self.l_links, self.l_trail, self.l_dot, self.l_ref, self.txt, self.cap, self.e_cur, self.p_dot]
        self.ready = True

    def set_animated(self, flag):
        for a in self.animated:
            a.set_animated(flag)

    def set_events(self, events, hold_frames):
        self.events, self.hold = events, max(1, int(hold_frames))

    def caption(self, f):
        txt = ""
        for idx, s in self.events:
            if idx <= f < idx + self.hold:
                txt = s
        return txt

    # ---------------------------------------------------------------- per frame
    def update(self, f, show_caption=True):
        r, P = self.results[self.sel], self.P[f]
        if len(P) > 1:
            self.l_links.set_data(P[:, 0], P[:, 1])
        else:
            self.l_links.set_data([], [])
        tr = self.P[max(0, f - 400):f + 1, -1]
        self.l_trail.set_data(tr[:, 0], tr[:, 1])
        self.l_dot.set_data([P[-1, 0]], [P[-1, 1]])
        self.l_ref.set_data([self.Pref[f, -1, 0]], [self.Pref[f, -1, 1]])
        self.txt.set_text(f"t = {self.t[f]:6.2f} s\nE-drift = {r.drift[f]:.2e}\nstate err = {r.err[f]:.2e}")
        self.e_cur.set_data([self.t[f]], [1e-16])
        i, j = self.system.phase
        self.p_dot.set_data([r.Y[f, i]], [r.Y[f, j]])
        cap = self.caption(f) if show_caption else ""
        self.cap.set_text(cap); self.cap.set_visible(bool(cap))
        return self.animated
