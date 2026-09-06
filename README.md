# S/60 可插拔策略验证台

第一次使用、准备把项目发给朋友，请先看：[朋友使用说明](README_给朋友.md)。

这是一个内部使用、可审计的滚动策略验证框架。它统一回答两个核心问题：

1. 用最近 60 个同频率收益得到的仓位，对下一期方向判断有多准？
2. 在同一回测区间内，策略收益和买入持有相比怎样？

## 固定回测口径

- 输入：日频复权收盘价；可上传 `date,symbol,adjusted_close` CSV，也可由服务端调用 EODHD 历史日线接口。
- 频率：日、周、月；周线和月线由日线取每个完整周期的最后一个价格。
- 窗口：60 日、60 周、60 月，均指 60 个该频率的有效收益。
- 策略：内置六种 Kelly 模型，也可上传符合模板的 Python 目标仓位策略。
- 仓位：所有策略统一限制在 `[-1,1]`；内置 Kelly 默认再乘 `0.5`（Half Kelly）。
- 时序：窗口截止于 `t`，仓位只评价 `t -> t+1` 的收益，不使用未来信息。
- 准确率：仅在仓位非零且下一期收益非零时判断方向；零仓位/零收益不硬算成错误，分母单独输出。
- 周/月最后一个聚合周期默认舍弃，因为仅凭文件截止日无法证明它已完整结束。

内置 Kelly 模型包括 `M2_LOG`、`M3_LOG`、`M4_LOG_ZERO`、`M4_SIMPLE`、`M4_LOG_MEAN` 和 `EMPIRICAL_EXACT`。六者使用相同的 60 期窗口、仓位边界、成本和下一期验证口径，默认模型仍为 `M4_SIMPLE`。

对数收益模型当前明确采用“现金收益为零”的假设，没有静默引入无风险利率序列。`EMPIRICAL_EXACT` 现可作为正式可选模型，同时仍是其他 Kelly 模型的同窗口诊断基准。

## 运行

项目不依赖第三方 Python 包，Python 3.11 及以上即可。

### 网页方式（推荐）

```bash
cd kelly-mvp
python3 run_web.py
```

浏览器打开 `http://127.0.0.1:8765`。选择内置模型或上传 Python 策略，再使用 CSV 或 EODHD 行情运行。网页会显示日、周、月结果，并可下载摘要、信号、逐期明细和调仓流水。上传内容只在本机内存中计算，不会发送到互联网，也不会由网页服务自动保存。

### 上传自己的策略

在页面点击“下载策略模板”，修改 `STRATEGY_META` 和 `decide(context)` 后上传 `.py` 文件。策略上下文只包含当前信号日及以前的数据：

- `context.symbol`、`context.frequency`、`context.signal_date`；
- `context.prices`：最近 61 个价格，最后一个是信号日价格；
- `context.returns`：由上述价格形成的最近 60 个同频率简单收益；
- `context.window_start_date`、`context.window_end_date`。

`decide` 可以直接返回目标仓位数字，也可以返回：

```python
return {
    "position": 0.5,
    "diagnostics": {"score": 1.25},
}
```

框架会把目标仓位限制到 `[-1,1]`，并统一计算下一期收益、交易成本和绩效。上传策略不会应用 Kelly 比例。该功能只适合当前约定的内部可信文件；Python 文件会在本机服务进程中直接执行。

如果有 EODHD Token，可在启动前把它放入环境变量：

```bash
export EODHD_API_TOKEN="你的Token"
python3 run_web.py
```

随后在网页选择“EODHD 取数”，填写例如 `300308.SHE` 和日期范围。浏览器不会接触 Token；本地服务仅把代码、日期和 Token 发给 EODHD 取得真实日线，返回行情仍在本机内存中运行同一套策略。Token 不会写入源码、日志、报告或下载文件。

仓库已附公开领域的真实测试数据 `test/DEXUSEU_FRED.csv`。它是美元兑欧元日汇率，用于测试完整调用链；来源、引用方式和限制见 `test/README.md`。也可运行 `python3 tools/fetch_fred_test_data.py` 重建同一固定区间。

### 命令行方式

先生成明确标注的合成演示数据并运行：

