from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_WAREHOUSE = ROOT.parent / "Golf Stats Tracker" / "data" / "golf-lab" / "pga-public-history-2002-2026"


def run_step(label: str, command: list[str]) -> None:
    print(f"\n== {label}")
    print(" ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Golf Lab data and static Pages output.")
    parser.add_argument("--warehouse", type=Path, default=DEFAULT_WAREHOUSE, help="PGA public-history warehouse path.")
    parser.add_argument("--rounds-per-player", type=int, default=240)
    parser.add_argument("--years", default="2023-2026", help="Public PGA TOUR stat years to refresh.")
    parser.add_argument("--skip-import", action="store_true", help="Skip rebuilding scorecards from the warehouse.")
    parser.add_argument("--skip-stats", action="store_true", help="Skip PGA TOUR public stat backfill.")
    parser.add_argument("--skip-export", action="store_true", help="Skip static docs export.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.skip_import:
        warehouse = args.warehouse.resolve()
        if warehouse.exists():
            run_step(
                "Rebuild scorecard warehouse",
                [
                    sys.executable,
                    "golf_lab_import.py",
                    "--from-warehouse",
                    str(warehouse),
                    "--rounds-per-player",
                    str(args.rounds_per_player),
                ],
            )
        else:
            print(f"\n== Rebuild scorecard warehouse\nSkipped: warehouse not found at {warehouse}")

    if not args.skip_stats:
        run_step("Refresh public PGA TOUR stat profiles", [sys.executable, "pga_tour_stats_backfill.py", "--years", args.years])

    if not args.skip_export:
        run_step("Export static GitHub Pages snapshot", [sys.executable, "export_static.py"])


if __name__ == "__main__":
    main()
