"""Print the NEPSE watch dashboard to stdout.

Usage:  python scripts/nepse_dashboard.py [YYYY-MM-DD]
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(r"C:\Users\Dell\Desktop\fuse\A-Multi-Agent-Trading-Research-Desk")
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env", override=True)

from argos.reports import nepse_watch_dashboard


def main():
    as_of = sys.argv[1] if len(sys.argv) > 1 else None
    print(nepse_watch_dashboard(as_of=as_of))


if __name__ == "__main__":
    main()
