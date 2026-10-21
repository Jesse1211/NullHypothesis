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
  * ADR-053(T20,加性)—— 公司行为表:**独立文件**,四列
    `date,event,ratio,amount`,经 `load_events` 载入与校验。行为表是**标的的
    属性**,价格文件是**区间的切片**,故二者分文件;一份行为表可配多份价格数据。

`load_events` 为什么在本模块、而不是一个新的 `events.py`:ADR-035 的布局是
**冻结的名字契约**,其模块树里没有行为表模块 —— 新建一个即是改布局;而
「CSV 载入 + 校验 + 失败即 `DataValidationError`」正是本模块的职责,且
T21 的门要求 `pd.read_csv` **全库仍仅本文件一处**。故这是加性的正确落点。
**本任务不做任何价格调整**(T20 的 spec 原话)—— `load_events` 只产出一个
经校验的事件序列,调价是 T21 的事。

索引口径(T1/T5 的门都断言它):返回的 DataFrame index 必须是
`0..n-1` 连续整数。pandas 排序后会留下陈旧的 RangeIndex ——
那会让 `df.iloc[i]` 与 `df.loc[i]` 分叉,之后每个 `i+1` 查找都读错 bar。
故排序后**必须** `reset_index(drop=True)`。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from .errors import DataValidationError

__all__ = [
    "load_csv",
    "REQUIRED_COLUMNS",
    "PRICE_COLUMNS",
    "EXCLUDED_COLUMNS",
    "UnsortedDateWarning",
    # ── 以下由 T20(ADR-053)加性追加 ──
    "load_events",
    "CorporateEvent",
    "EVENT_COLUMNS",
    "EVENT_VALUES",
    "EVENT_IGNORED_COLUMNS",
    "NoOpEventWarning",
    "OutOfRangeEventWarning",
]

#: ADR-001:必须存在的列。`Volume` 是可选的,引擎不读它。
REQUIRED_COLUMNS: tuple[str, ...] = ("Date", "Open", "High", "Low", "Close")

#: ADR-019 的 NaN / `<= 0` 校验作用在这四列上(即 ADR-001 的 OHLC)。
PRICE_COLUMNS: tuple[str, ...] = ("Open", "High", "Low", "Close")

#: ADR-004:存在也不读、不参与任何计算,载入期即丢弃。
EXCLUDED_COLUMNS: tuple[str, ...] = ("Adj Close",)

# ── 以下三组常量由 T20 加性追加,值取自 `contracts.yaml` 的
#    `fixtures.adr053_events_schema`(单一真相来源,门只引用 key)──

#: ADR-053:行为表的四列,顺序即该 ADR 表格的顺序。
EVENT_COLUMNS: tuple[str, ...] = ("date", "event", "ratio", "amount")

#: ADR-053:`event` 的枚举。**其他值一律报错,不静默跳过**。
EVENT_VALUES: tuple[str, ...] = ("split", "dividend")

#: ADR-053:存在则**校验并忽略** —— Polygon / Alpaca / EODHD 的导出都带它,
#: 拒收会拒掉用户真实粘贴的文件。为 OQ-16(多标的)留的前向兼容。
EVENT_IGNORED_COLUMNS: tuple[str, ...] = ("symbol", "ticker")

#: ADR-053:`date` 按**日历日**解析。格式钉死而不让 pandas 推断 ——
#: 推断路径会接受带时刻/时区的写法,而那正是「除息日整体移一天」的入口。
_EVENT_DATE_FORMAT = "%Y-%m-%d"

#: ADR-053 / T20 门 6:无操作的 split 比值。见 `NoOpEventWarning`。
_NOOP_RATIO = 1.0


class UnsortedDateWarning(UserWarning):
    """日期非单调递增时的「提示」(ADR-019 的最后一行:排序 + 提示,非错误)。

    用 `warnings` 而非 `print`:它是标准库里唯一既能到达用户、又能被测试
    `pytest.warns` 观测、且**不**改变返回值契约的提示通道。
    """


class NoOpEventWarning(UserWarning):
    """`ratio == 1.0` 的 split —— 无操作事件(T20 门 6)。

    与 `UnsortedDateWarning` **同构**:走 `warnings` 通道、可被 `pytest.warns`
    观测、**不改变返回值契约**(该事件只是不进入因子计算,返回值仍是一个
    正常的事件序列)。

    为什么必须是**响亮的警告**而不是静默跳过:ADR-053 记明野外有第四种约定
    (CRSP 的 `FACPR` / ISO 的 `ADEX` 表示**增量**股数),它会把真实的 2:1
    拆股写成 `1.0`。静默丢弃等于把一次真实拆股无声吞掉 —— 正是 ADR-019
    反对的那类静默降级。
    """


