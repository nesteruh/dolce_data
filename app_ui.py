#!/usr/bin/env python3
"""Launch the Dolce Data Spotlight-style UI.

Usage:
    python app_ui.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from src.ui import run

if __name__ == "__main__":
    run()
