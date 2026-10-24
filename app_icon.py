"""The app's icon: a white music note on a rounded blue-violet square, drawn in code."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _draw(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256  # draw on a 256-unit canvas

    # background
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor("#7c6cff"))
    grad.setColorAt(1, QColor("#3d7bf5"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 56 * s, 56 * s)

    # eighth note
    p.setBrush(QColor("white"))
    p.save()
    p.translate(104 * s, 178 * s)
    p.rotate(-22)
    p.drawEllipse(QPointF(0, 0), 40 * s, 29 * s)  # head
    p.restore()
    stem_x = 132 * s
    p.drawRoundedRect(QRectF(stem_x, 52 * s, 17 * s, 128 * s), 6 * s, 6 * s)  # stem
    flag = QPainterPath()
    flag.moveTo(stem_x + 8 * s, 52 * s)
    flag.cubicTo(stem_x + 20 * s, 92 * s, stem_x + 82 * s, 92 * s, stem_x + 70 * s, 150 * s)
    flag.cubicTo(stem_x + 66 * s, 116 * s, stem_x + 36 * s, 104 * s, stem_x + 12 * s, 100 * s)
    flag.closeSubpath()
    p.drawPath(flag)
    p.end()
    return pm


def icon():
    ic = QIcon()
    for size in SIZES:
        ic.addPixmap(_draw(size))
    return ic


def set_windows_taskbar_id(app_id="vgmtrans-to-fl.app"):
    """Without this, Windows groups the window under Python and shows Python's icon."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        pass
