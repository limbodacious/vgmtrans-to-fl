"""Build the Windows .exe (dist/VGMTrans-to-FL.exe) with PyInstaller.

    .venv\\Scripts\\python -m pip install pyinstaller
    .venv\\Scripts\\python build_exe.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")


def make_icon(path):
    from PySide6.QtWidgets import QApplication
    import app_icon
    QApplication.instance() or QApplication([])
    pm = app_icon._draw(256)
    if not pm.save(path, "ICO"):
        sys.exit("Couldn't write the .ico file")


def main():
    os.makedirs(BUILD, exist_ok=True)
    ico = os.path.join(BUILD, "icon.ico")
    make_icon(ico)
    # Qt parts the app doesn't use; leaving them out keeps the .exe much smaller
    unused = ["QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "Qt3DCore", "Qt3DRender",
              "QtQuick", "QtQml", "QtQuick3D", "QtCharts", "QtDataVisualization", "QtPdf",
              "QtPdfWidgets", "QtSql", "QtTest", "QtBluetooth", "QtNfc", "QtPositioning",
              "QtLocation", "QtSensors", "QtSerialPort", "QtWebSockets", "QtDesigner", "QtHelp",
              "QtOpenGL", "QtOpenGLWidgets", "QtSvg", "QtSvgWidgets", "QtNetworkAuth",
              "QtRemoteObjects", "QtScxml", "QtTextToSpeech", "QtSpatialAudio", "QtGraphs",
              "QtHttpServer", "QtWebChannel", "QtWebView", "QtXml", "QtConcurrent", "QtDBus",
              "QtStateMachine", "QtUiTools", "QtAxContainer", "QtPrintSupport"]
    cmd = [sys.executable, "-m", "PyInstaller", "app.py", "--name", "VGMTrans-to-FL",
           "--onefile", "--windowed", "--noconfirm", "--clean", "--icon", ico,
           "--distpath", os.path.join(HERE, "dist"), "--workpath", BUILD,
           "--specpath", BUILD]
    for mod in unused:
        cmd += ["--exclude-module", f"PySide6.{mod}"]
    subprocess.check_call(cmd, cwd=HERE)
    print("Built", os.path.join(HERE, "dist", "VGMTrans-to-FL.exe"))


if __name__ == "__main__":
    main()
