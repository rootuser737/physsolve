# SciSketch

A desktop (PySide6) science studio: **verified physics simulation + an offline-rendering AI tutor.**

**Problem:** most educational simulators use naive integrators whose energy silently drifts, so
animations look right while being physically wrong.
**Solution:** every run is checked against a high-precision DOP853 reference (rtol 1e-12). SciSketch
reports energy drift, trajectory error and a verdict (TRUSTWORTHY / USABLE / UNRELIABLE), and
"Compare all methods" overlays Euler, RK4 and DOP853.

- Systems: nonlinear pendulum, chaotic double pendulum, Kepler orbit, Hénon-Heiles
- **AI tutor dock:** talks to your SciSketch AI (`api-chat` endpoint, streaming). Ask it to set up
  simulations ("Kepler orbit, e=0.9, compare methods") and SciSketch runs them; explanations use the
  verified numbers.
- **Offline rendering:** Markdown, LaTeX (KaTeX), Mermaid, SVG, p5.js and three.js all render on your
  machine from bundled libraries (`web/vendor`). No network from the view; p5/three run in sandboxed iframes.
- CSV export, light/dark aware.

## Run from source
    pip install -r requirements.txt
    python scisketch.py

First launch: click **API key...** in the AI dock and paste your key (stored only on this computer in
`~/.scisketch/config.json`, or `%APPDATA%\SciSketch\config.json` on Windows). `SCISKETCH_KEY` env var also works.

## Build the app
    build.bat        # Windows -> dist\SciSketch\SciSketch.exe
    ./build.sh       # Linux/macOS -> dist/SciSketch/
GitHub Actions (`.github/workflows/build.yml`) builds Windows, macOS and Linux on every push and
runs `SciSketch --selftest` (simulation + offline renderer) before uploading the artifacts.

Third-party licenses: `web/vendor/*-LICENSE` (p5.js is LGPL-2.1).
