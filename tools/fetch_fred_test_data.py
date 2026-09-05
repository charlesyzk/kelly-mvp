"""Download and convert a fixed FRED S&P 500 snapshot for local testing."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


SOURCE_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv"
    "?id=SP500&cosd=2016-09-01&coed=2026-09-04"
)
OUTPUT_NAME = "SP500_FRED.csv"


def download_source() -> bytes:
    request = Request(
        SOURCE_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Safari/537.36"
            )
        },
    )
    for attempt in range(2):
        try:
            with urlopen(request, timeout=12) as response:
                return response.read()
        except (ConnectionError, TimeoutError, URLError):
            if attempt < 1:
                time.sleep(attempt + 1)
    # FRED occasionally resets Python TLS connections while accepting curl.
    completed = subprocess.run(
        [
            "curl",
            "-L",
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "3",
            "--connect-timeout",
            "10",
            "--max-time",
            "45",
            SOURCE_URL,
        ],
        check=True,
        stdout=subprocess.PIPE,
    )
    return completed.stdout


def main(source_file: str | None = None) -> None:
    source_bytes = Path(source_file).read_bytes() if source_file else download_source()
    source_text = source_bytes.decode("utf-8-sig")
    converted: list[tuple[str, str, str]] = []
    for row in csv.DictReader(io.StringIO(source_text)):
        observed = (row.get("observation_date") or "").strip()
        value = (row.get("SP500") or "").strip()
        if observed and value not in {"", "."}:
            float(value)
            converted.append((observed, "SP500_FRED", value))
    if len(converted) < 1_500:
        raise RuntimeError(f"unexpectedly short FRED response: {len(converted)} rows")

    target_dir = Path(__file__).resolve().parents[1] / "test"
    target_dir.mkdir(parents=True, exist_ok=True)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("date", "symbol", "adjusted_close"))
    writer.writerows(converted)
    output_bytes = output.getvalue().encode("utf-8")
    data_path = target_dir / OUTPUT_NAME
    data_path.write_bytes(output_bytes)

    metadata = {
        "dataset": "S&P 500 daily close",
        "symbol_in_file": "SP500_FRED",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_series": "SP500",
        "source_url": SOURCE_URL,
        "source_note": "Price index, daily close, excludes dividends.",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "first_date": converted[0][0],
        "last_date": converted[-1][0],
        "rows": len(converted),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "converted_sha256": hashlib.sha256(output_bytes).hexdigest(),
    }
    metadata_path = target_dir / "SP500_FRED.metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(data_path)
    print(metadata_path)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-file",
        help="convert an already downloaded FRED CSV instead of downloading again",
    )
    arguments = parser.parse_args()
    main(arguments.source_file)
