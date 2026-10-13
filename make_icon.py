"""Generate the SciSketch app icon (PNG + ICO) with Qt. Run: python make_icon.py"""
import os, sys, math
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QBrush
from PySide6.QtCore import QPointF, QRectF, Qt

app = QGuiApplication(sys.argv)
S = 512
img = QImage(S, S, QImage.Format.Format_ARGB32)
img.fill(Qt.transparent)
p = QPainter(img); p.setRenderHint(QPainter.Antialiasing)
g = QLinearGradient(0, 0, S, S); g.setColorAt(0, QColor("#1d4ed8")); g.setColorAt(1, QColor("#7c3aed"))
p.setBrush(QBrush(g)); p.setPen(Qt.NoPen); p.drawRoundedRect(QRectF(16, 16, S - 32, S - 32), 96, 96)
# orbit ellipse + sine wave + pendulum
p.setPen(QPen(QColor(255, 255, 255, 90), 10)); p.setBrush(Qt.NoBrush); p.drawEllipse(QRectF(70, 150, 372, 212))
path = QPainterPath(); path.moveTo(70, 330)
for x in range(70, 443, 4):
    path.lineTo(x, 330 - 70 * math.sin((x - 70) / 372 * 2 * math.pi * 1.5))
p.setPen(QPen(QColor("#fde68a"), 14, Qt.SolidLine, Qt.RoundCap)); p.drawPath(path)
p.setPen(QPen(QColor("white"), 10, Qt.SolidLine, Qt.RoundCap)); p.drawLine(QPointF(256, 80), QPointF(330, 200))
p.setBrush(QColor("white")); p.setPen(Qt.NoPen); p.drawEllipse(QPointF(330, 200), 28, 28)
p.drawEllipse(QPointF(256, 80), 10, 10); p.end()
img.save("assets/scisketch.png")
img.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation).save("assets/scisketch.ico")
print("icon written")
