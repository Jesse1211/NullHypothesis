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
from .errors import StrategyError
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
    "compute_max_drawdown",
    "compute_sharpe",
    "run",
    "ROUNDING_DECIMALS",
    "SHARPE_DEGENERATE_EPS",
    "SHARPE_REPORTABLE_CEILING",
    "TRADING_DAYS_PER_YEAR",
    "RISK_FREE_RATE",
]

# `contracts.yaml` 的 `rounding.decimals`。进入 Summary 的金融量一律取整 ——
# 不取整的话 T5/T6 的门永不可满足:由真实价格算出的 -8% 跳空浮点是
# -7.9999999999999964,而 16/3 的平均绝对跳空是 5.3333…,**不存在**任何
# 2 位小数值能让 `== 5.33` 成立。
ROUNDING_DECIMALS = 2


def _r(x: float | None) -> float | None:
    """ADR-038 的取整规则。`None` 透传(单行 CSV 时跳空三值为 None)。"""
    return None if x is None else round(float(x), ROUNDING_DECIMALS)


# ═══════════ 风险指标的命名常量(ADR-058)═══════════
#
# 三者都【必须】是命名常量而非散落的字面量 —— T23 门 16 直接断言这一点。

# 夏普的退化阈值。**不可写成 `sigma == 0`**:完美的恒定比率增长在浮点里
# 不给 0 —— `[100.0, 110.0, 121.0, 133.1]` 的 `sigma(ddof=1)` 是 1.2832e-16,
# `== 0.0` 为假,于是裸算输出 Sharpe = 1.237e16。而它还**依赖数据规模**:
# 同比例序列起点换成 1e6 时 sigma 恰好是 0.0,守卫反而触发 —— 同一个 bug
# 在某些输入上发作、另一些上不发作(T7 与 T8 配对钉死这条规模无关性)。
SHARPE_DEGENERATE_EPS = 1e-12

# 年化因子。**硬编码 252 并绑死日线**(ADR-058)—— 与 ADR-021 的跳空统计
# 同构,靠 ADR-059 的声明守护:喂周线会无声放大约 2.2 倍,框架无从检测
# K 线周期。换 K 线粒度时这两条 ADR 都必须重审。
TRADING_DAYS_PER_YEAR = 252

# 无风险利率取 0,**不加 CLI 参数**(ADR-058)。跨区间的固定 rf 标量本身
# 就是错的(2010 年代 ≈ 0.2% vs 2023 ≈ 5%),给错比不给更危险 —— 它看
# 起来精确。偏差方向已知(夏普偏高)且由 ADR-059 公开声明。
RISK_FREE_RATE = 0.0

