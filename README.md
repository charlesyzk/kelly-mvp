# 策略研究框架

本项目是可审计的策略研究工具，不会自动下单，也不承诺盈利。当前包含 Kelly 2.0 六模型验证、独立 A 股 EWMA 状态与账户策略，以及用户上传的 Python 研究策略。独立 EWMA 与 Kelly 内部的 EWMA 加权模型是两件不同的事。

## 策略模块

- `KELLY_SIX_MODEL`：一个顶层模块，内部运行 `M2_LOG`、`M4_LOG_ZERO`、`EMPIRICAL_EXACT` 及其三个 EWMA 加权版本。每个模型分别生成 `RAW / BOUNDED / SAFE`，并报告 `WITHOUT_STOP / WITH_STOP` 两条路径。
- EWMA 状态事件研究：[`ewma_return_position.py`](src/kelly_mvp/module_strategy/ewma_return_position.py) 提供因果波动率标准化、历史排名、五类状态、首次进入与冷却标记、固定持有期结果、MFE/MAE、分位数和恰好最差 5% 的 CVaR 汇总。它用于检验状态是否含有条件收益信息；不把极端排名解释成方向预测，也不构成已经审批的买卖策略。
- 独立 A 股 EWMA 顶层策略：[`ewma_refresh_strategy.py`](src/kelly_mvp/module_strategy/ewma_refresh_strategy.py) 按规则手册第一部分实现日对数收益、一次性 60 日样本方差初始化、λ=0.9 递推、严格过去排名、状态冷却、因果 a/b 校准、A/B/C 退出、63 日探索审批与 T+1 账户账本。它不经过 Kelly 的 `run_backtest()`，不生成 `RAW / BOUNDED / SAFE`，也不改变上传策略。
- 集合竞价材料中可复用的单股因子：[`opening_gap_filter.py`](src/kelly_mvp/module_strategy/opening_gap_filter.py) 统计前 30 个已完成交易日里，复权开盘价高于前一日复权收盘价的次数，默认不少于 10 次时标记为 eligible。信号日自身不计入，缺 OHLC 时返回不可用。该因子不含沪深 300 历史成分股，也不做跨股票排序或组合资金分配。
- 用户策略：网页或命令行可运行可信 Python 策略，只输出 `TARGET`；当前无 Python 沙箱，请勿上传来源不明代码。

## Kelly 2.0 口径

- 日/周/月窗口：252/104/60；最低正式匹配样本：252/52/24。
- EWMA 半衰期：84/35/20；窗口长度仍与对应等权模型一致，权重只使用信号日及之前的收益。
- 六模型分别做 RAW、BOUNDED、SAFE 求解。BOUNDED 是在 `[-1,1]` 内重新优化；Taylor SAFE 用完整数学收敛域，不再使用 κ 或与 `[-1,1]` 取交集；经验精确 SAFE 采用 `1+fZ_i >= 1e-6`。
- 不有限最优解时，沿用上一个有效仓位并标记状态；没有前序仓位时保持缺失。真实零仓位不作为缺失回填。
- 止损按调整后收盘价检查，按前一日止损线理想成交；多头 `k=2.5`、空头 `k=1.5`。周/月止损需要同标的日线。该模拟忽略跳空、成本和真实成交约束。
- 可用至少 252 个过去日收益标记大于 30% 且稳健 z 分数大于 10 的疑似异常；标记只提示人工核对，异常数据仍保留在模型中。
- 财富倍数为 `1+fR`；非正值记录为破产，平均对数增长使用 `log(max(1+fR,1e-12))`。方向准确率仅统计非零仓位且非零收益。
- 区块 Bootstrap：日/周/月区块 20/8/6、2000 次、种子 `20260904`；候选模型按仓位和止损路径分组与各自频率内基准比较，并做 BH-FDR。显著性是研究诊断，不是自动仓位开关。

## 输入与运行

需要 Python 3.11+。

```bash
python3 -m pip install -e .
python3 run_web.py
```

访问 `http://127.0.0.1:8765`。CSV 使用 `date,symbol,adjusted_close`；可选 OHLC 输入必须完整包含 `open,high,low,close`，用于保留原始和按复权因子换算后的价格字段。Excel 使用 `daily`、`weekly`、`monthly` 工作表，列为 `date,symbol,adjusted_close`。正式研究优先使用直接日/周/月数据；仅有日线时聚合周/月用于兼容和交叉核验。

```bash
PYTHONPATH=src python3 -m kelly_mvp \
  --input /absolute/path/to/prices.xlsx \
  --output outputs/study_20261005
```

EODHD Token 仅从 `EODHD_API_TOKEN` 环境变量读取。调用 EODHD 会消耗订阅额度；本次实现和验收不需要外部行情调用。

