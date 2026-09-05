# 真实行情测试数据

`SP500_FRED.csv` 是可直接上传到 K/60 网页的真实日频测试数据。

- 标的：S&P 500 指数；
- 来源：Federal Reserve Bank of St. Louis，FRED `SP500`；
- 口径：日收盘价格指数，不含股息；
- 用途：验证本项目的数据导入、日/周/月聚合和回测链路；
- 限制：这是价格指数，不等同于可直接交易的含股息投资组合，不能用来证明实盘收益。

网页使用：

1. 运行 `python3 run_web.py`；
2. 打开 `http://127.0.0.1:8765`；
3. 上传 `test/SP500_FRED.csv`；
4. 点击“开始计算”。

`SP500_FRED.metadata.json` 记录下载地址、获取时间、日期范围、行数和 SHA-256。需要重新获取同一固定区间时运行：

```bash
python3 tools/fetch_fred_test_data.py
```
