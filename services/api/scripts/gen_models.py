"""Generate Pydantic models from the OpenAPI contract (ADR-0006).

Usage:
    python scripts/gen_models.py          # write app/contracts/models.py
    python scripts/gen_models.py --check  # fail if the committed file is out of date
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
REPO = API_DIR.parents[1]
SPEC = REPO / "packages" / "contracts" / "openapi.yaml"
OUT = API_DIR / "app" / "contracts" / "models.py"


def generate(target: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(SPEC),
            "--input-file-type",
            "openapi",
            "--output",
            str(target),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.12",
            "--use-standard-collections",
            "--use-union-operator",
            "--field-constraints",
            "--enum-field-as-literal",
            "one",
            "--disable-timestamp",
            "--use-schema-description",
        ],
        check=True,
    )
    subprocess.run([sys.executable, "-m", "ruff", "format", "--quiet", str(target)], check=True)


def main() -> int:
    if "--check" in sys.argv:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "models.py"
            generate(fresh)
            if not OUT.exists() or fresh.read_text() != OUT.read_text():
                print("app/contracts/models.py is out of date. Run: python scripts/gen_models.py")
                return 1
        print("Pydantic models match openapi.yaml")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    generate(OUT)
    print(f"Wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
