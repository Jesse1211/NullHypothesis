"""CSV 载入与校验(T1)。

契约来源:
  * ADR-001 —— 输入列为 `Date,Open,High,Low,Close[,Volume]`(主张 A,**保留**)。
    主张 B/C/D 已被 ADR-050/051 **推翻**:复权现在由本模块自己完成。
  * **ADR-050(T21)—— 复权划入框架内:`load_csv` 自己算因子,OHLC 四列同乘。**
    `supersedes ADR-001(部分)与 ADR-004(全部)`。

        cumfactor_t = ∏ { f_e : 事件 e 的日期 > t }    # 只作用于事件日【之前】
        Open'_t = Open_t × cumfactor_t                # High/Low/Close 同理,【同一个】因子

    **后复权**:最新一行的 `cumfactor` 恒为 `1.0`,历史被缩放 —— 与 `Adj Close`
    列自身的语义一致,使两条因子路径产出**同一组绝对价格**。
    **累积规则取「严格晚于 t」**:除权除息日当根 bar **不**被自己的事件调整
    (它本就已按新基准交易)。该规则与 CRSP 的 `CUMFACPR`(`EXDT > d`)一致;
    ⚠️ zipline 反其道而行(inclusive),故 zipline 格式的调整表不能逐日照搬。
  * **ADR-004 —— 全部作废(ADR-050)。** `Adj Close` 不再被丢弃前就无视:
    它现在是**因子路径一**的来源(`factor_t = AdjClose_t / Close_t`)。
    **读取顺序**:先读取用于算因子,**再**从返回帧中移除 —— 原先的
    「先 drop 再校验」顺序会让路径一的前提结构上不可达。
    该列仍**不出现**在返回的 DataFrame 里(ADR-001 主张 A 的残留部分仍有效)。
  * **ADR-051(T21)—— 两条因子路径都缺席时不调价,但体检隔夜跳空。**
    `|Open_t/Close_{t-1} − 1| >= gap_suspicion_pct` → 响亮**提示**(非错误)。
    运算符**必须**是 `>=`:5:4 拆股算出 `-19.999999999999996`,`round` 后恰为
    `-20.0`,写成 `>` 会**静默**漏掉它 —— 那是浮点表示造成的,不是决策。
  * **ADR-052(T21)—— 两条因子路径,行为表优先,不一致则报错。**
    两路各算**累积因子序列**(不是逐 bar 因子,也不是调整后的价格),
    逐日比最大**相对**偏差;`>= factor_agreement_tol` 即 `DataValidationError`。
  * **ADR-054(T21)—— `amount` 与 `close_before` 必须同股数基准**:
    三条门(拆股方向门 / 分红量级门 / `amount >= close_before`)部分守护一个
    **无法从数据独立证实**的前提。
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
`load_events` 只产出一个经校验的事件序列;**调价由 T21 在本模块内完成**
(`load_csv(path, events=...)`)。

**ADR-035 的签名契约**:ADR-035 把 `load_csv(path) -> pd.DataFrame` 钉死。
T21 的 `events=` 是**仅关键字且可选**的加性参数 —— `load_csv(path)` 的调用
形态与返回值契约逐字不变(§7 第 1 条的加性原则),与 T20 给 `load_events`
加 `price_dates=` 的手法同构。**不是**改签名。

**下游零影响,且这是结构性保证而非约定**(ADR-050):`load_csv` 是唯一数据
入口(`pd.read_csv` 全库仅本文件),引擎只读 `bar.Open` 与 `Close`,
`BarLike` 只声明 `Open` 与 `Date`。故只要本函数返回**列名与形状不变**的
DataFrame,下游**无从分辨**价格是否被调整过 —— 没有那个接缝。

**诚实代价(ADR-055,文案由 T24 归集)**:复权后的价格**不是**当时屏幕上的
价格,交易清单里的成交价从未真实成交过;分红按因子法折进价格等价于
**分红自动再投资且不付手续费**,故净值曲线**高估**真实可得收益;
`Volume` **不**随复权调整,复权后它与价格序列不再自洽。

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
    # ── 以下由 T21(ADR-050/051/052/054)加性追加 ──
    "ADJ_CLOSE_COLUMN",
    "GAP_SUSPICION_PCT",
    "FACTOR_AGREEMENT_TOL",
    "DIVIDEND_YIELD_SUSPICION",
    "SPLIT_DIRECTION_TOL",
    "UnadjustedPriceWarning",
    "cumfactors_from_events",
    "cumfactors_from_adj_close",
]

#: ADR-001:必须存在的列。`Volume` 是可选的,引擎不读它。
REQUIRED_COLUMNS: tuple[str, ...] = ("Date", "Open", "High", "Low", "Close")

#: ADR-019 的 NaN / `<= 0` 校验作用在这四列上(即 ADR-001 的 OHLC)。
PRICE_COLUMNS: tuple[str, ...] = ("Open", "High", "Low", "Close")

#: ADR-004(**已由 ADR-050 作废**)原先在任何计算之前就丢弃的列。
#: 名字保留(它是 T1 以来的公开符号),但语义已改为「**不出现在返回帧里**」——
#: 该列现在先被**读取**用于算因子路径一,算完**才**移除。见 `ADJ_CLOSE_COLUMN`。
EXCLUDED_COLUMNS: tuple[str, ...] = ("Adj Close",)

#: ADR-052 路径一的来源列:`factor_t = AdjClose_t / Close_t`。
#: 它是 `EXCLUDED_COLUMNS` 里唯一有因子语义的那个 —— 分开命名,因为
#: 「从返回帧移除」与「作为因子来源读取」在 ADR-050 之后是两件不同的事。
ADJ_CLOSE_COLUMN: str = "Adj Close"

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

# ── 以下四个阈值由 T21 加性追加。值取自 `contracts.yaml` 的 `thresholds`
#    (单一真相来源);`tests/test_contracts.py` 断言两处逐值一致,故这里
#    不是第二处复述点而是一份被门守护的投影。
#
#    为什么不在 import 时读 yaml:`data.py` 是 T1 以来最底层的模块,
#    `engine`/`run`/`api`/`backtest` 全都 import 它。让它在 import 时依赖
#    `contracts.yaml` 的存在会把一个数据文件变成 import 的前置条件 ——
#    而 `report.py` / `plot.py` 的 `lru_cache` 读法是在**函数内**按需读。
#    这里的量是纯数值常量,门守护即足够。

#: OQ-11 / ADR-051:跳空体检阈值(百分比)。**构建期不得改** ——
#: 要调须重新讨论 OQ-11,而不是直接改这个常数。
GAP_SUSPICION_PCT: float = 20.0

#: ADR-052:两路累积因子的相对偏差容差。**取 `1e-4`,不取 `1e-6`** ——
#: 厂商数据自带浮点噪声(Tiingo 把 7:1 存成 `7.000007000007001`,
#: 相对偏差实测 `1.0e-6`),`1e-6` 会让这条门几乎必红。
FACTOR_AGREEMENT_TOL: float = 1e-4

#: ADR-054 门二:`amount / close_before` 的上限。超过即**响亮失败** ——
#: 单笔现金分红 > 价格的 25% 几近必为基准错或特别分配。
DIVIDEND_YIELD_SUSPICION: float = 0.25

#: ADR-054 门一(方向门)的容差。**不可按精确值断言** —— 厂商有浮点噪声
#: (ADR-054 原话)。该门抓的是**方向**写反(16 倍误差),不是精度。
SPLIT_DIRECTION_TOL: float = 0.05


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


class UnadjustedPriceWarning(UserWarning):
    """跳空体检命中 —— 「嫌疑未复权」的**提示**(ADR-051 / T21 门 14)。

    与 `UnsortedDateWarning` **同构**:走 `warnings` 通道、可被 `pytest.warns`
    观测、**不改变返回值契约**。

    **它是体检,不是诊断** —— 发出本提示时价格**未被调整**(调不了:两条
    因子路径都缺席,框架不知道事件是什么)。ADR-051 的原话:
    「漏报的代价是一条错曲线,误报的代价是一行提示」,故宁可误报。

    ADR-001 主张 D 写「该前提**不可验证**」—— 那是**事实错误**:ADR-021
    早就在算跳空统计了,而 4:1 拆股的签名**就是**一个 −75% 的隔夜跳空。
    框架早就在计算那个能证伪这个前提的统计量,只是没人拿它做判断。
    """


def load_csv(
    path: str | Path,
    *,
    events: "str | Path | Sequence[CorporateEvent] | None" = None,
) -> pd.DataFrame:
    """载入、校验**并复权**价格 CSV,返回可直接喂给引擎的 DataFrame。

    `path` 是 ADR-035 钉死的位置参数,**未变**;`events` 是 T21 加性追加的
    **仅关键字可选**参数(与 T20 给 `load_events` 加 `price_dates=` 同构)。
    `load_csv(path)` 的调用形态与返回值契约逐字不变。

    返回的 DataFrame:
      * 列为 `Date,Open,High,Low,Close[,Volume]`,`Date` 已转为 `datetime64`;
      * **不含** `Adj Close` —— 但 ADR-050 之后该列先被**读取**用于算因子
        路径一,算完**才**移除(顺序是本任务的前提,不是实现细节);
      * OHLC 四列已按**同一个** `cumfactor_t` 复权(ADR-050),**后复权**:
        最新一行的 `cumfactor` 恒为 `1.0`,故最后一行逐值等于 CSV 原值;
      * `Volume` **不**随复权调整(ADR-050 重申)——复权后它与价格序列不再自洽;
      * 按 `Date` 升序,index 为 `0..n-1` 连续整数;
      * 行数 == 交易日数(ADR-042)。

    Args:
        path: 价格 CSV 路径。
        events: **因子路径二**(ADR-053)。两种形态都接受:

              * **行为表文件路径**(`--events` 的直接取值)—— 本函数内部调
                `load_events(..., price_dates=...)`,故区间过滤(T20 门 10)
                拿得到价格日期,且价格 CSV **只被读一次**;
              * 已解析的 `Sequence[CorporateEvent]` —— 给门与直接调用方用。

            给定且非空时优先于 `Adj Close`(ADR-052:行为表信息更多,
            带事件类型与金额,可审计)。两路并存时**逐日比对累积因子**,
            最大相对偏差 `>= FACTOR_AGREEMENT_TOL` 即报错。

    Returns:
        复权后的 DataFrame。两条因子路径**均缺席**时**不调价**,
        但按 `GAP_SUSPICION_PCT` 体检隔夜跳空(ADR-051)。

    Raises:
        DataValidationError: ADR-019 的任一失败情形。多个问题会在**同一个**
            异常里一次报全(消息逐条列出);`.row`/`.column` 取第一条
            有行列定位的问题,使单问题场景下字段精确、多问题场景下仍不为 None。
            T21 追加的失败情形(各自响亮失败,ADR-019 的姿态):
              * 除息日落在第一根 bar（`close_before` 不存在,ADR-053）
              * `amount >= close_before`（因子 `<= 0`,ADR-054 门三）
              * `amount / close_before >= DIVIDEND_YIELD_SUSPICION`（门二）
              * split 方向门不成立（`ratio` 约定写反,门一）
              * 两路累积因子不一致（ADR-052）

    Warns:
        UnadjustedPriceWarning: 两路缺席 + 隔夜跳空命中阈值(ADR-051)。
        UnsortedDateWarning: 日期非单调递增(ADR-019,既有行为)。
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

    # ── ADR-050:**先读取用于算因子,再从返回帧中移除** ─────────────────────
    #
    # 这个顺序是 T21 的前提,不是实现细节。ADR-004 原先的注释自述
    # 「先丢弃再校验…结构上不可能影响结果」—— 那个顺序会让因子路径一
    # (`factor_t = AdjClose_t / Close_t`)**结构上不可达**。
    #
    # ADR-004 已由 ADR-050 **全部作废**。保留的只有 ADR-001 主张 A 的残留
    # 部分:该列**不出现在返回的 DataFrame 里**(下游读不到它,
    # 也就不可能把复权价与未复权价混在一个表里用)。
    adj_close_raw: "pd.Series | None" = None
    if ADJ_CLOSE_COLUMN in raw.columns:
        adj_close_raw = raw[ADJ_CLOSE_COLUMN]
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

    # `Adj Close` 必须跟着**同一个排序置换**走 —— 它是在排序前按原始行序
    # 读下来的。用 `df.index`(reset 之前的陈旧 RangeIndex)重取即可:
    # 那正是「排序后的第 i 行来自原始的第几行」。漏掉这一步会在乱序 CSV 上
    # 把因子配错 bar,而这恰是 ADR-019 允许存在的输入形态。
    if adj_close_raw is not None:
        adj_close_raw = adj_close_raw.reindex(df.index)

    # index 必须重建为 0..n-1 连续 —— 无条件执行,不只在排序分支里:
    # pandas 排序后留下的陈旧 RangeIndex 会让 iloc/loc 分叉,
    # 之后每个 i+1 查找都读错 bar(T1/T5 的门都断言这一条)。
    df = df.reset_index(drop=True)
    if adj_close_raw is not None:
        adj_close_raw = adj_close_raw.reset_index(drop=True)

    # ── T21:复权(ADR-050/051/052/054)───────────────────────────────────
    #
    # `events` 是路径形态时在这里才解析 —— 此刻 `df["Date"]` 已校验、已排序,
    # 正是 `load_events` 的 `price_dates` 要的东西(T20 门 10 的区间过滤)。
    resolved = events
    if isinstance(events, (str, Path)):
        resolved = load_events(events, price_dates=df["Date"])

    return _adjust_prices(df, file_str, events=resolved, adj_close=adj_close_raw)


