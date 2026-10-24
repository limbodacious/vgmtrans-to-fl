"""Where the app keeps its folders (rips, input, output, template, data).

Next to the scripts when run from source, next to the .exe when run as the packaged app.
"""
import os
import sys

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