# ADR-058「处置(两条都要)」第 1 条的**绝对上限兜底**:
#   `abs(sharpe) > SHARPE_REPORTABLE_CEILING` → `None`
# 它关闭的是**整个类别**(一切「大到不值得上报」的夏普),而非一个实例 ——
# `SHARPE_DEGENERATE_EPS` 只拦浮点噪声,拦不住 ADR-058 实测反例那一类:
# 持仓余现金(`shares=999, cash=50.05`,即 ADR-057 修正后 `weight=1.0` 的
# **常态**)配恒定比率价格时,`cash` 是固定加项使收益率**单调漂移**,σ 落在
# 1e-10 ~ 1e-6 —— 高出 1e-12 守卫三个数量级,故**不被抑制**,裸算输出
# 1e5 ~ 1e7 量级的夏普。
#
# **暴露面的实测边界(本任务实测,勿读作「用户可见」)**:上述 1e5 ~ 1e7
# 经**本纯函数**可达;但**经真实 CLI 实测不可达** —— ADR-002 的 T+1 语义
# 总会前置一根持现金的平盘 bar,其收益率**精确为 `0.0`**,注入主导 σ,
# 使夏普只按 ≈√n 增长(实测 n=2520→796、n=10000→1587、n=40000→3175,
# 在可表示日期跨度内最高约 6e3,**从未越过 1e4**;同一条 equity 去掉那根
# 平盘 bar 则给 6.3e7)。故封顶的依据是**纯函数层的暴露**,不是用户可见破口。
#
# ⚠️ **取值是 OQ-17,设计期【未决】,且 DESIGN.md §14 明文规定
#    「构建期不得自行选值」**(须与 ADR-059 的封顶声明一并定稿 ——
#    封顶本身也是一种偏差)。故这里**只建成机制、不填数值**:
#    `None` 是「尚未裁决」的显式编码,**不是**「无上限」这个决定。
#    守卫在 `None` 时惰性(见 `compute_sharpe` 末段),行为与 OQ-17 落锁前
#    完全一致 —— 本常量不改变任何现有输出。
#
# OQ-17 落锁后要做的**全部**工作:把下面的 `None` 换成裁决出的数值
# (ADR-058 建议 `100`),并摘掉 T23 门 9b 的 xfail 标记
# (`strict=True` 会因「意外通过」自动迫使这一步)。
SHARPE_REPORTABLE_CEILING: float | None = None


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
    # ADR-049:当日收盘价。**不是**派生量 —— 它就是 `equity_at` 的那个入参,
    # 记在这里是为了让前端能在同一张图上画价格对照曲线(买入持有时两条
    # 形状必须全同,那是一个肉眼可见的正确性检查),而 ADR-025 禁止前端
    # 自己从别处取价。放在末尾且有默认值:前四个字段的顺序被 ADR-044
    # 钉死,T5/T6/T7 的门大量使用位置构造。
    close: float = 0.0


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
    # ── 以下四个字段由 ADR-058 【追加在末尾】,均带默认值 ──
    #
    # **追加而非插入**:ADR-037 钉死了字段【顺序】,而既有门大量使用位置
    # 构造 —— 插在中间会让位置参数静默错位。手法与 ADR-046 补 `Fill.amount`
    # 时逐字一致(那条也是「末尾 + 默认值」)。
    #
    # 默认 `None` 而非必填,是为了保持加性/向后兼容:既有门(如
    # `tests/test_report_plot.py::_summary` )按关键字构造 `Summary` 且不传
    # 这四个字段,必填会让它们在**本任务之外**红掉。
    sharpe: float | None = None                    # 裸比率 1.23,**不乘 100**
    max_drawdown_pct: float | None = None          # 百分数,**正数表跌幅**
    max_drawdown_peak_date: str | None = None      # YYYY-MM-DD
    max_drawdown_trough_date: str | None = None


@dataclass(frozen=True)
class RunRequest:
    """一次运行的输入。ADR-044 钉死四个字段,与 ADR-038 的 `request` 同构。

    阶段三**追加在末尾**,顺序依 ADR-044 的字段表逐字:`events`(ADR-053 / T20)
    在前、`slippage`(ADR-056 / T22)在后。追加而非插入的理由同 ADR-046 的
    `Fill.amount` —— 既有门大量使用**位置构造**,插在中间会让位置参数静默错位。
    两个新字段都有默认值(`None` = 无行为表 / `0.0` = 无滑点),故既有调用点
    (`RunRequest(strategies, data_file, cash, fee)`)**一字不改**仍合法,
    不传它们时行为与阶段二**逐点相同**(§7 第 1 条的加性原则)。

    *(本类由 T20 与 T22 共享。两者各自分支上都只有自己那一个字段,
    合并时必须 rebase 并**保留双方字段**,见 T22 门 17 / T23 门 19。
    此处已 rebase 到含 `events` 的 trunk 之上,双方字段俱在。)*
    """

    strategies: list[str]
    data_file: str
    cash: float
    fee: float
    # ── 以下两个字段由 ADR-053 / ADR-056(阶段三)追加在末尾 ──
    events: str | None = None                # 行为表文件名;None = 无行为表
    slippage: float = 0.0                    # 滑点率,与 fee 同口径(0.0005 = 5bps)


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


# ═══════════════════ 风险指标(ADR-058)═══════════════════


def _simple_returns(equity: Sequence[float]) -> list[float]:
    """简单收益 `r[k] = equity[k+1] / equity[k] - 1`,`k = 0 .. n-2`。

    **简单收益,非对数收益。** 理由不是与 `total_return_pct` 口径一致,而是
    **所有参考实现都用简单收益**(empyrical 用 `pct_change`)—— 对数夏普无法
    与任何公开数字比较,那会让这个指标失去唯一的用途。

    **入参是 `equity`,绝不是 `close`**(ADR-058)—— 从 `close` 算的话同一
    份 CSV 上**所有策略的夏普都相同**,那是荒谬的。
    """
    return [
        equity[k + 1] / equity[k] - 1.0
        for k in range(len(equity) - 1)
    ]


