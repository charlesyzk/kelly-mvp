# 真实行情测试数据

`DEXUSEU_FRED.csv` 是可直接上传到 K/60 网页的真实日频测试数据，并随公开仓库分发。

- 数据：一欧元对应的美元即期汇率；
- 原始来源：Board of Governors of the Federal Reserve System (US)；
- 获取来源：Federal Reserve Bank of St. Louis，FRED `DEXUSEU`；
- 口径：纽约午间电汇买入汇率，日频；
- 版权标签：`Public Domain: Citation Requested`；
- 用途：验证本项目的数据导入、日/周/月聚合和回测链路；
- 限制：这是汇率序列，不包含交易点差、隔夜利息和实际外汇执行约束，不能用来证明实盘收益。

网页使用：

1. 运行 `python3 run_web.py`；
2. 打开 `http://127.0.0.1:8765`；
3. 上传 `test/DEXUSEU_FRED.csv`；
4. 点击“开始计算”。

`DEXUSEU_FRED.metadata.json` 记录下载地址、获取时间、日期范围、行数、引用文本和 SHA-256。需要重新获取同一固定区间时运行：

```bash
python3 tools/fetch_fred_test_data.py
```
