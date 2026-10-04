"""均线穿越:短均线上穿长均线则满仓,下穿则清仓。

**边沿检测**,不是区间持续为真 —— 只在穿越发生的那一根下单。

两者的区别在成交次数上是数量级的:区间写法会天天表达意图(虽然
ADR-008 的幂等让它仍只成交一次),边沿写法只在状态切换时表达。
更重要的是,边沿检测让「交易次数 ≈ 穿越次数」这个读数有意义。

注意:**穿越次数 ≠ 交易次数**。空仓时的下穿不会产生成交(目标仓位
本来就是 0,差额为 0 → ADR-008 无声跳过),最后一根上的穿越也会被
丢弃(ADR-005)。
"""

from nullhypothesis.strategy import Strategy

SHORT, LONG = 20, 60


class MaCross(Strategy):
    def next(self) -> None:
        close = self.data["Close"]
        if len(close) < LONG + 1:
            return  # 均线还没长够,不表达任何意图

        short_now = close.iloc[-SHORT:].mean()
        long_now = close.iloc[-LONG:].mean()
        short_prev = close.iloc[-SHORT - 1 : -1].mean()
        long_prev = close.iloc[-LONG - 1 : -1].mean()

        crossed_up = short_prev <= long_prev and short_now > long_now
        crossed_down = short_prev >= long_prev and short_now < long_now

        if crossed_up:
            self.target(weight=1.0)
        elif crossed_down:
            self.target(weight=0.0)
