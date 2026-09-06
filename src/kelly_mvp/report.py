"""CSV and standalone HTML outputs."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from dataclasses import fields
from html import escape
from pathlib import Path

from .backtest import BacktestResult, PeriodResult, SignalResult, SummaryResult, TradeResult


def _write_dataclasses(path: Path, rows: tuple[object, ...], row_type: type) -> None:
    names = [field.name for field in fields(row_type)]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for row in rows:
            values = {
                name: (
                    json.dumps(getattr(row, name), ensure_ascii=False, sort_keys=True)
                    if isinstance(getattr(row, name), Mapping)
                    else getattr(row, name)
                )
                for name in names
            }
            writer.writerow(values)


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def _bps(value: float | None) -> str:
    return "—" if value is None else f"{value * 10000:.2f} bps"


def _polyline(
    values: list[float],
    low: float,
    high: float,
    width: int = 620,
    height: int = 180,
) -> str:
    if not values:
        return ""
    span = high - low or 1.0
    x_step = width / max(1, len(values) - 1)
    points = [f"{i*x_step:.1f},{height-(value-low)/span*height:.1f}" for i, value in enumerate(values)]
    return " ".join(points)


def _html(result: BacktestResult) -> str:
    all_rows = [row for row in result.summaries if row.segment == "all"]
    strategy_name = all_rows[0].strategy_name if all_rows else "策略"
    body_rows = "".join(
        "<tr>"
        f"<td>{escape(row.strategy_name)}</td><td>{escape(row.symbol)}</td><td>{row.frequency}</td><td>{row.observations}</td>"
        f"<td>{_percent(row.direction_accuracy)}</td><td>{_percent(row.total_return)}</td>"
        f"<td>{_percent(row.annualized_return)}</td><td>{_bps(row.average_log_growth)}</td>"
        f"<td>{_percent(row.mean_abs_exact_kelly_gap)}</td><td>{_percent(row.buy_hold_return)}</td>"
        f"<td>{_percent(row.max_drawdown)}</td><td>{row.average_turnover:.3f}</td>"
        "</tr>" for row in all_rows
    )
    charts = []
    keys = sorted({(row.symbol, row.frequency) for row in result.periods})
    for symbol, frequency in keys:
        rows = [row for row in result.periods if row.symbol == symbol and row.frequency == frequency]
        strategy = [1.0] + [row.wealth for row in rows]
        benchmark = [1.0] + [row.buy_hold_wealth for row in rows]
        low = min(strategy + benchmark)
        high = max(strategy + benchmark)
        charts.append(
            f"<section><h3>{escape(symbol)} · {frequency}</h3>"
            '<svg viewBox="0 0 620 180" role="img" aria-label="净值曲线">'
            f'<polyline points="{_polyline(strategy, low, high)}" class="strategy"/>'
            f'<polyline points="{_polyline(benchmark, low, high)}" class="benchmark"/>'
            "</svg><p><span class='s'>策略</span> <span class='b'>买入持有</span></p></section>"
        )
    issues = "".join(f"<li>{escape(issue)}</li>" for issue in result.issues) or "<li>无</li>"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{escape(strategy_name)} · 策略验证报告</title><style>
body{{font-family:ui-sans-serif,system-ui,-apple-system;max-width:1120px;margin:40px auto;padding:0 20px;color:#18221c;background:#f5f7f2}}
h1{{font-size:30px}} .note{{padding:14px 18px;background:#fff4cf;border-left:4px solid #d19a00}}
table{{border-collapse:collapse;width:100%;background:white}} th,td{{padding:10px;border-bottom:1px solid #dfe5dc;text-align:right}} th:first-child,td:first-child{{text-align:left}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:16px}} section{{background:white;padding:16px;border-radius:10px}}
svg{{width:100%;height:180px;background:#fafcf8}} polyline{{fill:none;stroke-width:2}} .strategy{{stroke:#176b47}} .benchmark{{stroke:#d28b28}} .s{{color:#176b47}} .b{{color:#b36a08}}
</style></head><body><h1>{escape(strategy_name)} · 策略验证报告</h1>
<p class="note">研究结果，不构成投资建议。策略只使用信号日前最近 60 期数据；成本为配置值，未建模滑点、融资和融券约束。</p>
<h2>核心结果（全区间）</h2><table><thead><tr><th>策略</th><th>标的</th><th>频率</th><th>样本</th><th>方向准确率</th><th>累计收益</th><th>年化收益</th><th>单期对数增长</th><th>精确Kelly平均差</th><th>买入持有</th><th>最大回撤</th><th>平均换手</th></tr></thead><tbody>{body_rows}</tbody></table>
<h2>净值曲线</h2><div class="charts">{''.join(charts)}</div>
<h2>数据问题</h2><ul>{issues}</ul>
<p>开发段/保留段的完整指标见 summary.csv；全部仓位含待验证当前信号见 signals.csv；已评价复算数据见 periods.csv；模拟调仓记录见 trades.csv。</p></body></html>"""


def write_outputs(result: BacktestResult, output_dir: str | Path) -> tuple[Path, Path, Path, Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    summary = target / "summary.csv"
    periods = target / "periods.csv"
    signals = target / "signals.csv"
    trades = target / "trades.csv"
    report = target / "report.html"
    _write_dataclasses(summary, result.summaries, SummaryResult)
    _write_dataclasses(periods, result.periods, PeriodResult)
    _write_dataclasses(signals, result.signals, SignalResult)
    _write_dataclasses(trades, result.trades, TradeResult)
    report.write_text(_html(result), encoding="utf-8")
    return summary, periods, signals, trades, report