# ═══════════ 因子法复权(T21 / ADR-050 · 051 · 052 · 054)═══════════


def cumfactors_from_events(
    df: "pd.DataFrame",
    events: "Sequence[CorporateEvent]",
    file_str: str = "<events>",
) -> list[float]:
    """**因子路径二** —— 由公司行为表算累积因子序列(ADR-053 的因子公式)。

    ADR-035 未钉死本名字,但 §4.5 自检规则二要求「门要读的内部序列必须是
    模块级公开名」:T21 的门 4(「严格晚于 t」)与门 9/10(仲裁)都要直接
    采样**累积因子序列**本身,而不是调整后的价格 —— 内联它会让那几条门
    结构上无法执行。故它是公开符号。

    ```
    cumfactor_t = ∏ { f_e : 事件 e 的日期 > t }      # 严格【晚于】t
    split    :  f = 1 / ratio                        # 前向股数比(约定 A)
    dividend :  f = 1 − amount / close_before
                close_before ≔ 除息日【前一交易日】的【原始、未调整】收盘价
    ```

    `close_before` 取**原始**值是**因子法的定义性前提**(ADR-053),不是实现
    细节:ADR-054 规定 `amount` 是除息日当时的每股申明毛额(拆股前基准),
    分母必须与分子**同基准**。ADR-053 实测两种读法在「分红在前、拆股在后」
    时差 **−6.09%**,且作用于分红日之前的**每一根** bar、多笔分红复利放大。

    Returns:
        长度 == `len(df)` 的累积因子列表。**最后一个恒为 `1.0`**(后复权)——
        没有任何事件晚于最后一根 bar。

    Raises:
        DataValidationError: ADR-054 的三条门与 ADR-053 的首根 bar 情形。
    """
    dates = [_as_date(d) for d in df["Date"].tolist()]
    # **原始、未经任何因子调整**的收盘价 —— 本函数全程只读它(ADR-053)。
    raw_closes = [float(v) for v in df["Close"].tolist()]

    problems: list[str] = []
    # 每个事件各自的因子 f_e,连同它的日期 —— 之后按「严格晚于」累积。
    factors: list[tuple[_date, float]] = []

    for ev in events:
        if ev.event == "split":
            ratio = float(ev.ratio)              # type: ignore[arg-type]
            if ratio == _NOOP_RATIO:
                # T20 已就此发过 NoOpEventWarning 并把它剔出序列;
                # 这里只是防御 —— 直接调本函数的调用方可能没走 load_events。
                continue
            factors.append((ev.date, 1.0 / ratio))
            continue

        # ── dividend:f = 1 − amount / close_before ───────────────────────
        amount = float(ev.amount)                # type: ignore[arg-type]
        idx = _bar_index_on_or_after(dates, ev.date)

        # ADR-053 / T21 门 7:ex-date 落在第一根 bar → **报错**。
        # **不得退化为用 bar 0 自己的收盘** —— 当天开盘已扣掉分红,
        # 用它当分母等于「用已经调整过的价格再算一次调整」= 二次扣减。
        if idx is None or idx == 0:
            problems.append(
                f"除息日没有前一交易日 date={ev.date.isoformat()} "
                f"column=amount —— `close_before` 取除息日【前一交易日】的"
                f"原始收盘价(ADR-053),而该日期"
                + (
                    "落在价格序列的第一根 bar 上"
                    if idx == 0
                    else "之后没有任何 bar"
                )
                + "。不得退化为用当根 bar 自己的收盘价:除息日开盘已扣掉分红,"
                f"用它当分母是二次扣减"
            )
            continue

        close_before = raw_closes[idx - 1]
        yield_ratio = amount / close_before

        # ── ADR-054 门三:amount >= close_before → 报错 ───────────────────
        # 因子 <= 0 会把之前**全部**历史价格归零或变号。
        # (zipline 对此只是 log-and-drop;按 ADR-019 的响亮失败姿态,我们报错。)
        if amount >= close_before:
            problems.append(
                f"amount >= close_before date={ev.date.isoformat()} "
                f"column=amount amount={amount} close_before={close_before} "
                f"—— 因子 f = 1 − {amount}/{close_before} = "
                f"{1.0 - yield_ratio} <= 0,会把该日之前的全部历史价格"
                f"归零或变号(ADR-054 门三)"
            )
            continue

        # ── ADR-054 门二:量级门 ──────────────────────────────────────────
        # 抓 Yahoo/Sharadar 那类基准错导致的 5 倍膨胀(膨胀后股息率会荒谬)。
        # 运算符取 `>=`,理由同 ADR-051 的边界问题。
        if yield_ratio >= DIVIDEND_YIELD_SUSPICION:
            problems.append(
                f"单笔分红占价格比例过高 date={ev.date.isoformat()} "
                f"column=amount amount={amount} close_before={close_before} "
                f"ratio={yield_ratio} >= {DIVIDEND_YIELD_SUSPICION} —— "
                f"几近必为 `amount` 与 `close_before` **股数基准不一致**"
                f"(ADR-054:Yahoo 的分红项在拆股后被放大、Sharadar 把值重述"
                f"到今日基准)或一笔特别分配。`amount` 须是除息日当时的"
                f"每股申明毛额"
            )
            continue

        factors.append((ev.date, 1.0 - yield_ratio))

    # ── ADR-054 门一:拆股**方向门** —— 本特性价值最高的一条 ───────────────
    # close[ex] / close[ex-1] ≈ 1/ratio。它是**唯一**能抓到约定写反
    # (A 与 B 互为倒数 → 4:1 读成 0.25 造成 16 倍误差)的机制 ——
    # **没有任何格式级校验能发现它**(ADR-053 原话)。
    for ev in events:
        if ev.event != "split":
            continue
        ratio = float(ev.ratio)                  # type: ignore[arg-type]
        if ratio == _NOOP_RATIO:
            continue
        idx = _bar_index_on_or_after(dates, ev.date)
        if idx is None or idx == 0:
            # 没有 ex-1 可比 → 方向门在此事件上不可执行(不是失败:
            # 行为表可以覆盖比价格切片更长的区间,T20 门 10 已有先例)。
            continue
        observed = raw_closes[idx] / raw_closes[idx - 1]
        expected = 1.0 / ratio
        # **不可按精确值断言** —— 厂商有浮点噪声(ADR-054 原话)。
        if abs(observed - expected) > SPLIT_DIRECTION_TOL * max(expected, 1.0):
            problems.append(
                f"split 方向门不成立 date={ev.date.isoformat()} column=ratio "
                f"ratio={ratio} —— 除权日价格比 close[ex]/close[ex-1] = "
                f"{observed}(实测),而 ratio={ratio} 预期 1/ratio = {expected}。"
                f"最可能的原因:`ratio` 用了**约定 B(价格乘数)**而非 ADR-053 "
                f"钉死的**前向股数比(约定 A)**—— 两者互为倒数,4:1 拆股写成 "
                f"0.25 会对拆股前的全部历史造成 16 倍误差且不抛任何异常。"
                f"4:1 拆股应写 4.0,1:8 合股应写 0.125"
            )

    if problems:
        raise DataValidationError(
            _format_message(file_str, problems),
            file=file_str,
            column="amount" if any("amount" in p for p in problems) else "ratio",
        )

    # ── 累积:cumfactor_t = ∏ { f_e : date_e > t } ────────────────────────
    #
    # **严格晚于 t** —— 除权除息日**当根** bar 不被自己的事件调整
    # (它本就已按新基准交易)。经三份独立实现确认为学术标准:
    # CRSP 的 `CUMFACPR`(`EXDT > d`)、一份 Tiingo 参考实现
    # ("never divide ex-date")、Sharadar 的 `cumprod(ratios[::-1])[::-1]`
    # + `searchsorted(side='right')`。
    # ⚠️ zipline 反其道而行(`Float64Multiply` 含 `effective_date` 当行)
    # —— 同名列,差一天,故 zipline 格式的调整表不能逐日照搬。
    #
    # 实现直接照抄定义,**逐 bar 只问一个谓词**:`date_e > dates[i]`。
    # 不走「从后往前累乘 + 在 bar 上匹配事件日」那条路 —— 那条路要额外处理
    # 「事件日落在区间内的非交易日」(`==` 匹配不到它),而多一个分支就多一处
    # 差一天的地方。这里的 `>` 对两种情形是**同一行代码**。
    cumfactors: list[float] = []
    for bar_date in dates:
        cf = 1.0
        for ev_date, f in factors:
            if ev_date > bar_date:          # ← 「严格晚于」就是这一个符号
                cf *= f
        cumfactors.append(cf)

    return cumfactors


