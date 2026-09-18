"""rdx's own eval harness as a Nightshift score dimension.

Prints one JSON line `{"value": 0-100, ...}` for the generic `cmd` scorer.
The value is the mean of five things the project actually cares about, each
already measured by the offline harness in seconds:

    safety pass rate · poison test · discovery pass rate · gate precision · gate recall

Usage (from discovery/):  ./venv/bin/python -m rdx.loopscore
"""

from __future__ import annotations

import json
import sys

from . import config, db, evalharness


def main() -> int:
    parts: dict[str, float] = {}
    safety = evalharness.run_safety()
    parts["safety"] = safety.passed / max(1, safety.passed + safety.failed)
    poison = evalharness.run_poison_test(db.init_db(":memory:"))
    parts["poison"] = 1.0 if poison.ok else 0.0
    if config.DB_PATH.exists():
        conn = db.open_db(config.DB_PATH, readonly=True)
        disc = evalharness.run_discovery(conn)
        parts["discovery"] = disc.passed / max(1, disc.passed + disc.failed)
        gate = evalharness.run_gate(conn)
        parts["gate_precision"] = float(gate.metrics.get("precision", 0.0))
        parts["gate_recall"] = float(gate.metrics.get("recall", 0.0))
    else:
        parts["discovery"] = 0.0
        parts["gate_precision"] = 0.0
        parts["gate_recall"] = 0.0
    value = 100.0 * sum(parts.values()) / len(parts)
    print(json.dumps({"value": round(value, 3), **{k: round(v, 4) for k, v in parts.items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
