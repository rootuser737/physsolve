# PhysSolve

A trust-checked physics simulator built with PySide6.

**Problem:** most educational simulators use naive integrators (Euler) whose energy
silently drifts, so animations look right but are physically wrong.

**Solution:** every run is compared against a high-precision DOP853 reference
(rtol 1e-12). PhysSolve reports energy drift, trajectory error and a verdict
(TRUSTWORTHY / USABLE / UNRELIABLE). "Compare all methods" overlays Euler, RK4
and DOP853 so the failure of naive methods is visible.

Systems: nonlinear pendulum, chaotic double pendulum, Kepler orbit, Hénon–Heiles.
Hollow marker in the animation = reference truth. CSV export included.

## Run
    pip install -r requirements.txt
    python physsolve.py

## AI tutor (SciSketch)
The right-hand dock chats with your SciSketch AI through its `api-chat` endpoint
(streaming). Ask it to set up simulations ("Kepler orbit, e=0.9, compare methods")
and PhysSolve runs them; its explanations use the verified numbers from the current run.

Setup: copy `config.example.json` to `config.json` and paste your key
(or set the `SCISKETCH_KEY` environment variable). `config.json` is git-ignored.

## Offline rich rendering
Replies from the AI are rendered **on your machine** in an embedded Chromium view (QtWebEngine):
Markdown, LaTeX (KaTeX), Mermaid diagrams, SVG, and interactive p5.js (2D) and three.js (3D) simulations.
All libraries are bundled in `web/vendor`, so rendering needs no internet and puts no load on any server;
only the text of the chat itself travels to your API.

Safety: all network access from the view is blocked, SVG is sanitized, and p5/three code runs in
sandboxed iframes with no access to your files or the network. CDN links in AI-written pages are
swapped for the bundled copies automatically (three.js UMD build r160).
Third-party licenses are in `web/vendor/*-LICENSE`.
