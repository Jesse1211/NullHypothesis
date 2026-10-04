"""买入持有:永远满仓。

天天调 `target(weight=1.0)` —— 幂等是引擎的责任(ADR-008),所以
最终只会成交 1 次,警告数为 0。
"""

from nullhypothesis.strategy import Strategy


class BuyAndHold(Strategy):
    def next(self) -> None:
        self.target(weight=1.0)
