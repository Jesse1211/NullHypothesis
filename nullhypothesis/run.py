"""T5 · 主循环 + 跳空统计 + Summary。

本模块是 §1 聚合根「推进时间」的那一半:`Backtest`(engine.py)持有账本与
订单队列并守 I1/I2/I4/I7,而这里把它按日推进,产出 ADR-044 的发布契约。

**整个 T+1 成交语义就是主循环里这两行的顺序**(ADR-002):

    resolve_queue(bar)      # A 结算【昨天】的意图
    strategy.next()         # B 问【今天】的意图

调换 A 与 B,语义立刻退化为同一根 K 线成交 —— 不报错、曲线照画。
三个参考框架独立采用同一模式(backtesting.py 的 broker 先于 strategy、
zipline 的 blotter 先于 handle_data、PyAlgoTrade 的 "It is VERY important
that the broker subscribes to barfeed events before the strategy")。

值对象(ADR-044)在此定义:EquityPoint / Summary / RunRequest / RunResult /
RunReport。`RunReport` 由 CLI 层组装(ADR-035 的 backtest.build_report)——
`run_id` 只有在归档目录独占 mkdir 成功后才确定,聚合根无从知道。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from .engine import Backtest, Fill, ShareOrder, TargetOrder, bar_date_str
from .strategy import ShareIntent, Strategy, TargetIntent, load_strategy

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

__all__ = [
    "EquityPoint",
    "Summary",
    "RunRequest",
    "RunResult",
    "RunReport",
    "compute_gaps",
    "run",
    "ROUNDING_DECIMALS",
]

# `contracts.yaml` 的 `rounding.decimals`。进入 Summary 的金融量一律取整 ——
# 不取整的话 T5/T6 的门永不可满足:由真实价格算出的 -8% 跳空浮点是
# -7.9999999999999964,而 16/3 的平均绝对跳空是 5.3333…,**不存在**任何
# 2 位小数值能让 `== 5.33` 成立。
ROUNDING_DECIMALS = 2


def _r(x: float | None) -> float | None:
    """ADR-038 的取整规则。`None` 透传(单行 CSV 时跳空三值为 None)。"""
    return None if x is None else round(float(x), ROUNDING_DECIMALS)


# ═════════════════════════ 值对象(ADR-044)═════════════════════════


@dataclass(frozen=True)
class EquityPoint:
    """逐日账本快照。四个字段 —— ADR-044 钉死。

    `cash`/`shares` 在这里而不是只在 `Account` 里,是因为 §1 声明 `Account`
    **不可被聚合外引用**:T5 的 I3 逐日对账门读的是 `equity[i].cash`,不是
    `account.cash`。ADR-038 的 JSON 投影只序列化 `date` 与 `equity` 两个键。
    """

    date: str
    cash: float
    shares: int
    equity: float


@dataclass(frozen=True)
class Summary:
    """ADR-038 的 summary 块,逐字段对应。

    三个跳空字段可空:单行 CSV 时 `gaps` 为空序列,`max()` 会抛
    `ValueError`,故三值均为 `None`(ADR-021/023),汇总按
    `contracts.yaml` 的 `formats.null_display` 显示。
    """

    start: str
    end: str
    bars: int
    initial_cash: float
    final_equity: float
    total_return_pct: float
    trade_count: int
    max_gap_pct: float | None
    max_gap_date: str | None
    mean_abs_gap_pct: float | None


@dataclass(frozen=True)
class RunRequest:
    """一次运行的输入。ADR-044 钉死四个字段,与 ADR-038 的 `request` 同构。"""

    strategies: list[str]
    data_file: str
    cash: float
    fee: float


@dataclass(frozen=True)
class RunResult:
    """按【策略】的结果 —— 与 ADR-038 的 `results[i]` 同构。

    **没有 `png_path`**:它的值含 `run_id`,而 `run_id` 由 CLI 层的
    `create_archive_dir` 产生,聚合根不拥有输出目录(ADR-044)。

    `trades_ledger` 与 `trades` 等长并行,记每笔成交**后**的 `(cash, shares)`。
    T7 不得 import `account`,故这两列必须由聚合根提供(ADR-037/044)。
    """

    strategy: str
    equity: list[EquityPoint]
    trades: list[Fill]
    trades_ledger: list[tuple[float, int]]
    summary: Summary


@dataclass(frozen=True)
class RunReport:
    """按【运行】的发布契约 —— 与 `summary.json` / `run.json` 同构(ADR-044)。

    由 CLI 层在创建归档目录后组装(ADR-035 的 `backtest.build_report`)。
    T6/T7 **只消费它**,不得遍历内部 `Bar` 列表或 `Account`。
    """

    run_id: str
    request: RunRequest
    results: list[RunResult]
    assumptions: list[str] = field(default_factory=list)


# ═════════════════════════ 跳空统计(ADR-021)═════════════════════════


def compute_gaps(df: "pd.DataFrame") -> list[float]:
    """`Open_t / Close_{t-1} - 1`,百分比,已按 ADR-038 取整。

    `t` 从**第 2 根**起 —— 第 1 根没有「昨天」,故 `len(gaps) == 交易日数 - 1`。
    这条边界是 `Open_0 / Close_{-1}` 那类错误的拦截点,T5 的门直接断言它。
    """
    opens = df["Open"].tolist()
    closes = df["Close"].tolist()
    return [
        round((opens[i] / closes[i - 1] - 1.0) * 100.0, ROUNDING_DECIMALS)
        for i in range(1, len(opens))
    ]


def _gap_stats(df: "pd.DataFrame") -> tuple[float | None, str | None, float | None]:
    """(max_gap_pct, max_gap_date, mean_abs_gap_pct)。

    * **按绝对值选取,按原符号显示** —— `max()` 而非 `max(abs())` 是最易犯的错
    * **平手取较早的日期**(ADR-021)—— 否则实现成「`max()` 碰巧返回哪个就哪个」
    * **均值取绝对值平均** —— 带符号平均在真实数据上接近 0,看起来合理却无意义
    """
    gaps = compute_gaps(df)
    if not gaps:
        return None, None, None

    # `max` 对相等的键保留**最先**出现的那个 → 平手自动取较早,与 ADR-021 一致。
    best = max(range(len(gaps)), key=lambda i: abs(gaps[i]))
    dates = df["Date"].tolist()
    # gaps[i] 落在第 i+1 根 K 线上(gap 从第 2 根起)。
    return (
        _r(gaps[best]),
        bar_date_str(dates[best + 1]),
        _r(sum(abs(g) for g in gaps) / len(gaps)),
    )


# ═════════════════════════ 主循环 ═════════════════════════


def _run_one(
    strategy_cls: type[Strategy],
    strategy_name: str,
    df: "pd.DataFrame",
    cash: float,
    fee: float,
) -> RunResult:
    bt = Backtest(cash=cash, fee=fee)
    strat = strategy_cls()

    # ADR-036:init() 在第 0 根之前恰调用一次,此时 data 零行、equity == cash。
    strat._enter_init(data=df.iloc[0:0].copy(), cash=cash)
    strat.init()
    strat._exit_init()

    equity: list[EquityPoint] = []
    trades: list[Fill] = []
    ledger: list[tuple[float, int]] = []
    dates = df["Date"].tolist()
    closes = df["Close"].tolist()

    for i, bar in enumerate(df.itertuples(index=False)):
        # ── 阶段 A · 开盘:结算【昨天】的意图(ADR-002)────────────────
        #
        # 第 1 根时队列是空的(没有昨天),故第一笔成交最早在第 2 根。
        fill = bt.resolve_queue(bar)
        if fill is not None:
            trades.append(fill)
            ledger.append((bt.cash, bt.shares))

        # ── 阶段 B · 收盘:问【今天】的意图 ──────────────────────────
        #
        # I5:传出去的是 Bars[0..i] 的**独立副本**。诚实边界见 §1 —— 框架只
        # 保证自己传出去的是副本,策略自行越界取数不在防护范围内,不做沙箱。
        close = float(closes[i])
        date = bar_date_str(dates[i])
        strat._enter_next(bar_index=i, date=date)
        strat._bind_data(df.iloc[: i + 1].copy())
        strat._bind_account(
            cash=bt.cash, shares=bt.shares, equity=bt.equity_at(close)
        )
        strat.next()
        strat._exit_next()

        bt.enqueue_intent(strat.pending_intent)

        # ── 记 EquityPoint(I3/I6)────────────────────────────────────
        equity.append(
            EquityPoint(
                date=date,
                cash=bt.cash,
                shares=bt.shares,
                equity=bt.equity_at(close),
            )
        )

    # 最后一根 K 线上入队的意图**被静默丢弃**(ADR-005)—— 循环结束后不再
    # resolve。这不是 bug,是 T+1 成交语义的必然推论:兑现它需要一个不存在
    # 的明天。

    initial = float(cash)
    final = equity[-1].equity if equity else initial
    max_gap, max_gap_date, mean_abs_gap = _gap_stats(df)

    summary = Summary(
        start=bar_date_str(dates[0]),
        end=bar_date_str(dates[-1]),
        bars=len(df),
        initial_cash=_r(initial),  # type: ignore[arg-type]
        final_equity=_r(final),  # type: ignore[arg-type]
        # 已乘 100(ADR-038)。前端只追加 `%`,不做这步乘法。
        total_return_pct=_r((final / initial - 1.0) * 100.0),  # type: ignore[arg-type]
        trade_count=len(trades),  # ADR-042:交易次数 ≔ len(fills)
        max_gap_pct=max_gap,
        max_gap_date=max_gap_date,
        mean_abs_gap_pct=mean_abs_gap,
    )

    return RunResult(
        strategy=strategy_name,
        equity=equity,
        trades=trades,
        trades_ledger=ledger,
        summary=summary,
    )


def run(
    strategy_paths: Sequence[str | Path],
    df: "pd.DataFrame",
    cash: float,
    fee: float = 0.0,
) -> list[RunResult]:
    """按 ADR-035 钉死的签名。每条策略独立跑一遍,互不影响。

    **不校验 stem 唯一性** —— 那属 CLI 层(ADR-037/039)。故 `run()` 接受
    同一个策略文件两次,而 CLI 会拒绝。T5 的多策略隔离门正是靠这一点:
    用两个**文件名不同、内容相同**的策略断言产出两条相同曲线。
    """
    results: list[RunResult] = []
    for p in strategy_paths:
        path = Path(p)
        # 每条策略都重新 load —— 不复用类对象,避免模块级可变状态串味。
        cls = load_strategy(path)
        results.append(_run_one(cls, path.stem, df, cash, fee))
    return results
