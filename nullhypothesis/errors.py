"""领域错误类型(ADR-040)。

本模块由 T1 **一次写全**:`DataValidationError` 与 `StrategyError` 两个类
都在这里定稿。T2 / T8 只 `import`,**不新建**(§4.5 归属矩阵)——
否则并行开发时两个 agent 各写一半,后写者会静默覆盖前者。

退出码映射(ADR-040 / `contracts.yaml` 的 `exit_codes`):
    DataValidationError -> 2    (data_validation)
    StrategyError       -> 3    (strategy_error)
    CLI 参数非法        -> 4    (invalid_cli_args)

ADR-031 的 API `code` 映射:
    DataValidationError -> "DATA_VALIDATION"
    StrategyError       -> "STRATEGY_ERROR"
"""

from __future__ import annotations

__all__ = ["DataValidationError", "StrategyError"]


class DataValidationError(ValueError):
    """载入期数据校验失败(ADR-019)。

    继承 `ValueError` —— ADR-019 的行为表逐条写的是 `ValueError`,而
    ADR-040 把它收窄为一个具名子类;二者因此不矛盾:catch `ValueError`
    的调用方仍然能接住。

    属性(ADR-040 钉死,T1 的门逐字段断言):
        file:   出问题的 CSV 路径(字符串形式)
        row:    出问题的行号;整表级失败(缺列 / 空文件 / 仅表头)为 None
        column: 出问题的列名;与行无关的失败为 None

    `row` 的口径:**CSV 数据行的 0 基序号**(即表头之后的第一行为 0),
    与校验后 DataFrame 的 index 同一口径。消息文本里同时给出该序号,
    使「消息含行号」与 `.row` 字段永不分叉。
    """

    def __init__(
        self,
        message: str,
        *,
        file: str = "",
        row: int | None = None,
        column: str | None = None,
    ) -> None:
        super().__init__(message)
        self.file = file
        self.row = row
        self.column = column


class StrategyError(RuntimeError):
    """策略抛出异常或请求非法值(ADR-022)。

    继承 `RuntimeError`(ADR-040)。原始异常由 `__cause__` 携带 ——
    抛出处必须用 `raise StrategyError(...) from exc`,这样 ADR-022 要求的
    「原始 traceback」不会丢失。

    属性(ADR-040 钉死):
        strategy:   策略名(ADR-022 的错误信息要打印它)
        bar_index:  交易日序号(ADR-042 的「一行 CSV == 一个交易日」口径)
        date:       该交易日的日期字符串
    """

    def __init__(
        self,
        message: str,
        *,
        strategy: str = "",
        bar_index: int = -1,
        date: str = "",
    ) -> None:
        super().__init__(message)
        self.strategy = strategy
        self.bar_index = bar_index
        self.date = date
