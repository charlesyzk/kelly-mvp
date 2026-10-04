"""Machine-readable CSV, Excel and plain-text research outputs."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from dataclasses import asdict, fields
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook

from .backtest import BacktestResult, PeriodResult, SignalResult, SummaryResult, TradeResult
from .config import StrategyConfig
from .statistics import ComparisonResult, compare_models


def _write_dataclasses(path: Path, rows: Iterable[object], row_type: type) -> None:
    names = [field.name for field in fields(row_type)]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _sheet(workbook: Workbook, name: str, rows: Iterable[object], row_type: type) -> None:
    sheet = workbook.create_sheet(name)
    names = [field.name for field in fields(row_type)]
    sheet.append(names)
    for row in rows:
        values = []
        for name in names:
            value = getattr(row, name)
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            elif isinstance(value, Mapping):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            values.append(value)
        sheet.append(values)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def _conclusion_text(result: BacktestResult, comparisons: tuple[ComparisonResult, ...], config: StrategyConfig) -> str:
    is_uploaded = bool(result.summaries) and all(
        row.position_type == "TARGET" for row in result.summaries
    )
    if is_uploaded:
        name = result.summaries[0].strategy_name
        lines = [
            f"用户策略样本外验证：{name}",
            "",
            "研究口径",
            f"窗口：日 {config.windows['daily']}、周 {config.windows['weekly']}、月 {config.windows['monthly']}",
            "仓位：上传策略 TARGET，经框架限制到 [-1,1]",
            "Kelly 模型比较、Bootstrap 和 BH-FDR：不适用",
            "交易成本：本阶段不启用",
            "",
            "结果",
            f"逐期评价行数：{len(result.periods)}",
            f"模拟调仓行数：{len(result.trades)}",
        ]
        if result.issues:
            lines.extend(("", "数据和运行问题", *(f"- {issue}" for issue in result.issues)))
        lines.extend(("", "本输出属于研究结果，不构成投资建议。"))
        return "\n".join(lines) + "\n"
    eligible = sum(row.minimum_sample_met for row in comparisons)
    supported = [row for row in comparisons if row.supported_at_05]
    exploratory = [row for row in comparisons if row.exploratory_at_10]
    lines = [
        "六模型动态 Kelly 样本外验证",
        "",
        "研究口径",
        f"窗口：日 {config.windows['daily']}、周 {config.windows['weekly']}、月 {config.windows['monthly']}",
        "仓位：RAW、BOUNDED、SAFE；不使用 Half Kelly",
        f"SAFE 参数：kappa={config.convergence_kappa:g}；财富下限={config.wealth_floor:g}",
        "交易成本：本阶段不启用",
        "正式判定：5% BH-FDR，同时要求相对 M2 均值为正、相对买入持有 95% CI 下限不低于 0、达到最低样本数",
        "",
        "结果",
        f"逐期评价行数：{len(result.periods)}",
        f"模拟调仓行数：{len(result.trades)}",
        f"候选比较行数：{len(comparisons)}",
        f"达到最低样本数：{eligible}",
        f"5% 正式支持：{len(supported)}",
        f"10% 探索性支持：{len(exploratory)}",
    ]
    for row in supported:
        lines.append(f"支持：{row.symbol}/{row.frequency}/{row.model_id}/{row.position_type}")
    if not supported:
        lines.append("本次运行没有候选组满足全部正式判定条件。")
    if result.issues:
        lines.extend(("", "数据和运行问题", *(f"- {issue}" for issue in result.issues)))
    lines.extend(("", "本输出属于研究结果，不构成投资建议。"))
    return "\n".join(lines) + "\n"


def write_outputs(
    result: BacktestResult,
    output_dir: str | Path,
    config: StrategyConfig | None = None,
) -> tuple[Path, ...]:
    active = config or StrategyConfig()
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    comparisons = compare_models(result, active)
    summary = target / "summary.csv"
    periods = target / "periods.csv"
    signals = target / "signals.csv"
    statistics = target / "statistics.csv"
    trades = target / "trades.csv"
    workbook_path = target / "results.xlsx"
    conclusion = target / "conclusion.txt"
    _write_dataclasses(summary, result.summaries, SummaryResult)
    _write_dataclasses(periods, result.periods, PeriodResult)
    _write_dataclasses(signals, result.signals, SignalResult)
    _write_dataclasses(statistics, comparisons, ComparisonResult)
    _write_dataclasses(trades, result.trades, TradeResult)
    workbook = Workbook()
    workbook.remove(workbook.active)
    _sheet(workbook, "summary", result.summaries, SummaryResult)
    _sheet(workbook, "statistics", comparisons, ComparisonResult)
    _sheet(workbook, "signals", result.signals, SignalResult)
    _sheet(workbook, "periods", result.periods, PeriodResult)
    _sheet(workbook, "trades", result.trades, TradeResult)
    workbook.save(workbook_path)
    conclusion.write_text(_conclusion_text(result, comparisons, active), encoding="utf-8")
    return summary, periods, signals, trades, statistics, workbook_path, conclusion
