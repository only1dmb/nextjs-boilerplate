#!/usr/bin/env python3
"""Entry point: python agent/run.py <command>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from robinhood_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
