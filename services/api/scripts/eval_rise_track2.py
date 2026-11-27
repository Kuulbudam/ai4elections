"""Print the Track 2 scores on the organisers' starter data: python scripts/eval_rise_track2.py [folder]."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation import run  # noqa: E402

folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3] / "data/rise-starter"
print(json.dumps(run(folder), indent=2))
