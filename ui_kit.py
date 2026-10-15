"""Small reusable widgets (same look as VoxCut): cards, rows, sliders, scroll pages."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QSlider,
                               QVBoxLayout, QWidget)


def card(title=None, subtitle=None):
    f = QFrame(); f.setObjectName("card")
    lay = QVBoxLayout(f); lay.setContentsMargins(18, 16, 18, 18); lay.setSpacing(11)
    if title:
        t = QLabel(title); t.setObjectName("h"); lay.addWidget(t)
    if subtitle:
        s = QLabel(subtitle); s.setObjectName("muted"); s.setWordWrap(True); lay.addWidget(s)
    return f, lay


def note(text):
    l = QLabel(text); l.setObjectName("muted"); l.setWordWrap(True); return l


def row(label, widget, tip=None, label_w=112):
    w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(10)
    lab = QLabel(label); lab.setMinimumWidth(label_w); lab.setMaximumWidth(label_w + 24); lab.setWordWrap(True)
    h.addWidget(lab); h.addWidget(widget, 1)
    if tip:
        w.setToolTip(tip)
    return w


def slider(lo, hi, val, fmt=str):
    s = QSlider(Qt.Horizontal); s.setRange(lo, hi); s.setValue(val)
    lab = QLabel(); lab.setMinimumWidth(62); lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    s.valueChanged.connect(lambda v: lab.setText(fmt(v))); lab.setText(fmt(val))
    w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.addWidget(s, 1); h.addWidget(lab)
    return w, s


def combo(items, current=None):
    c = QComboBox(); c.addItems(list(items))
    if current:
        c.setCurrentText(current)
    return c


def dspin(val, lo, hi, step, dec=2, suffix=""):
    s = QDoubleSpinBox(); s.setDecimals(dec); s.setRange(lo, hi); s.setSingleStep(step); s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    return s


def page(*cards):
    inner = QWidget(); lay = QVBoxLayout(inner); lay.setContentsMargins(0, 0, 8, 0); lay.setSpacing(12)
    for c in cards:
        lay.addWidget(c)
    lay.addStretch(1)
    sa = QScrollArea(); sa.setWidgetResizable(True); sa.setFrameShape(QFrame.NoFrame); sa.setWidget(inner)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return sa