def cumfactors_from_adj_close(
    df: "pd.DataFrame", adj_close: "pd.Series"
) -> list[float] | None:
    """**因子路径一** —— `Adj Close` 反解(ADR-052)。

    ```
    factor_t = AdjClose_t / Close_t
    ```

    该列**已把拆股与分红一起折平**,是最常见的来源(Yahoo 等导出默认带它)。
    **同日多事件的口径问题在这条路径上物理上不存在** —— 数据源内部已处理完,
    我们拿到的是结果。

    与路径二同为**累积**因子序列(ADR-052 的仲裁比的就是这个量),且同样
    **后复权**:这里显式按最后一行归一化,使两路产出**同一组绝对价格**。
    Yahoo 式的 `Adj Close` 本就满足末行比值为 1,归一化只是把厂商的微小
    漂移吸收掉 —— 没有它,一份整体差一个常数的 `Adj Close` 会让仲裁误报。

    Returns:
        累积因子列表,**最后一个恒为 `1.0`**;该列不可用(全 NaN / 含非正值
        / 对应 `Close` 非正)时返回 `None` —— 由调用方当作「路径一缺席」。
        **不报错**:ADR-052 把两条路径都列为**可选**,一列废数据不该让一份
        本来能跑的价格文件跑不起来(§7 第 1 条的加性原则)。
    """
    adj = pd.to_numeric(adj_close, errors="coerce")
    closes = pd.to_numeric(df["Close"], errors="coerce")
    if adj.isna().any() or (adj <= 0).any():
        return None
    if closes.isna().any() or (closes <= 0).any():
        return None

    ratios = [float(a) / float(c) for a, c in zip(adj.tolist(), closes.tolist())]
    last = ratios[-1]
    if last <= 0:
        return None
    # 后复权归一化:最新一行的 cumfactor 恒为 1.0(ADR-050)。
    return [r / last for r in ratios]


