# Rolling-60 M4 Kelly MVP

这是对历史工程做减法后的最小研究框架。它只回答两个核心问题：

1. 用最近 60 个同频率收益得到的仓位，对下一期方向判断有多准？
2. 在同一回测区间内，策略收益和买入持有相比怎样？

## 固定研究口径

- 输入：一份日频复权收盘价 CSV，列为 `date,symbol,adjusted_close`。
- 频率：日、周、月；周线和月线由日线取每个完整周期的最后一个价格。
- 窗口：60 日、60 周、60 月，均指 60 个该频率的有效收益。
- 分布假设：下一期简单收益的前四阶原始矩，与最近 60 期经验矩相同。
- 目标函数：`q1*f - q2*f^2/2 + q3*f^3/3 - q4*f^4/4`。
- 仓位：先求 `[-1,1]` 内的四阶近似最优仓位，再乘 `0.5`（Half Kelly）。
- 时序：窗口截止于 `t`，仓位只评价 `t -> t+1` 的收益，不使用未来信息。
- 准确率：仅在仓位非零且下一期收益非零时判断方向；零仓位/零收益不硬算成错误，分母单独输出。
- 周/月最后一个聚合周期默认舍弃，因为仅凭文件截止日无法证明它已完整结束。

这是一项新的、简化后的研究设定。它不复用旧工程的六模型、Bootstrap/FDR 或四类仓位语义，也不继承旧结果。

## 运行

项目不依赖第三方 Python 包，Python 3.11 及以上即可。

### 网页方式（推荐）

```bash
cd kelly-mvp
python3 run_web.py
```

浏览器打开 `http://127.0.0.1:8765`。选择 CSV 后点击“开始计算”，网页会直接显示日、周、月结果，也可以下载摘要与逐期明细。上传内容只在本机内存中计算，不会发送到互联网，也不会由网页服务自动保存。

需要真实测试数据时运行 `python3 tools/fetch_fred_test_data.py`，它会从 FRED 下载固定区间的 S&P 500 日收盘数据并生成 `test/SP500_FRED.csv`。来源和使用限制见 `test/README.md`；行情文件本身不纳入版本库。

### 命令行方式

先生成明确标注的合成演示数据并运行：

```bash
cd kelly-mvp
python3 tools/make_demo_data.py
PYTHONPATH=src python3 -m kelly_mvp \
  --input examples/demo_prices.csv \
  --output outputs/demo
```

使用真实数据：

```bash
PYTHONPATH=src python3 -m kelly_mvp \
  --input /absolute/path/to/prices.csv \
  --output outputs/real_run \
  --cost-bps 0
```

输出包括：

- `summary.csv`：准确率、累计/年化收益、买入持有、最大回撤、换手等摘要；
- `periods.csv`：每次信号、前四阶矩、下一期收益、成本和净值，可逐行复算；
- `report.html`：可直接打开的结果页面，含指标表和净值曲线。

`--cost-bps 0` 表示毛收益研究。用于实盘可行性讨论前必须填入合理的交易成本、滑点及融资融券成本。

## 数据检查

本仓库不分发原始研究行情或历史正式输出。测试时使用自己的日频数据，或运行 `tools/fetch_fred_test_data.py` 获取公开测试序列。不要从汇总统计反推或伪造价格。

真实 CSV 规则：

- `date` 必须能解析为 `YYYY-MM-DD`；
- `symbol` 非空；
- `adjusted_close` 必须有限且大于 0；
- 同一标的同一天只能有一条记录；
- 每个标的至少需要 62 个相应频率价格，才能形成 60 个估计收益和 1 个下一期评价收益。

## 防止越调越差

摘要把可评价时期按时间切成 `development`（前 70%）和 `holdout`（后 30%）。参数修改只看 development；策略口径锁定后才看 holdout。若看过 holdout 后继续修改，下一轮必须换新的保留区间或新数据，不能继续称为未见样本。

## 测试

```bash
cd kelly-mvp
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
