"""Frozen-app entry point. PyInstaller needs a plain script, not a package
``__main__`` with relative imports."""
import sys

from mistcoat.gui.app import run

if __name__ == "__main__":
    sys.exit(run())