def _as_date(value: Any) -> _date:
    """把一个 bar 的 `Date` 规范成 `datetime.date`(日历日)。

    与 ADR-053 的 `date` 口径一致:行为表按**日历日**解析,故比较的两侧
    都必须是 `date`。拿 `Timestamp` 直接和 `date` 比会在任何带时刻的
    中间表示上把除息日整体移一天 —— 正是 ADR-053 禁止的那个入口。
    """
    return pd.Timestamp(value).date()


def _bar_index_on_or_after(dates: "list[_date]", target: _date) -> int | None:
    """首个 `>= target` 的 bar 下标;没有则 `None`。

    除息日**可能不是**交易日(行为表来自厂商,未必对齐本价格切片的日历)。
    此时「除息日当根 bar」的自然定义是**其后第一根** bar —— 它是按新基准
    交易的第一个 session,正是 ADR-053 给 `date` 列的语义。
    故 `close_before` 取它的**前一根**,仍是「除息日前一交易日」。
    """
    for i, d in enumerate(dates):
        if d >= target:
            return i
    return None


def _adjust_prices(
    df: "pd.DataFrame",
    file_str: str,
    *,
    events: "Sequence[CorporateEvent] | None",
    adj_close: "pd.Series | None",
) -> "pd.DataFrame":
    """两路仲裁 + 全列同乘 + 跳空体检(ADR-050/051/052)。

    仲裁规则(ADR-052 的表,逐行照做):

    | 情形 | 行为 |
    |---|---|
    | 仅 `Adj Close` | 用路径一 |
    | 仅行为表 | 用路径二 |
    | 两路并存且一致 | **用行为表**(信息更多,可审计) |
    | 两路并存且不一致 | `DataValidationError`,指明哪一天、差多少 |
    | 两路缺席 | 不调价 + 跳空体检(ADR-051) |
    """
    # 空事件序列 ≡ 路径二缺席:`--events` 指向一份只有表头的合法行为表时
    # (T20 门 9),不该把它当成「有行为表」而跳过体检。
    path_two = (
        cumfactors_from_events(df, events, file_str) if events else None
    )
    path_one = (
        cumfactors_from_adj_close(df, adj_close) if adj_close is not None else None
    )

    # ── 两路并存:逐日比**累积因子**的最大相对偏差(ADR-052)──────────────
    # 比的是 `cumfactor_t`,**不是**逐 bar 的 `factor_t`,也**不是**调整后的
    # 价格 —— ADR-052 明文钉死了被比的那个量。
    if path_one is not None and path_two is not None:
        worst_i, worst_dev = -1, 0.0
        for i, (a, b) in enumerate(zip(path_one, path_two)):
            denom = abs(b) if b != 0 else 1.0
            dev = abs(a - b) / denom
            if dev > worst_dev:
                worst_i, worst_dev = i, dev
        # 运算符取 `>=`,理由同 ADR-051 的边界问题。
        if worst_dev >= FACTOR_AGREEMENT_TOL:
            day = _as_date(df["Date"].iloc[worst_i]).isoformat()
            raise DataValidationError(
                f"{file_str}: 两条因子路径不一致 date={day} column=Close "
                f"—— `Adj Close` 反解得累积因子 {path_one[worst_i]},"
                f"行为表得 {path_two[worst_i]},相对偏差 {worst_dev} >= "
                f"{FACTOR_AGREEMENT_TOL}。两个独立来源对不上**本身就是**"
                f"「假的赚钱」的 signal(ADR-052),静默选一个等于把它藏起来。"
                f"可能的原因:行为表漏了一笔事件、`ratio` 写反、"
                f"或漏了一笔分红(漏一笔拆股是**数量级**偏差,"
                f"漏一笔分红约 0.5%~2%)",
                file=file_str,
                row=worst_i,
                column="Close",
            )

    # 两路一致 → **用行为表**(ADR-052:信息更多,带事件类型与金额,可审计)。
    cumfactors = path_two if path_two is not None else path_one

    # ── 两路缺席 → **不调价**,但体检隔夜跳空(ADR-051)─────────────────────
    if cumfactors is None:
        _gap_health_check(df, file_str)
        return df

    # ── 全列同乘:OHLC 四列乘**同一个**因子,绝不半复权(ADR-050)──────────
    #
    # 为什么不能直接把 `Close` 换成 `Adj Close`:两列不在同一个价格体系里。
    # 而 ADR-002 让决策读 `Close_t`、成交读 `Open_{t+1}`,所以半复权序列会在
    # **每一根** bar 上注入一个**假的隔夜跳空**,而不只是在事件日出错 ——
    # 那比完全不复权更糟。全列同乘是唯一能保住 ADR-001 主张 A 的做法。
    #
    # `Volume` **不**在此列(ADR-050 重申):zipline 会把 volume 除以 ratio,
    # 我们只乘 OHLC 四列。故复权后 `Volume` 与价格序列不再自洽 ——
    # 引擎从不读它,但不得将来有人拿它算成交额。
    out = df.copy()
    factor_series = pd.Series(cumfactors, index=out.index, dtype=float)
    for column in PRICE_COLUMNS:
        out[column] = out[column].astype(float) * factor_series

    return out


