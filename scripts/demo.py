"""
Run the pipeline on the reference scenarios without QGIS.

    python scripts/demo.py             # all scenarios
    python scripts/demo.py rbl         # one of: rbl, priority, bent, signal
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yield_pipeline import run, summarise            # noqa: E402
from tests.fixtures import make_junction             # noqa: E402

ALL_12 = [(a, b) for a in "NESW" for b in "NESW" if a != b]

SCENARIOS = {
    "rbl": ("Unsigned 4-way: right-before-left",
            make_junction(ALL_12)),
    "priority": ("DE 306 east-west, DE 205 north-south",
                 make_junction(ALL_12, {"E": ["de306"], "W": ["de306"],
                                        "N": ["de205"], "S": ["de205"]})),
    "bent": ("DE 306 + plate 1002-10: priority road turns south-west",
             make_junction(ALL_12, {"S": ["de306", "1002-10"],
                                    "W": ["de306", "1002-10"],
                                    "N": ["de205", "1002-10"],
                                    "E": ["de205", "1002-10"]})),
    "signal": ("Signalised, two phases, crossings on every arm",
               make_junction(ALL_12, signalised=True, crossings=("N", "S", "E", "W"))),
}


def main(which=None):
    for key, (title, features) in SCENARIOS.items():
        if which and key != which:
            continue
        print("=" * 72)
        print(f"{key.upper()}  -  {title}")
        print("=" * 72)
        result, cells, lines = run(features)
        print(summarise(result))
        print(f"\n  {len(lines)} yield line(s) would be drawn.\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
