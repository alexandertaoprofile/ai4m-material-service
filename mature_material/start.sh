#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# main.py loads .env itself. Run in the foreground so systemd, Docker, or the
# caller owns lifecycle and logs instead of leaving an untracked nohup process.
exec python main.py
