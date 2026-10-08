#!/usr/bin/env python3
"""Run the existing 1111 worker command in an explicitly selected environment."""
import os
from pathlib import Path
import sys


def main():
    args = sys.argv[1:]
    if len(args) < 5 or args[:2] != ["run", "-p"] or args[3] != "python":
        raise SystemExit("Expected: worker_python.py run -p /environment python script [arguments]")
    python = Path(args[2]).resolve() / "bin/python"
    if not python.is_file():
        raise SystemExit(f"Environment Python missing: {python}")
    os.execv(str(python), [str(python), *args[4:]])


if __name__ == "__main__":
    main()