def _gap_health_check(df: "pd.DataFrame", file_str: str) -> None:
    """两条因子路径均缺席时的隔夜跳空体检(ADR-051)。

    ```
    |Open_t / Close_{t-1} − 1| >= GAP_SUSPICION_PCT  →  响亮提示「嫌疑未复权」
    ```

    **是提示(warning),不是错误**,且**不调价** —— 它是体检,不是诊断。
    阈值有一个无法消除的张力:常见拆股造成的跳空与真实暴跌在量级上重叠
    (2:1 = −50%、3:2 = −33.3% 都抓得到;6:5 = −16.7%、10:9 = −10% 抓不到;
    而真实暴跌也能到 −20%)。故默认 20% 且**宁可误报**:漏报的代价是一条
    错曲线,误报的代价是一行提示。

    **运算符必须是 `>=`。** 5:4 拆股算出 `-19.999999999999996`,严格 `> 0.20`
    为假;而 `round(x, 2)` 得**恰好** `-20.0`,`> 20.0` 也为假 ——
    **两条路径都漏掉 5:4**,是因为浮点表示而非因为决策。写成 `>` 会
    **静默改变**哪些拆股能被检出。

    口径与 `run.compute_gaps`(ADR-021 / ADR-038)**一致**:先按
    `rounding.decimals` 取整再比 —— 否则同一个 5:4 拆股会在汇总里显示
    `-20.00%` 而体检却说没事,两处对同一根 bar 各说一套。
    """
    opens = df["Open"].tolist()
    closes = df["Close"].tolist()
    dates = df["Date"].tolist()

    hits: list[str] = []
    for i in range(1, len(opens)):
        prev_close = float(closes[i - 1])
        if prev_close <= 0:
            continue
        gap_pct = round((float(opens[i]) / prev_close - 1.0) * 100.0, 2)
        if abs(gap_pct) >= GAP_SUSPICION_PCT:          # ← `>=`,钉死
            hits.append(
                f"{_as_date(dates[i]).isoformat()}({gap_pct:+.2f}%)"
            )

    if not hits:
        return

    warnings.warn(
        f"{file_str}: **嫌疑未复权** —— 检出 {len(hits)} 处 >= "
        f"{GAP_SUSPICION_PCT}% 的隔夜跳空:{', '.join(hits[:10])}"
        + ("…" if len(hits) > 10 else "")
        + "。两条因子路径均缺席(既无 `Adj Close` 列也无 `--events` 行为表),"
        "故**价格未被调整**。4:1 拆股的签名就是一个 −75% 的隔夜跳空,"
        "而 ADR-019 的六条校验没有一条能看见拆股。"
        "**这是体检而不是诊断**:真实暴跌也能到这个量级(宁可误报,ADR-051)。"
        "要复权请提供 `Adj Close` 列或经 `--events` 传入公司行为表",
        UnadjustedPriceWarning,
        stacklevel=3,
    )


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
