# -*- coding: utf-8 -*-
"""
Created Date: Sat May 25 03:50:52 UTC 2024

This file is the *single* entry that other modules may import as `team_config`.
To avoid having two diverging implementations (root team_config.py vs src/team_config.py),
we bridge everything to `src.team_config`.

We also keep the original CLI helpers (start/main) for local terminal interaction.
"""

print("[BOOT] team_config loaded from:", __file__)

import os
import sys
from dotenv import load_dotenv

from alpha.team import Team
from alpha.logs import logger

# Always load env
load_dotenv()
server_base = os.getenv("server_base")

# Keep logger behavior same as before
handler = {"sink": sys.stdout, "level": "ERROR"}
logger.configure(handlers=[handler])

# ---- Bridge to the real implementation ----
# Export everything from src.team_config (so old imports still work)
from src.team_config_test import *  # noqa: F401,F403
import src.team_config_test as _impl

print("[BOOT] real implementation:", _impl.__file__)

# Keep explicit symbol import used by your CLI
from src.team_config_test import XIMUAlpha_MNS  # noqa: E402


async def start(
    idea: str = "",
    investment: float = 0,
    n_round: int = 1,
    add_human: bool = True,
):
    team = Team()
    team.hire([XIMUAlpha_MNS()])
    team.run_project(idea)
    await team.run(n_round=n_round)


async def main():
    while True:
        userInput = input("\n\n老板，您好：").encode("utf-8").decode("utf-8")
        if userInput in ("结束", "exit"):
            break
        await start(userInput)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
