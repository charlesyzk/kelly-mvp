# 高阶矩动态 Kelly 验证框架

这是一个可插拔、可审计的策略研究工具。顶层可以切换完整 Kelly 策略或上传可信 Python 策略；框架统一完成数据处理、无前视滚动、下一期验证、净值和调仓审计。它不下单，也不承诺盈利。第一次使用可先看 [给朋友的使用说明](README_给朋友.md)。

## 策略层级

- `KELLY_SIX_MODEL` 是一套顶层策略模块。它内部同时运行六个模型及三类仓位，页面结果区再切换查看；六个公式不是六个互相孤立的顶层策略。
- “上传 Python 策略”是另一种顶层模式。下载 [`user_strategy_template.py`](src/kelly_mvp/module_strategy/user_strategy_template.py)，实现 `STRATEGY_META` 和 `decide(context)` 后即可在网页上传。
- 上传策略只返回目标仓位，框架限制到 `[-1,1]` 并标为 `TARGET`。它与 Kelly 共用日/周/月窗口、pending、下一期收益、净值和调仓流水，但不参加 Kelly 专属的 M2、Bootstrap、FDR 比较。
- 当前按内部可信代码使用，不提供 Python 沙箱；不要上传来源不明的代码。

## 当前冻结口径

- 模型：`M2_LOG`、`M3_LOG`、`M4_LOG_ZERO`、`M4_SIMPLE`、`M4_LOG_MEAN`、`EMPIRICAL_EXACT`。`M2_LOG` 是比较基准，另外五个是候选；经验精确模型也用于诊断 Taylor 截断。
- 窗口：日 252、周 104、月 60 个同频率有效收益；最低正式匹配样本分别为 252、52、24。
- 仓位：`RAW / BOUNDED / SAFE`，边界 `[-1,1]`。不再使用旧版 `FRACTIONAL / TRADE`、Half Kelly 或 70/30 切分。
- 当前无风险对数收益率为 0，不启用交易成本。
- 统计：循环移动区块 Bootstrap（日/周/月区块 20/8/6，2000 次，种子 `20260904`）；按“候选模型 × 仓位类型”在 18 个标的频率组内做 BH-FDR，正式阈值 5%，探索阈值 10%。
- 主指标是带财富下限 `1e-12` 惩罚的平均对数增长；同时报告方向准确率、覆盖率、累计收益、买入持有、回撤、波动率、换手和破产。

候选只有同时满足“样本达标、相对同仓位类型 M2 的均值差为正且经 FDR、相对买入持有的差值置信区间下限不低于 0”才标为正式支持。方向准确率不能单独证明策略有效。

## 三类仓位与 κ

- `RAW`：模型的有限原始候选；不存在时保持空值。
- `BOUNDED`：直接在 `[-1,1]` 内最大化模型目标。
- `SAFE`：在边界内进一步满足展开收敛安全域；`safe_domain_type` 标明 `LOG_TAYLOR / SIMPLE_TAYLOR / EXACT_DOMAIN`，`safe_domain_intervals` 保存可能不连通的完整区间。

κ 是 SAFE 安全余量，必须满足 `0 < κ < 1`。网页提供 0.50、0.80、0.95 按钮和滑杆，修改后自动重算安全域及仓位；默认预注册值为 0.80。这里的“自动调整”不是遍历 κ 后挑历史收益最高的值。每次改变 κ 都是新研究设定，应使用新输出目录并披露参数。

## 安装与网页

需要 Python 3.11 或更高版本。

```bash
git clone https://github.com/charlesyzk/kelly-mvp.git
cd kelly-mvp
python3 -m pip install -e .
python3 run_web.py
```

Windows 将 `python3` 换成 `py`。打开 `http://127.0.0.1:8765`。页面先选择 Kelly 或上传策略，再载入明确标识的合成演示、日线 CSV、三工作表 Excel 或 EODHD。Kelly 模式显示 κ、模型和仓位筛选；上传模式显示策略文件及模板入口。所有指标均来自后端真实结果。

EODHD Token 只从服务端环境变量读取，不进入浏览器、源码、报告或日志。历史参考代码中曾出现硬编码凭据，不能继续使用，建议账户持有人轮换。

```bash
# macOS / Linux / WSL / Git Bash
export EODHD_API_TOKEN="你的Token"
python3 run_web.py
```

```powershell
# Windows PowerShell
$env:EODHD_API_TOKEN="你的Token"
py run_web.py
```

```bat
rem Windows CMD / Anaconda Prompt
set "EODHD_API_TOKEN=你的Token"
py run_web.py
```

## 数据输入

### EODHD

服务分别请求供应商日、周、月复权收盘价（`period=d/w/m`），三套序列独立进入对应窗口。调用会消耗账户额度，范围取决于订阅。

```bash
PYTHONPATH=src python3 -m kelly_mvp \
  --eodhd-symbol 300308.SHE --eodhd-from 2010-01-01 --eodhd-to 2026-08-31 \
  --kappa 0.8 --output outputs/300308_k080_20260907
```

### Excel（正式三频输入）

工作簿使用名称固定为 `daily`、`weekly`、`monthly` 的三个工作表；每张表都有 `date | symbol | adjusted_close`。日期可为 Excel 日期或 `YYYY-MM-DD`，价格须有限且大于 0，同表同标的日期不得重复。网页和命令行均可直接读取；程序不会根据文件名猜频率，也不会用日线覆盖 Excel 周/月数据。

