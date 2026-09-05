"""Start the local browser interface without installing the package."""

from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / "src"))

from kelly_mvp.web import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
