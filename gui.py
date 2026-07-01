"""
OS Agent — GUI launcher.

Runs the agent with a system-tray icon and details window. The console entry
point (`python main.py`) still works unchanged.
"""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="os_agent_gui",
        description="OS Agent with system tray GUI",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the agent in dry-run mode (no actions executed).",
    )
    args = parser.parse_args()

    try:
        from src.gui.tray import run
    except ImportError as e:
        print(f"GUI unavailable (missing PyQt6?): {e}", file=sys.stderr)
        print("Install with: pip install PyQt6", file=sys.stderr)
        return 1

    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
