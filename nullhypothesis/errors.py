"""领域错误类型(ADR-040)。

归属:ADR-035 / §4.5 的产出物归属矩阵把本文件分给 **T1**(「一次写全两个类」),
并规定「T2/T8 只 import,不新建」。本文件在 T2 分支上出现的唯一理由是:T1 与 T2
并行开工,T2 开工时 trunk 上尚无 `nullhypothesis/` 包,而 T2 的 ADR-022 门必须能
`from nullhypothesis.errors import StrategyError`。

故本文件**逐字实现 ADR-040 钉死的两个类与字段**,不增减任何字段 —— 两个分支写出
的内容因此相同,合并时不会有一方静默覆盖另一方的语义。若 T1 的版本先落地,本文件
应以 T1 的为准(字段相同,行为相同)。
"""

from __future__ import annotations


class DataValidationError(ValueError):
    """CSV 校验失败(ADR-019)。退出码见 `contracts.yaml` 的 `exit_codes.data_validation`。"""

    def __init__(
        self,
        message: str,
        *,
        file: str,
        row: int | None = None,
        column: str | None = None,
    ) -> None:
        super().__init__(message)
        self.file = file
        self.row = row
        self.column = column


class StrategyError(RuntimeError):
    """策略抛异常或请求非法值(ADR-022)。

    消息必须含:交易日序号 + 日期 + 原始 traceback(ADR-022)。
    原始异常由 `__cause__` 携带(`raise ... from exc`),ADR-040 明文规定。
    退出码见 `contracts.yaml` 的 `exit_codes.strategy_error`。
    """

    def __init__(
        self,
        message: str,
        *,
        strategy: str,
        bar_index: int,
        date: str,
    ) -> None:
        super().__init__(message)
        self.strategy = strategy
        self.bar_index = bar_index
        self.date = date