## 本地行情管理

管理页位于 `http://127.0.0.1:8765/data`，可导入五份 EODHD 代码清单、查看 SQLite 日/周/月覆盖及任务状态，并按清单、交易所和搜索条件导出 CSV。研究页可选择清单及代码，先用本地行情，缺频率时创建补数任务。命令行 `kelly-market-data` 与网页共用数据库。

数据库默认位于 `data/market_data.sqlite3`，可用 `KELLY_MARKET_DB` 更改路径；数据库和 WAL 文件不提交 Git，请自行备份。导入会保留成员快照、来源哈希和时间，不表示成分股历史恒定。

```bash
# 导入/刷新五份清单并查看状态
kelly-market-data import-universes
kelly-market-data status

# 获取清单的全历史日/周/月数据；可暂停后续跑
kelly-market-data fetch --collection sp500 --collection csi300 --batch-size 30

# 按交易所过滤
kelly-market-data fetch --collection sp500 --exchange US --batch-size 30
```

首次用整段历史请求同时发现各代码、频率最早可用日期，不额外发探测请求；增量更新回退45天重叠。请求按额度分批、串行执行，遇额度不足或限速会暂停，可续跑并重试失败项。交易所目录快照七天复用；默认只使用每日额度，只有明确传入 `--use-extra-calls` 才允许 EODHD 消耗已购 extra calls。NASDAQ Composite 清单是重构候选集；`WISGP.INDX`、`BVSP.INDX` 尚待验证。当前成分股快照回测存在幸存者偏差风险。详见 [EODHD 额度与限速说明](https://eodhd.com/financial-apis/api-limits)。

Token 仅通过服务端环境变量 `EODHD_API_TOKEN` 提供，不进入源码、日志、报告或行情库。

## 输出

Kelly 运行写出 `summary.csv`、`periods.csv`、`signals.csv`、`trades.csv`、`statistics.csv`、`results.xlsx` 和 `conclusion.txt`。止损版本是结果主键的一部分。调仓流水只解释目标仓位变化，不是券商成交单；pending 信号不进入收益和准确率统计。

原有 EWMA 事件研究仍由 `compute_ewma_rank_features()`、`mark_state_events()` 和 `analyze_ewma_state_events()` 提供 Python API，保留收盘到收盘条件统计。独立 A 股 EWMA 已加入主研究页的“顶层策略”选择器，也可通过 `/ewma` 查看完整审计页或使用专属 CLI；它不和 Kelly 共用回测契约：

```bash
kelly-ewma --input /absolute/path/to/300308.csv --output outputs/ewma-300308-run-001
```

输入必须是一个 `.SHG` 或 `.SHE` 标的的日线 CSV，列含 `date,symbol,adjusted_close,open,high,low,close`。OHLC 按 `adjusted_close / close` 统一复权。主研究页选择该策略后可直接上传 CSV 并在页面查看账户与审批摘要；完整审计入口 `/ewma` 和 CLI 只读本地文件，不触发行情请求。已有 CLI 输出目录不会覆盖。

输出包括 `run.json` 和特征、事件、固定期限结果、21 日路径、参数校准、A/B/C 退出、审批快照、账户信号、交易及逐日账本 CSV。手册当前采用 60/20/20 切分；验证容量不足 100 时改为三等分。账户包括刷新审批 × A/B/C 和固定初始审批 × A/B/C 共六组，并附同预算买入持有基准。主审批使用非循环 63 交易日区块、10,000 次重采样、种子 `20260906`；126 日仅作敏感性诊断。每 63 日重复审批尚无序贯误差控制保证。事件条件统计可能重叠，不能连乘；账户账本使用 10% 投入比例，往返成本入场时一次计提。探索准入与严格统计验证分别输出，`exploration_allowed` 不能替代 `statistically_validated`。

可选列 `adjusted_limit_up,adjusted_limit_down,price_limits_verified` 提供逐日核实的复权涨跌停价。只有 `price_limits_verified=true` 才会使用；缺少时采用单一价格日的保守代理。当前行情层没有完整停牌、历史 ST、队列、整手和逐笔成交资料。规则手册指出 2024-04-30 至 2024-05-06 的复权价格疑似断点；当前输入尚未附这段的供应商原始报文和公司行动对照，具体缺少原始开高低收、供应商复权收盘、逐日复权因子/算法版本、公司行动生效日期及交易状态字段。引擎不会修复这段价格或推断根因，也不证明价格连续性；正式评价该标的前应先补齐并核对这些资料。

## 验收

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --check src/kelly_mvp/web_static/app.js
```

研究结果不构成投资建议。没有完整成本、滑点、融资融券、税费及真实执行验证前，不得宣称可实盘。
