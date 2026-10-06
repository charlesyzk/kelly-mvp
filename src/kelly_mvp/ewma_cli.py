"""Command-line entry point for the independent A-share EWMA strategy."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

from .data import load_daily_prices
from .module_strategy.ewma_refresh_strategy import run_ewma_refresh_backtest


def _write_csv(path: Path, rows: object) -> None:
    materialized = list(rows)  # type: ignore[arg-type]
    if not materialized:
        path.write_text("", encoding="utf-8")
        return
    flat: list[dict[str, object]] = []
    for row in materialized:
        values = asdict(row) if is_dataclass(row) else dict(row)
        flat.append({key: json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
                     if isinstance(value, (dict, tuple, list)) else value for key, value in values.items()})
    fields = list(dict.fromkeys(key for row in flat for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flat)


def write_ewma_outputs(result: object, output: str | Path) -> list[Path]:
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=False)
    data = result.public_dict()  # type: ignore[attr-defined]
    (destination / "run.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    paths = [destination / "run.json"]
    for name, rows in (
        ("features.csv", result.features),
        ("events.csv", result.events),
        ("fixed_path_labels.csv", result.fixed_labels),
        ("fixed_horizon_outcomes.csv", result.horizon_outcomes),
        ("fixed_horizon_summaries.csv", result.horizon_summaries),
        ("path_quantiles_21d.csv", result.path_quantiles),
        ("path_drawdowns_21d.csv", result.path_drawdowns),
        ("direction_summaries.csv", result.direction_summaries),
        ("calibrations.csv", result.calibrations),
        ("exits.csv", result.exit_results),
        ("approvals.csv", result.approvals),
        ("signals.csv", result.signals),
        ("trades.csv", result.trades),
        ("ledger.csv", result.ledger),
        ("summaries.csv", result.summaries),
    ):
        path = destination / name
        _write_csv(path, rows)
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent A-share EWMA strategy research runner")
    parser.add_argument("--input", required=True, help="daily CSV with date,symbol,adjusted_close,open,high,low,close")
    parser.add_argument("--output", required=True, help="new output directory; existing directories are not overwritten")
    args = parser.parse_args(argv)
    try:
        rows = load_daily_prices(args.input)
        result = run_ewma_refresh_backtest(rows)
        outputs = write_ewma_outputs(result, args.output)
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        print(f"EWMA research error: {exc}")
        return 2
    print(f"Completed {result.symbol}: {len(result.events)} retained state events; {len(result.approvals)} approval rows")
    print(f"Split: {result.split_method}; test starts at row {result.test_start_index}")
    for path in outputs:
        print(path.resolve())
    for issue in result.issues:
        print(f"Note: {issue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
