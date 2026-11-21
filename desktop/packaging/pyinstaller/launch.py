"""Frozen-app entry point. PyInstaller needs a plain script, not a package
``__main__`` with relative imports.

``Mistcoat protect ...`` (or any other CLI command) runs headless: Qt is
never imported and no window opens. Anything else starts the app.
"""
import sys

from mistcoat.cli import is_cli_invocation

if __name__ == "__main__":
    if is_cli_invocation(sys.argv):
        from mistcoat.cli import main
        sys.exit(main(sys.argv[1:]))
    from mistcoat.gui.app import run
    sys.exit(run())
