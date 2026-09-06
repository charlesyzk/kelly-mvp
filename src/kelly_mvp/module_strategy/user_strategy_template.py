"""可直接下载、修改并从网页上传的策略模板。

框架通过 context 只提供截至信号日的窗口数据；回测框架负责下一期评价、成本和绩效。
"""

STRATEGY_META = {
    "id": "my_momentum_strategy",
    "name": "我的动量策略",
    "version": "1.0",
    "description": "最近窗口累计收益为正时做多，为负时做空。",
}


def decide(context):
    """返回目标仓位，允许范围外数值；框架会统一限制到 [-1, 1]。"""

    cumulative_return = context.prices[-1] / context.prices[0] - 1
    position = 0.5 if cumulative_return > 0 else -0.5 if cumulative_return < 0 else 0.0
    return {
        "position": position,
        "diagnostics": {
            "window_observations": len(context.returns),
            "window_return": cumulative_return,
        },
    }
