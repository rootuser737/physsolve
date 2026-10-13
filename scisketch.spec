# PyInstaller spec for SciSketch. Build with:  pyinstaller scisketch.spec --noconfirm
import sys
block_cipher = None
a = Analysis(
    ["scisketch.py"],
    pathex=["."],
    datas=[("web", "web"), ("assets", "assets"), ("config.example.json", ".")],
    hiddenimports=["scipy.integrate", "scipy.integrate._ivp.dop853_coefficients"],
    excludes=["tkinter", "matplotlib.backends.backend_tkagg", "matplotlib.backends.backend_gtk3agg",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
              "PySide6.QtCharts", "PySide6.QtDataVisualization", "IPython", "pytest", "PIL.ImageTk"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SciSketch", console=False,
          icon="assets/scisketch.ico" if sys.platform.startswith("win") else None)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="SciSketch")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="SciSketch.app", icon="assets/scisketch.png", bundle_identifier="app.scisketch.desktop")