```bash
PYTHONPATH=src python3 -m kelly_mvp \
  --input /absolute/path/to/prices.xlsx --kappa 0.8 \
  --output outputs/excel_k080_20260907
```

上传策略也可从命令行运行：

```bash
PYTHONPATH=src python3 -m kelly_mvp \
  --input /absolute/path/to/prices.xlsx \
  --strategy-file /absolute/path/to/my_strategy.py \
  --output outputs/my_strategy_20260907
```

### CSV（日线兼容输入）

CSV 列为 `date,symbol,adjusted_close`。只有日线时，程序会保守聚合完整周/月，用于兼容、演示和交叉核验；正式研究优先使用 EODHD 或 Excel 的直接三频数据，不能把聚合结果冒充供应商正式周/月序列。`test/DEXUSEU_FRED.csv` 是公开真实测试数据，来源与哈希见 `test/README.md`，只证明工程链路可运行。

## 输出与命名

建议目录名：`{symbol_or_source}_k{κ×100三位整数}_{运行日期}`，例如 `300308_k080_20260907`。不要覆盖历史正式输出。

- `summary.csv`：六模型 × 三仓位摘要；
- `periods.csv`：已评价信号的仓位、变化、下一期收益、财富倍数、增长、净值、十二个滚动矩、三个目标值、所选目标值及求解位置；
- `signals.csv`：全部信号，包括最后一个 `pending`、原仓位、目标变化、安全域、κ、十二个矩和目标值；
- `trades.csv`：仅保留目标仓位发生变化的模拟操作，包含开多/加多/减多/平多、开空/加空/减空/平空和双向反手；
- `statistics.csv`：相对 M2 与买入持有的 Bootstrap、FDR 和支持状态；
- `results.xlsx`：以上结果的 Excel 视图；
- `conclusion.txt`：配置、方法和结论摘要。

网页提供策略与买入持有净值曲线、完整仓位曲线、操作变动图、模型相对经验精确 Kelly 的诊断面板，以及可分页的逐笔操作表。“逐笔”是同一标的、频率、模型和仓位类型下相邻目标仓位之差，不是券商成交回报；没有虚构成交价、股数或订单状态。`RAW` 操作会标记为研究用途，因为它可能缺失或越过交易边界；网页默认查看 `SAFE`。

没有仓位变化的时期不会生成交易。最新 pending 信号只有在相对上一期确实发生变化时才生成 pending 操作；其下一期收益和净值结果保持空白。若 `RAW` 连续性中间出现缺失，程序不会猜测缺失期间持仓，也不会跨越缺口制造一笔交易。

## 计算逻辑

价格转换为普通简单收益 `R=P_t/P_{t-1}-1`，同时构造对数收益 `X=log(P_t/P_{t-1})`。同一窗口计算对数原始矩 `m_k=E[X^k]`、中心矩 `ν_k=E[(X-μ)^k]` 和简单收益原始矩 `q_k=E[R^k]`。六模型各自求 `RAW`，再独立求 `BOUNDED` 和 `SAFE`。`M4_SIMPLE` 的目标为：

```text
G4(f) = q1·f - q2·f²/2 + q3·f³/3 - q4·f⁴/4
```

`EMPIRICAL_EXACT` 最大化窗口内 `mean(log(1+fR_i))`，只对该经验分布“精确”，不代表未来分布已知。Kelly 只负责在给定分布估计后决定投多少，不会创造预测能力；研究检验的是历史窗口能否代表下一期。

财富倍数保留为 `1+fR_next`。若不大于 0，仍记录破产；统计使用 `log(max(1+fR_next,1e-12))` 给予有限惩罚。零仓位进入覆盖率和平均增长，但不进入方向准确率；非零仓位遇到零收益算方向失败。

## 本轮改动记录（0.6）

- 单一 `M4_SIMPLE + Half Kelly` 改为六模型统一计算；窗口改为 252/104/60。
- 仓位改为独立 `RAW / BOUNDED / SAFE`，加入 κ 按钮、配置及完整安全域输出。
- EODHD 改为供应商直接三频；新增三工作表 Excel 输入。
- 删除 70/30 切分和本阶段成本扣除。
- 增加 pending、财富可行性、破产和财富下限口径。
- 恢复循环区块 Bootstrap、18 组 BH-FDR、相对 M2 与买入持有联合判定。
- 输出统一为五个 CSV、一个 XLSX、一个 TXT；恢复逐笔操作、分页、净值图、完整仓位/变动图和经验精确诊断。
- 将十二个窗口矩、目标函数值、经验精确目标损失及求解位置写入逐期与信号输出，恢复直接复算能力。
- 增加六模型、SAFE、三频输入、统计复现、报告和网页契约测试。
- 增加顶层策略注册表和上传模板；Kelly 作为完整模块保留内部六模型/三仓位，上传策略使用同一审计链路并输出 `TARGET`。

旧版模型、窗口、仓位和统计口径均已改变，旧结果不能与 0.5 直接拼接或继续称为同一基线。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --check src/kelly_mvp/web_static/app.js
```

研究结果不构成投资建议，也不等于可实盘；滑点、融资融券、税费、成交约束和组合执行仍未完整建模。
