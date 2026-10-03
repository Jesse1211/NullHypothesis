"""CSV 载入与校验(T1)。

契约来源:
  * ADR-001 —— 输入列为 `Date,Open,High,Low,Close[,Volume]`。OHLC 四列必须
    **预先**按同一复权因子调整。框架内无任何复权机制,也**不验证**该前提;
    本模块因此不做任何价格调整。
  * ADR-004 —— **不使用 `Adj Close` 列**。CSV 若含该列,本模块在任何计算之前
    就把它从 DataFrame 里移除,使下游结构上无法读到它。
  * ADR-019 —— 载入时**一次校完,全部响亮失败**:六种失败情形在同一次调用里
    全部收集后一次抛出,不做「遇到第一个就返回」的早退;日期非单调递增则
    **排序 + 提示**(非错误)。
  * ADR-023 —— 单行 CSV 允许载入(零交易由引擎层体现,不是载入期错误)。
  * ADR-040 —— 失败一律抛 `DataValidationError`,并填好 `.file/.row/.column`。
  * ADR-042 —— 交易日 ≔ 校验后的**一行**。框架没有交易日历,**不检测日期缺口**。

索引口径(T1/T5 的门都断言它):返回的 DataFrame index 必须是
`0..n-1` 连续整数。pandas 排序后会留下陈旧的 RangeIndex ——
那会让 `df.iloc[i]` 与 `df.loc[i]` 分叉,之后每个 `i+1` 查找都读错 bar。
故排序后**必须** `reset_index(drop=True)`。
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd

from .errors import DataValidationError

__all__ = [
    "load_csv",
    "REQUIRED_COLUMNS",
    "PRICE_COLUMNS",
    "EXCLUDED_COLUMNS",
    "UnsortedDateWarning",
]

#: ADR-001:必须存在的列。`Volume` 是可选的,引擎不读它。
REQUIRED_COLUMNS: tuple[str, ...] = ("Date", "Open", "High", "Low", "Close")

#: ADR-019 的 NaN / `<= 0` 校验作用在这四列上(即 ADR-001 的 OHLC)。
PRICE_COLUMNS: tuple[str, ...] = ("Open", "High", "Low", "Close")

#: ADR-004:存在也不读、不参与任何计算,载入期即丢弃。
EXCLUDED_COLUMNS: tuple[str, ...] = ("Adj Close",)


class UnsortedDateWarning(UserWarning):
    """日期非单调递增时的「提示」(ADR-019 的最后一行:排序 + 提示,非错误)。

    用 `warnings` 而非 `print`:它是标准库里唯一既能到达用户、又能被测试
    `pytest.warns` 观测、且**不**改变返回值契约的提示通道。
    """


def load_csv(path: str | Path) -> pd.DataFrame:
    """载入并校验价格 CSV,返回可直接喂给引擎的 DataFrame(ADR-035 钉死的签名)。

    返回的 DataFrame:
      * 列为 `Date,Open,High,Low,Close[,Volume]`,`Date` 已转为 `datetime64`;
      * **不含** `Adj Close`(ADR-004);
      * 按 `Date` 升序,index 为 `0..n-1` 连续整数;
      * 行数 == 交易日数(ADR-042)。

    Raises:
        DataValidationError: ADR-019 的任一失败情形。多个问题会在**同一个**
            异常里一次报全(消息逐条列出);`.row`/`.column` 取第一条
            有行列定位的问题,使单问题场景下字段精确、多问题场景下仍不为 None。
    """
    file_path = Path(path)
    file_str = str(path)

    # ── 读文件本身的失败(不存在 / 不可解析)也是数据校验失败 ───────────────
    if not file_path.exists():
        raise DataValidationError(
            f"{file_str}: 数据文件不存在", file=file_str
        )

    try:
        raw = pd.read_csv(file_path)
    except pd.errors.EmptyDataError as exc:
        # ADR-019:空文件 —— 连表头都没有,pandas 直接抛 EmptyDataError
        raise DataValidationError(
            f"{file_str}: 空文件 —— CSV 不含任何内容(需要表头 "
            f"{','.join(REQUIRED_COLUMNS)} 与至少 1 个数据行)",
            file=file_str,
        ) from exc
    except pd.errors.ParserError as exc:
        raise DataValidationError(
            f"{file_str}: CSV 无法解析 —— {exc}", file=file_str
        ) from exc

    # ── ADR-004:在任何校验/计算之前就摘掉 Adj Close ────────────────────────
    # 顺序是有意义的:先丢弃再校验,该列里的 NaN / 负值/ 任何内容都
    # 结构上不可能影响结果 —— 它根本没进入被校验的那张表。
    dropped = [c for c in EXCLUDED_COLUMNS if c in raw.columns]
    if dropped:
        raw = raw.drop(columns=dropped)

    problems: list[str] = []
    first_row: int | None = None
    first_column: str | None = None

    def record(msg: str, row: int | None = None, column: str | None = None) -> None:
        """登记一条问题 —— 不抛出,ADR-019 要求「一次校完」。"""
        nonlocal first_row, first_column
        problems.append(msg)
        if first_row is None and first_column is None and (
            row is not None or column is not None
        ):
            first_row, first_column = row, column

    # ── 情形 1:缺必须列 ────────────────────────────────────────────────────
    missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    for column in missing:
        record(f"缺少必须列 column={column}", column=column)

    # ── 情形 2:空文件 / 仅表头 ─────────────────────────────────────────────
    if len(raw) == 0:
        record(
            "仅表头,无数据行 —— 需要至少 1 个数据行(ADR-023 允许恰好 1 行)"
        )

    # 缺列或零行时,后续按行/按列的校验无从进行 → 此时就把已收集的问题报全。
    # 这不是「早退」:该分支下剩余校验在结构上不可执行,而非被跳过。
    if missing or len(raw) == 0:
        raise DataValidationError(
            _format_message(file_str, problems),
            file=file_str,
            row=first_row,
            column=first_column,
        )

    df = raw.copy()

    # ── 情形 3:OHLC 含 NaN;情形 4:价格 <= 0 ──────────────────────────────
    # 两条都逐 (行, 列) 报告,消息含行号 + 列名(+ 值),ADR-019 原文要求。
    for column in PRICE_COLUMNS:
        numeric = pd.to_numeric(df[column], errors="coerce")
        for row in range(len(df)):
            value = numeric.iloc[row]
            if pd.isna(value):
                original = df[column].iloc[row]
                record(
                    f"OHLC 含 NaN/非数值 row={row} column={column} "
                    f"value={original!r}",
                    row=row,
                    column=column,
                )
            elif value <= 0:
                record(
                    f"价格 <= 0 row={row} column={column} value={value}",
                    row=row,
                    column=column,
                )
        df[column] = numeric

    # ── Date 解析:无法解析的日期本身就是 NaN 类失败 ────────────────────────
    parsed_dates = pd.to_datetime(df["Date"], errors="coerce")
    for row in range(len(df)):
        if pd.isna(parsed_dates.iloc[row]):
            record(
                f"Date 无法解析 row={row} column=Date "
                f"value={df['Date'].iloc[row]!r}",
                row=row,
                column="Date",
            )
    df["Date"] = parsed_dates

    # ── 情形 5:重复日期 —— 消息含日期 + 出现次数(ADR-019) ────────────────
    # ADR-042 的括号「去重、排序」是对「已通过 ADR-019 的数据长什么样」的
    # 描述,不是授权静默去重:ADR-019 的行为表与 T1 的门都把重复日期列为
    # **失败**情形。静默去重会悄悄改变交易日数(ADR-042),属 ADR-019
    # 反对的「静默降级」。
    if not parsed_dates.isna().any():
        counts = parsed_dates.value_counts()
        duplicates = counts[counts > 1]
        for date in sorted(duplicates.index):
            occurrences = int(duplicates[date])
            rows = [int(i) for i in parsed_dates.index[parsed_dates == date]]
            record(
                f"重复日期 date={date.date().isoformat()} "
                f"occurrences={occurrences} rows={rows} column=Date",
                row=rows[0],
                column="Date",
            )

    # ── 一次抛出:全部响亮失败(ADR-019) ──────────────────────────────────
    if problems:
        raise DataValidationError(
            _format_message(file_str, problems),
            file=file_str,
            row=first_row,
            column=first_column,
        )

    # ── 情形 6:日期非单调递增 → 排序 + 提示(非错误) ─────────────────────
    if not df["Date"].is_monotonic_increasing:
        warnings.warn(
            f"{file_str}: 日期非单调递增,已按 Date 升序排序(ADR-019)",
            UnsortedDateWarning,
            stacklevel=2,
        )
        df = df.sort_values("Date", kind="mergesort")

    # index 必须重建为 0..n-1 连续 —— 无条件执行,不只在排序分支里:
    # pandas 排序后留下的陈旧 RangeIndex 会让 iloc/loc 分叉,
    # 之后每个 i+1 查找都读错 bar(T1/T5 的门都断言这一条)。
    df = df.reset_index(drop=True)

    return df


def _format_message(file_str: str, problems: list[str]) -> str:
    """把一次校验里收集到的全部问题拼成一条消息(ADR-019「一次校完」)。"""
    if len(problems) == 1:
        return f"{file_str}: {problems[0]}"
    lines = [f"{file_str}: 数据校验失败,共 {len(problems)} 处:"]
    lines.extend(f"  - {p}" for p in problems)
    return "\n".join(lines)
