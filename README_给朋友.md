# 给朋友的高阶矩动态 Kelly 使用说明

这个项目可以切换策略做统一验证。默认的 Kelly 策略会逐期计算六个内部模型；你也可以下载模板、上传自己的 Python 策略。两种模式都只用当时已经看到的数据，再用下一期真实收益评价。它不会自动交易，也不承诺盈利。

## 先选择策略

- 选择“Kelly 六模型策略”时，页面内部可以继续切换六个模型和 `RAW / BOUNDED / SAFE`。
- 选择“上传 Python 策略”时，先下载模板并实现 `decide(context)`，再上传 `.py` 文件。上传策略只产生一个 `TARGET` 目标仓位，不套用 Kelly 的模型统计。
- 上传策略与 Kelly 使用相同的行情入口、日周月窗口、pending、净值和模拟调仓流水。当前是内部可信代码工具，不要上传来源不明的 Python 文件。

## 安装与启动

需要 Python 3.11 或更高版本。

```bash
# macOS / Linux
git clone https://github.com/charlesyzk/kelly-mvp.git
cd kelly-mvp
python3 -m pip install -e .
python3 run_web.py
```

```powershell
# Windows
git clone https://github.com/charlesyzk/kelly-mvp.git
cd kelly-mvp
py -m pip install -e .
py run_web.py
```

打开 `http://127.0.0.1:8765`；结束时按 `Ctrl+C`。

## 数据怎么给

- 合成演示只用于确认页面正常，会明确标识为合成数据。
- 日线 CSV 可验证调用链；仓库有公开真实测试数据 `test/DEXUSEU_FRED.csv`。
- 正式研究使用 EODHD 或 Excel 提供日、周、月序列。

EODHD 会分别请求供应商日/周/月，不再只拿日线后冒充三频。Excel 建立 `daily`、`weekly`、`monthly` 三张表，每张都是 `date | symbol | adjusted_close`，可直接拖入网页。只有日线 CSV 时，程序仍可保守聚合周/月用于演示和交叉核验。

EODHD 环境变量：

```bash
# macOS / Linux / Git Bash
export EODHD_API_TOKEN="你的Token"
```

```powershell
# Windows PowerShell
$env:EODHD_API_TOKEN="你的Token"
```

```bat
rem Windows CMD / Anaconda Prompt；这里不支持 export
set "EODHD_API_TOKEN=你的Token"
```

Token 不要写进代码、截图或聊天；浏览器与输出文件不会得到 Token。

## κ 按钮和三类仓位

页面可选 κ=0.50、0.80、0.95，也能用滑杆，默认 0.80。κ 控制 `SAFE` 安全余量，越小通常越保守。选择后系统会按每个滚动窗口自动重算，但不会遍历 κ 并挑历史收益最高的值；改 κ 就是新研究设定，应单独保存结果。

- `RAW`：模型的有限原始候选，没有就留空；
- `BOUNDED`：直接在 −100% 到 +100% 内求最优；
- `SAFE`：再限制到 Taylor 收敛安全域。

页面可按六个模型、三类仓位和频率筛选。可以查看策略与买入持有净值、目标仓位、操作变动和可分页的逐笔买卖操作。

逐笔操作会区分开多、加多、减多、平多、开空、加空、减空、平空和反手。没有仓位变化的日期不会凑成一笔交易；最新仓位发生变化但还没有下一期结果时显示“待验证”。这些都是模型目标仓位变化，不是券商成交单，没有虚构股数、成交价和订单状态。`RAW` 可能越过边界或缺失，只用于研究；页面默认显示 `SAFE`。

## 当前规则

- 模型：`M2_LOG`、`M3_LOG`、`M4_LOG_ZERO`、`M4_SIMPLE`、`M4_LOG_MEAN`、`EMPIRICAL_EXACT`；
- 日/周/月窗口为 252/104/60 个收益；仓位边界为 −100% 到 +100%；
- 当前不乘 Half Kelly、不做 70/30 切分、不扣交易成本；
- 最新窗口作为 `pending` 信号保留，没有未来收益前不进入统计；
- 正式结论还要通过固定 Bootstrap、FDR、最低样本数及买入持有对照，不能只看准确率或一次盈利。

输入价格先计算普通简单收益，同时构造对数收益供不同模型使用。Kelly 本身不预测未来，只能基于历史窗口对未来分布的估计决定投多少。

## 命令行、命名和输出

```bash
PYTHONPATH=src python3 -m kelly_mvp \
  --eodhd-symbol AAPL.US --eodhd-from 2010-01-01 \
  --kappa 0.8 --output outputs/AAPL_k080_20260907
```

```bash
PYTHONPATH=src python3 -m kelly_mvp \
  --input /absolute/path/to/prices.xlsx --kappa 0.8 \
  --output outputs/excel_k080_20260907
```

目录建议命名为 `{标的或来源}_k{κ×100三位数}_{日期}`。输出为 `summary.csv`、`periods.csv`、`signals.csv`、`trades.csv`、`statistics.csv`、`results.xlsx`、`conclusion.txt`。逐期和信号文件还包含十二个滚动矩、目标值和求解状态，方便直接复算仓位来源。

本轮相对旧版：从单模型改成六模型，窗口改为 252/104/60，删除 Half Kelly、仓位类型 `FRACTIONAL / TRADE` 和 70/30，改用 `RAW / BOUNDED / SAFE`，加入 κ、EODHD 三频、Excel 三频、pending、破产口径、Bootstrap/FDR 和新报告。模拟交易流水 `trades.csv` 与仓位类型 `TRADE` 不是同一个概念：前者已恢复，用来解释目标仓位变化；后者仍按新研究口径删除。旧结果不能直接当作当前结果继续使用。

研究结果不构成投资建议；滑点、融资融券、税费和真实成交约束仍未完整建模。