def compute_sharpe(equity: Sequence[float]) -> float | None:
    """年化夏普比率(ADR-058)。数学上未定义时返回 `None`,**绝不返回 NaN**。

        Sharpe = mean(r) / std(r, ddof=1) × √252         (rf = 0)

    返回 `None` 而非 `NaN`:**`NaN` 不是合法 JSON**,会打断前端;`None` →
    `null` → 按 `contracts.yaml` 的 `formats.null_display` 显示。

    **退化判定按 ADR-058 的规范顺序**,顺序本身是契约的一部分:

    1. `len(r) < 2` → `None`,**短路,不调用 std**。2 行 CSV 两种 `ddof` 都
       退化(`ddof=1` 给 `nan`、`ddof=0` 给 `0/0`)。
    2. `sigma <= SHARPE_DEGENERATE_EPS * max(1.0, abs(mean))` → `None`。
       `max(1.0, ·)` 使 `mean == 0` 时判定式仍然成立。
    3. `abs(sharpe) > SHARPE_REPORTABLE_CEILING` → `None`(ADR-058「处置」
       第 1 条的绝对上限兜底)。**上限取值是 OQ-17,设计期未决且构建期
       不得自行选值**,故该常量现为 `None` 且本级守卫惰性 —— 机制在位、
       数值待裁决。见 `SHARPE_REPORTABLE_CEILING` 的注释与 T23 门 9b。
    4. 否则给真实数字。

    `sigma == 0 且 mean != 0` → `None` 而不是 `+inf`:无限夏普字面为真
    (无风险套利)但无操作价值,只能来自合成数据。

    **策略从不交易 → `None`。** 这是 `0/0`,数学上未定义。返回 `0.0` 是一种
    *特定的谎言*:`0.0` 意思是「承担了风险但没赚到超额收益」,而真相是
    「**没承担风险**,比率未定义」。

    **交易但回到起点 → 真实数字,非 `None` 非 `0.0`。** `[100,150,100]` 给
    `r = [0.5, -0.3333]`、`sharpe = +2.24` —— 总收益为 0 而夏普为正,是算术
    平均的波动拖累效应。数字按定义正确但反直觉,故进 ADR-059 的声明。
    """
    if len(equity) < 2:
        return None

    r = _simple_returns(equity)

    # ── 退化 ① ── 短路:**不得**调用 std(它会给 nan)
    if len(r) < 2:
        return None

    mean = _fmean(r)

    # **`ddof=1` 字面写出**,不依赖任何库的默认值(T23 门 17)。
    # `np.std` 默认 `ddof=0`、`pd.Series.std` 默认 `ddof=1` —— 实测
    # `[0.01, 0.02, 0.03]` 分别给 0.00816 与 0.01。写「标准差」是歧义的;
    # 不加限定的 `np.std` 会把夏普按 √((n-1)/n) **静默放大**
    # (T6 门:正确实现给 4.58,`ddof=0` 给 5.61)。
    # empyrical 与 quantstats 都用 `ddof=1`。
    sigma = _stdev(r, ddof=1, mean=mean)

    # ── 退化 ② ── **不可写 `sigma == 0.0`**,见 SHARPE_DEGENERATE_EPS 的注释
    if sigma <= SHARPE_DEGENERATE_EPS * max(1.0, abs(mean)):
        return None

    # rf = 0 故分子就是 mean(r) 本身 —— 常量显式参与,使「rf 去哪了」可读。
    excess = mean - RISK_FREE_RATE
    sharpe = excess / sigma * math.sqrt(TRADING_DAYS_PER_YEAR)

    # ── 退化 ③ ── ADR-058「处置」第 1 条:绝对上限兜底。
    # 取值是 OQ-17,设计期未决且**构建期不得自行选值**,故常量现为 `None`、
    # 本守卫**惰性**(不改变任何现有输出)。机制已就位:OQ-17 落锁时只需给
    # `SHARPE_REPORTABLE_CEILING` 填上裁决值,无需再碰这里的逻辑。
    if (
        SHARPE_REPORTABLE_CEILING is not None
        and abs(sharpe) > SHARPE_REPORTABLE_CEILING
    ):
        return None

    return sharpe