```bash
cd kelly-mvp
python3 tools/make_demo_data.py
PYTHONPATH=src python3 -m kelly_mvp \
  --input examples/demo_prices.csv \
  --strategy M4_SIMPLE \
  --output outputs/demo
```

使用真实数据：

```bash
PYTHONPATH=src python3 -m kelly_mvp \
  --input /absolute/path/to/prices.csv \
  --output outputs/real_run \
  --cost-bps 0
```

直接调用 EODHD：

```bash
export EODHD_API_TOKEN="你的Token"
PYTHONPATH=src python3 -m kelly_mvp \
  --eodhd-symbol 300308.SHE \
  --eodhd-from 2012-01-01 \
  --eodhd-to 2026-08-31 \
  --output outputs/300308 \
  --cost-bps 10
```

其他内置模型可通过 `--strategy M2_LOG` 等方式选择。运行自定义策略：

```bash
PYTHONPATH=src python3 -m kelly_mvp \
  --input /absolute/path/to/prices.csv \
  --strategy-file /absolute/path/to/my_strategy.py \
  --output outputs/custom
```

`--eodhd-to` 可以省略，默认取到当天。EODHD 模式只下载日线复权价格，周线、月线仍由本项目按同一规则聚合，避免三个来源口径不一致。调用会消耗 EODHD 账户额度，具体历史范围和市场权限取决于订阅。

接口参数和代码格式请参考 [EODHD 官方历史行情文档](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes)。

输出包括：

- `summary.csv`：准确率、累计/年化收益、买入持有、最大回撤、换手等摘要；
- `signals.csv`：全部仓位信号，包含策略编号、原始/有界/最终仓位、诊断字段以及最后一个 `pending` 当前信号；
- `periods.csv`：已经有下一期结果的信号、策略诊断、仓位、收益、成本、对数增长和净值，可逐行复算；
- `trades.csv`：目标仓位发生变化的模拟调仓记录；最新待验证调仓的结果字段为空；
- `report.html`：可直接打开的结果页面，含指标表和净值曲线。

网页结果区还提供目标仓位曲线、单次调仓变化图和可分页的模拟逐笔调仓表，可按标的、开发/保留区间及日/周/月频率切换。这里的“逐笔”是模型目标仓位变化，不是券商成交回报；项目没有虚构成交价、股数或成交状态。

诊断区显示扣费后的平均单期与年化对数增长、触界率，以及 Kelly 模型与经验精确解的仓位差；上传策略则显示策略返回的诊断字段。最后一个没有未来收益的信号标记为 `pending`，只表示下一期目标仓位，不进入准确率或收益统计。

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

## Kelly 为什么需要“分布”

Kelly 本身是仓位选择规则，不是预测模型。理论上它需要未来收益分布，并选择使 `E[log(1+fR)]` 最大的仓位 `f`。现实中未来分布未知，因此必须另外估计。

本项目没有假设正态分布，也没有拟合真实的未来分布。六个内置模型分别检验低阶/高阶、简单收益/对数收益、零点/均值展开和经验精确目标的差异。

默认 `M4_SIMPLE` 的计算是：

1. 用最近 60 个同频率简单收益作为样本；
2. 计算经验原始矩 `q_k = (1/60) Σ R_i^k`，`k=1..4`；
3. 假设下一期的前四阶矩与这 60 期相同；
4. 用 `log(1+fR)` 的四阶 Taylor 展开近似期望对数增长；
5. 最大化 `G4(f)=q1*f-q2*f²/2+q3*f³/3-q4*f⁴/4`，并施加仓位边界和 Kelly 比例。

选择其他 Taylor 模型时，同一个窗口仍会计算 `mean(log(1+fR_i))` 的经验精确最优解，用来回答近似目标与不截断经验目标相差多少。选择 `EMPIRICAL_EXACT` 时，它直接形成交易仓位。策略实际对数增长使用扣除已配置交易成本后的财富倍数计算；若财富倍数归零或为负，单期对数增长为空、年化几何增长记为 `-100%`，不以人为小数替代 `log(0)`。

所以它并不知道真实完整分布，只使用了过去样本估计出的四个矩。策略是否有效，取决于“过去 60 期矩对下一期仍有代表性”这一假设是否成立；回测的作用正是检验这一点。

## 测试

```bash
cd kelly-mvp
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
