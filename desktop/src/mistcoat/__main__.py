"""``python -m mistcoat`` opens the app; ``python -m mistcoat protect ...``
(or any other command) runs headless, like the ``mistcoat`` command."""
import sys

from .cli import is_cli_invocation

if __name__ == "__main__":
    if is_cli_invocation(sys.argv):
        from .cli import main
        sys.exit(main(sys.argv[1:]))
    from .gui.app import run
    sys.exit(run())
