"""Entry point of mistcoat-cli.exe, the Windows console companion.

The main Mistcoat.exe is a windowed program: perfect for running invisibly
from other software, but a terminal does not wait for it. This console
build behaves like any command-line tool (the terminal waits, output appears
in order) and leaves out Qt, so it is smaller and starts faster.
"""
import sys

from mistcoat.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
