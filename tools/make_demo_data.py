"""Write deterministic synthetic prices for engineering demonstration only."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kelly_mvp.demo import generate_demo_csv  # noqa: E402


def main() -> None:
    target = PROJECT / "examples" / "demo_prices.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(generate_demo_csv(), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