class OutOfRangeEventWarning(UserWarning):
    """事件日期落在价格序列区间之外 —— 忽略该事件并提示(T20 门 10)。

    **不是错误**:行为表是**标的的属性**,价格文件是**区间的切片**
    (ADR-053),故一份行为表合理地可以覆盖比当前价格数据更长的区间。
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


# ═════════════════ 公司行为表(T20 / ADR-053 · ADR-054)═════════════════


@dataclass(frozen=True)
class CorporateEvent:
    """一条经校验的公司行为(ADR-053 的一行)。

    字段名与 ADR-053 的四列**逐字同名**,顺序也相同 —— 行为表的列名是对外
    契约(用户手写、厂商导出的 CSV 照它写),值对象换名字会让 T21 的仲裁门
    与 API 响应各说一套。

    `date` 是 `datetime.date` 而**不是** `datetime`/`Timestamp`:ADR-053 要求
    按**日历日**解析,**不得**按 UTC datetime —— 带时刻的类型会在任何时区
    转换里把除息日整体移一天,而 `date` 在类型层面就没有那个自由度。

    `ratio` / `amount` 互斥(ADR-053):split 行只有 `ratio`,dividend 行只有
    `amount`,另一个恒为 `None`。
    """

    date: _date
    event: str
    ratio: float | None
    amount: float | None


def load_events(
    path: str | Path,
    *,
    price_dates: "Sequence[Any] | None" = None,
) -> list[CorporateEvent]:
    """载入并校验公司行为表,返回按日期升序的事件序列(ADR-053 / T20)。

    **本函数不做任何价格调整。** 它只产出一个经校验的事件序列;因子法复权、
    两路仲裁、方向门与量级门都是 T21 的职责(T20 的 spec 原话)。

    Args:
        path: 行为表 CSV 路径(经 `--events` / `RunRequest.events` 传入)。
        price_dates: 价格序列的日期(通常即 `load_csv(...)["Date"]`)。给定时,
            落在其 **[首日, 末日]** 区间之外的事件被**忽略并提示**
            (`OutOfRangeEventWarning`)—— 不是错误,见 ADR-053:行为表是标的
            的属性,可以覆盖比价格切片更长的区间。不给定则不做区间过滤。

    Returns:
        `list[CorporateEvent]`,按 `date` 升序。空文件 / 仅表头 → `[]`
        (合法,等价于无事件 —— T20 门 9)。

    Raises:
        DataValidationError: ADR-019 的姿态,与 `load_csv` **同构** ——
            一次校完、全部响亮失败、`.file/.row/.column` 填好。
            `row` 的口径同 `load_csv`:**CSV 数据行的 0 基序号**。

    Warns:
        NoOpEventWarning: `ratio == 1.0` 的 split(该事件不进入因子计算)。
        OutOfRangeEventWarning: 事件日期在价格区间之外。
    """
    file_path = Path(path)
    file_str = str(path)

    # ── 文件本身读不到也是数据校验失败(与 load_csv 同构)────────────────
    if not file_path.exists():
        raise DataValidationError(
            f"{file_str}: 行为表文件不存在", file=file_str
        )

    try:
        raw = pd.read_csv(file_path)
    except pd.errors.EmptyDataError:
        # T20 门 9:**空文件合法** —— 等价于无事件,不报错。
        # 这与 load_csv 的空文件是**不同**的决策:价格序列为空时无从回测,
        # 而行为表为空是一个完全正常的事实(该标的没有行为)。
        return []
    except pd.errors.ParserError as exc:
        raise DataValidationError(
            f"{file_str}: 行为表 CSV 无法解析 —— {exc}", file=file_str
        ) from exc

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

    def fail() -> None:
        raise DataValidationError(
            _format_message(file_str, problems),
            file=file_str,
            row=first_row,
            column=first_column,
        )

    # ── 缺必须列 ───────────────────────────────────────────────────────────
    missing = [c for c in EVENT_COLUMNS if c not in raw.columns]
    for column in missing:
        record(f"行为表缺少必须列 column={column}", column=column)
    if missing:
        fail()

    # ── `symbol`/`ticker`:**校验并忽略**(ADR-053)────────────────────────
    # 「校验」= 存在就必须是非空字符串(一列全空的 symbol 是导出残缺的信号);
    # 「忽略」= 不进入返回的事件序列。OQ-16 把多标的列为非目标,故这里
    # 刻意**不**按 symbol 过滤 —— 那会是一个未被授权的语义。
    for column in EVENT_IGNORED_COLUMNS:
        if column not in raw.columns:
            continue
        for row in range(len(raw)):
            value = raw[column].iloc[row]
            if pd.isna(value) or not str(value).strip():
                record(
                    f"{column} 列存在但该行为空 row={row} column={column} "
                    f"—— 该列按 ADR-053 校验并忽略,但不得是空值",
                    row=row,
                    column=column,
                )

    # ── 仅表头 → 合法,等价于无事件(T20 门 9)────────────────────────────
    if len(raw) == 0:
        if problems:
            fail()
        return []

    # ── `date`:必须按【日历日】解析,不得按 UTC datetime(ADR-053)───────
    # `format=` 钉死 + `.dt.date` 取日历日。不传 format 时 pandas 会对
    # 混合写法做推断;而任何带时刻/时区的中间表示都可能把除息日移一天。
    parsed = pd.to_datetime(
        raw["date"], format=_EVENT_DATE_FORMAT, errors="coerce", utc=False
    )
    for row in range(len(raw)):
        if pd.isna(parsed.iloc[row]):
            record(
                f"date 无法解析 row={row} column=date "
                f"value={raw['date'].iloc[row]!r} —— 需要 "
                f"{_EVENT_DATE_FORMAT} 形式的日历日",
                row=row,
                column="date",
            )

    # ── `event`:枚举,其他值一律报错(ADR-053,T20 门 4)──────────────────
    # 不静默跳过:EODHD 的 splits 端点会混入 spinoff(如 `1324:1000`),
    # 送股/配股/并购都在这里**响亮失败** —— 配股是投资决策而非纯价格因子。
    events: list[str] = []
    for row in range(len(raw)):
        value = raw["event"].iloc[row]
        text = "" if pd.isna(value) else str(value).strip()
        events.append(text)
        if text not in EVENT_VALUES:
            record(
                f"event 取值非法 row={row} column=event value={text!r} —— "
                f"只接受 {{{', '.join(EVENT_VALUES)}}};不支持的事件类型"
                f"(送股 / 配股 / spinoff / 并购 / 股票股利)是 ADR-053 的"
                f"决策而非遗漏,遇到即报错",
                row=row,
                column="event",
            )

    # ── `ratio` / `amount`:列错位 + 取值域(T20 门 5、6)──────────────────
    ratios = pd.to_numeric(raw["ratio"], errors="coerce")
    amounts = pd.to_numeric(raw["amount"], errors="coerce")
    raw_ratio_present = [
        not (pd.isna(v) or str(v).strip() == "") for v in raw["ratio"]
    ]
    raw_amount_present = [
        not (pd.isna(v) or str(v).strip() == "") for v in raw["amount"]
    ]

    for row in range(len(raw)):
        event = events[row]
        has_ratio, has_amount = raw_ratio_present[row], raw_amount_present[row]

        if event == "split":
            # `amount` 出现在 split 行 → 报错(抓列错位的导出)
            if has_amount:
                record(
                    f"split 行不得有 amount row={row} column=amount "
                    f"value={raw['amount'].iloc[row]!r} —— 列错位",
                    row=row,
                    column="amount",
                )
            if not has_ratio:
                record(
                    f"split 行缺 ratio row={row} column=ratio —— "
                    f"split 行必填(前向股数比:4:1 拆股 = 4.0,1:8 合股 = 0.125)",
                    row=row,
                    column="ratio",
                )
            elif pd.isna(ratios.iloc[row]):
                record(
                    f"ratio 非数值 row={row} column=ratio "
                    f"value={raw['ratio'].iloc[row]!r}",
                    row=row,
                    column="ratio",
                )
            elif float(ratios.iloc[row]) <= 0:
                record(
                    f"ratio <= 0 row={row} column=ratio "
                    f"value={float(ratios.iloc[row])} —— 前向股数比必须 > 0",
                    row=row,
                    column="ratio",
                )
        elif event == "dividend":
            # `ratio` 出现在 dividend 行 → 报错(抓列错位的导出)
            if has_ratio:
                record(
                    f"dividend 行不得有 ratio row={row} column=ratio "
                    f"value={raw['ratio'].iloc[row]!r} —— 列错位",
                    row=row,
                    column="ratio",
                )
            if not has_amount:
                record(
                    f"dividend 行缺 amount row={row} column=amount —— "
                    f"dividend 行必填(每股税前毛额)",
                    row=row,
                    column="amount",
                )
            elif pd.isna(amounts.iloc[row]):
                record(
                    f"amount 非数值 row={row} column=amount "
                    f"value={raw['amount'].iloc[row]!r}",
                    row=row,
                    column="amount",
                )
            elif float(amounts.iloc[row]) <= 0:
                record(
                    f"amount <= 0 row={row} column=amount "
                    f"value={float(amounts.iloc[row])} —— 每股税前毛额必须 > 0",
                    row=row,
                    column="amount",
                )

    # ── 重复 `(date, event)` → 报错(T20 门 7)─────────────────────────────
    # 与 `data.py` 现有的重复日期规则**一致**:静默去重会悄悄改变事件数,
    # 属 ADR-019 反对的静默降级。
    if not parsed.isna().any():
        seen: dict[tuple[str, str], list[int]] = {}
        for row in range(len(raw)):
            key = (parsed.iloc[row].date().isoformat(), events[row])
            seen.setdefault(key, []).append(row)
        for (day, event), rows in seen.items():
            if len(rows) > 1 and event in EVENT_VALUES:
                record(
                    f"重复 (date, event) date={day} event={event} "
                    f"occurrences={len(rows)} rows={rows}",
                    row=rows[0],
                    column="date",
                )

        # ── 同日 split + dividend → 报错(ADR-053,T20 门 8)───────────────
        # 不支持的理由不是「猜错有风险」,而是让这个 bug 在代码里**物理上
        # 不存在**:框架从不把 split 因子与 dividend 因子在同一天相乘,
        # 就没有口径可错(`amount` 与 `close_before` 会分属拆股前/后两个基准)。
        by_day: dict[str, set[str]] = {}
        for row in range(len(raw)):
            by_day.setdefault(
                parsed.iloc[row].date().isoformat(), set()
            ).add(events[row])
        for day in sorted(by_day):
            if set(EVENT_VALUES) <= by_day[day]:
                record(
                    f"同日同时存在 split 与 dividend date={day} —— ADR-053 "
                    f"不支持该情形(两者的基准通常不同口径,相乘必错)。"
                    f"出路:回退到路径一,用数据源的 `Adj Close`"
                    f"(其内部已处理好口径)",
                    column="date",
                )

    # ── 一次抛出:全部响亮失败(ADR-019)─────────────────────────────────
    if problems:
        fail()

    # ── 构造事件序列 ──────────────────────────────────────────────────────
    parsed_rows: list[tuple[int, CorporateEvent]] = []
    for row in range(len(raw)):
        event = events[row]
        parsed_rows.append((
            row,
            CorporateEvent(
                date=parsed.iloc[row].date(),
                event=event,
                ratio=float(ratios.iloc[row]) if event == "split" else None,
                amount=float(amounts.iloc[row]) if event == "dividend" else None,
            ),
        ))

    # ── `ratio == 1.0` → NoOpEventWarning,且该事件不进入因子计算(门 6)──
    kept: list[tuple[int, CorporateEvent]] = []
    for row, ev in parsed_rows:
        if ev.event == "split" and ev.ratio == _NOOP_RATIO:
            warnings.warn(
                f"{file_str}: ratio == {_NOOP_RATIO} 的 split 是无操作事件,"
                f"不进入因子计算 date={ev.date.isoformat()} row={row}。"
                f"**请核对数据来源**:若它来自 CRSP 的 `FACPR` 或 ISO 的 "
                f"`ADEX`(约定 D,表示【增量】股数),那么一次真实的 2:1 "
                f"拆股正好被写成 {_NOOP_RATIO} —— 此时本行应当是 2.0"
                f"(ADR-053 的前向股数比)",
                NoOpEventWarning,
                stacklevel=2,
            )
            continue
        kept.append((row, ev))

    # ── 区间外事件 → 忽略并提示(门 10,非错误)──────────────────────────
    if price_dates is not None:
        bounds = pd.to_datetime(pd.Series(list(price_dates)), errors="coerce").dropna()
        if len(bounds) > 0:
            lo, hi = bounds.min().date(), bounds.max().date()
            in_range: list[tuple[int, CorporateEvent]] = []
            for row, ev in kept:
                if lo <= ev.date <= hi:
                    in_range.append((row, ev))
                else:
                    warnings.warn(
                        f"{file_str}: 事件日期在价格序列区间之外,已忽略 "
                        f"date={ev.date.isoformat()} event={ev.event} "
                        f"row={row}(价格区间 {lo.isoformat()}..{hi.isoformat()})"
                        f" —— 行为表是标的的属性,覆盖更长区间属正常(ADR-053)",
                        OutOfRangeEventWarning,
                        stacklevel=2,
                    )
            kept = in_range

    # 按日期升序 —— `mergesort` 保证同日多事件(合法组合)保持文件内次序。
    return [ev for _, ev in sorted(kept, key=lambda t: (t[1].date, t[0]))]