def _fmean(xs: Sequence[float]) -> float:
    """算术平均。独立出来只为让 `compute_sharpe` 的分子分母读起来对称。"""
    return sum(xs) / len(xs)


def _stdev(xs: Sequence[float], *, ddof: int, mean: float) -> float:
    """样本标准差,**`ddof` 是必填关键字参数** —— 调用处无法「忘记」指定它。

    这不是风格洁癖:本仓库的两个候选库默认值**相反**(`np.std` 为 0、
    `pd.Series.std` 为 1),而 ADR-058 钉死 `ddof=1`。把 `ddof` 做成必填
    关键字,使「依赖默认值」在语法上不可表达。
    """
    n = len(xs)
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - ddof))


def compute_max_drawdown(
    equity: Sequence[float],
    dates: Sequence[str],
) -> tuple[float, str | None, str | None]:
    """(max_drawdown_pct, peak_date, trough_date)。ADR-058。

    * **running max 含当前点**(inclusive)—— 新高处 `dd == 0`,永不为正。
      empyrical 用 `np.fmax.accumulate`、quantstats 用 `expanding().max()`,
      与此一致。
    * **平手取较早**(严格 `<`),与 ADR-021 的 `max_gap_pct` 平手规则一致。
      `[100,90,100,90]` 的谷底取 **bar1** 而非 bar3。
    * **峰值起点取 `equity[0]`,不取 `initial_cash`。** 两者可证明相等 ——
      由 ADR-002 的 T+1 语义强制(bar 0 上 `resolve_queue` 见空队列,首个
      `EquityPoint` 记录前不可能有成交)。**不要**把 `initial_cash` 穿进来
      「修正」它:不可能改变任何结果,只会引入一个分歧源。
    * **单调上涨 / 恒定净值 / 单行 CSV → `0.0`,但两个日期 `None`** ——
      给日期等于断言发生了一次没发生的回撤。
    * **与夏普的不对称是有意且有原则的**:「未发生下跌」是**确定事实**
      (`0.0`),而「零风险下的单位风险收益」是**未定义**(`None`)。
    * **不需要 epsilon**(实测 `[100*1.1**i]` 的回撤精确等于 `0.0`)——
      它是比较与 running-max,从不做近邻量相减。不要对称地也加一个,
      那是无意义的模仿。
    """
    if not equity:
        return 0.0, None, None

    run_max = equity[0]
    worst = 0.0
    peak_i: int | None = None
    trough_i: int | None = None
    cur_peak_i = 0

    for i, point in enumerate(equity):
        if point > run_max:
            run_max = point
            cur_peak_i = i
        dd = point / run_max - 1.0        # 恒 <= 0
        if dd < worst:                    # 严格 `<` ⇒ 平手取较早
            worst = dd
            peak_i = cur_peak_i
            trough_i = i

    # **必须用 `abs(worst)`,不可写 `-worst`。** `worst` 初值 `0.0`,单调上涨
    # 或恒定净值时它从未被重新赋值,于是 `-worst` 得 **`-0.0`**:
    # `json.dumps(-0.0)` 输出 `'-0.0'`、`'{:.2f}'.format(-0.0)` 输出 `'-0.00'`
    # —— 屏幕上出现 `-0.00%`,正是本 ADR 选正数约定时要避免的「负号 + 带符号
    # 格式化器 → 双重负号」显示 bug。14 条 fixture 里有 4 条命中,而
    # `-0.0 == 0.0` 与 `-0.0 >= 0.0` 在 Python 里都为 `True`,**没有一条
    # 只断言 `>= 0.0` 的门会红** —— 故门必须断言符号位。
    max_drawdown_pct = abs(worst) * 100.0

    return (
        max_drawdown_pct,
        None if peak_i is None else dates[peak_i],
        None if trough_i is None else dates[trough_i],
    )


# ═════════════════════════ 主循环 ═════════════════════════


