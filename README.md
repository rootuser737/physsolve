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