def _run_one(
    strategy_cls: type[Strategy],
    strategy_name: str,
    df: "pd.DataFrame",
    cash: float,
    fee: float,
    slippage: float = 0.0,
) -> RunResult:
    # ADR-056:滑点率一路透传到聚合根 —— 成交价的偏移只能发生在成交那一层
    # (`resolve_queue`),本函数不碰价格。
    bt = Backtest(cash=cash, fee=fee, slippage=slippage)
    strat = strategy_cls()

    # ADR-036:init() 在第 0 根之前恰调用一次,此时 data 零行、equity == cash。
    strat._enter_init(data=df.iloc[0:0].copy(), cash=cash)
    try:
        strat.init()
    except StrategyError:
        raise                                  # 已是契约异常,不再包一层
    except Exception as exc:                   # ADR-022
        raise StrategyError(
            f"策略 {strategy_name} 在 init() 中抛出 {type(exc).__name__}: {exc}",
            strategy=strategy_name, bar_index=-1, date="<init>",
        ) from exc
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
        try:
            strat.next()
        except StrategyError:
            # 策略请求非法值时 Strategy 已按 ADR-022 抛了带上下文的契约异常。
            raise
        except Exception as exc:
            # ADR-022:立即终止。消息含**交易日序号 + 日期**,原始 traceback
            # 由 `__cause__` 携带 —— 策略 bug 被吞掉会产生一条你以为可信
            # 却不可信的曲线,那是 v0 最该避开的失真。
            raise StrategyError(
                f"策略 {strategy_name} 在第 {i + 1} 个交易日({date})抛出 "
                f"{type(exc).__name__}: {exc}",
                strategy=strategy_name, bar_index=i, date=date,
            ) from exc
        strat._exit_next()

        bt.enqueue_intent(strat.pending_intent)

        # ── 记 EquityPoint(I3/I6)────────────────────────────────────
        equity.append(
            EquityPoint(
                date=date,
                cash=bt.cash,
                shares=bt.shares,
                equity=bt.equity_at(close),
                close=close,      # ADR-049:与 equity 同源的那个收盘价
            )
        )

    # 最后一根 K 线上入队的意图**被静默丢弃**(ADR-005)—— 循环结束后不再
    # resolve。这不是 bug,是 T+1 成交语义的必然推论:兑现它需要一个不存在
    # 的明天。

    initial = float(cash)
    final = equity[-1].equity if equity else initial
    max_gap, max_gap_date, mean_abs_gap = _gap_stats(df)

    # ── 风险指标(ADR-058)── 计算在**这里**,不在 `report.py`
    #
    # ADR-044 规定 `report.py` 只渲染不计算,故两个指标必须在 `Summary` 构建
    # 处算完,交给 `report.py` 的只能是已完成全部算术的裸标量。
    #
    # 喂进去的是 `equity` 序列,**不是** `close` —— 见 `_simple_returns`。
    equity_series = [pt.equity for pt in equity]
    equity_dates = [pt.date for pt in equity]
    sharpe = compute_sharpe(equity_series)
    mdd_pct, mdd_peak_date, mdd_trough_date = compute_max_drawdown(
        equity_series, equity_dates
    )

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
        # ADR-058 的四个字段。两个数值字段按 `rounding.fields` 取整
        # (`sharpe` 是**裸比率不乘 100**),两个日期字段不参与舍入。
        sharpe=_r(sharpe),
        max_drawdown_pct=_r(mdd_pct),
        max_drawdown_peak_date=mdd_peak_date,
        max_drawdown_trough_date=mdd_trough_date,
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
    slippage: float = 0.0,
) -> list[RunResult]:
    """按 ADR-035 钉死的签名。每条策略独立跑一遍,互不影响。

    `slippage`(ADR-056)**追加在末尾且有默认值** —— ADR-035 钉死的签名因此
    向后兼容:不传它时行为与阶段二**逐点相同**(`s = 0` 使 `price == Open`)。

    **不校验 stem 唯一性** —— 那属 CLI 层(ADR-037/039)。故 `run()` 接受
    同一个策略文件两次,而 CLI 会拒绝。T5 的多策略隔离门正是靠这一点:
    用两个**文件名不同、内容相同**的策略断言产出两条相同曲线。
    """
    results: list[RunResult] = []
    for p in strategy_paths:
        path = Path(p)
        # 每条策略都重新 load —— 不复用类对象,避免模块级可变状态串味。
        cls = load_strategy(path)
        results.append(_run_one(cls, path.stem, df, cash, fee, slippage))
    return results
