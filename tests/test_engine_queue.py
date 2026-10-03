"""T4 验收门 —— 订单队列与成交。

> 本门的每一条都必须**钉死具体数值**。原门多处写作「断言按 X 重算」—— 那是
> 散文:执行它必须先算出期望值,而算期望值只能复用被测实现的同一公式,结果
> 是同义反复。(§5 T4 的前言,逐字)

故本文件**不调用** `engine` 的任何算术来产生期望值:股数、现金、价格全部
取自 `contracts.yaml` 的 `fixtures.*`(由 `tests/test_contracts.py` 验算过
算术自洽),或在测试内字面写死。

**进程内**(`contracts.yaml` 的 `gate_execution.in_process_required` 列出
`T4.queue_sampling` 与 `T4.push_count`):队列采样与 `push_count` 都要对
`Backtest.enqueue` / `Backtest.resolve_queue` 打桩,`monkeypatch` 跨不过
`subprocess` 边界。本文件全部在进程内跑。

主循环归 T5(§4.5 归属矩阵:`equity[]`/`trades[]` 由 `engine.run` 产出)。
T4 的门不能等 T5,故本文件自带一个**最小驱动器** `drive()` —— 它只做
ADR-002 的那两行(出队先于入队)。它是测试脚手架,不是产品代码:产品的
主循环由 T5 在 `engine.run()` 里写,届时 T5 的门断言它自己的产物。
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from nullhypothesis.engine import (
    Backtest,
    Fill,
    ShareOrder,
    TargetOrder,
    bar_date_str,
)
from nullhypothesis.strategy import ShareIntent, Strategy, TargetIntent

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))
FIX = CONTRACTS["fixtures"]


# ═════════════════════════ 脚手架 ═════════════════════════


@dataclass(frozen=True)
class Bar:
    """测试侧的 K 线。字段名与 T1 的 DataFrame 列名一致(`Open`/`Date`)。

    刻意**只给** `resolve_queue` 用得到的字段加上 `Close` —— `Close` 在这里
    的唯一用途是让 I7 的「四价互异」fixture 能把错误价位也摆在桌上(见
    `test_i7_four_distinct_prices`):若测试侧根本不提供 `Close`,一个按
    `Close` 成交的错误实现会以 AttributeError 失败,而那条门要断言的是
    **价格数值**错,不是属性缺失。
    """

    Date: str
    Open: float
    Close: float = 0.0


def drive(
    bt: Backtest,
    strategy: Strategy,
    bars: list[Bar],
    *,
    on_sample: Any = None,
) -> list[Fill]:
    """最小主循环 —— ADR-002 的那两行,**出队先于入队**。

        for bar in bars:
            resolve_queue(bar)      # A 结算【昨天】的意图
            strategy.next()         # B 问【今天】的意图,入队

    「T+1 成交语义的全部实现就是 A 在 B 之前。」(ADR-002)

    `on_sample(phase, queue)` 在两个时刻被调用:`"before_resolve"`(出队前)
    与 `"after_enqueue"`(入队后)—— 正是 T4 的 I4 门要求的两个采样点。

    ADR-005:循环结束后**不再** resolve。最后一根 K 线上产生的订单因此留在
    队列里无人出队 = 被静默丢弃。这里显式调 `discard_queue()` 把那句语义写
    出来(它不打警告、不抛错)。
    """
    fills: list[Fill] = []
    for i, bar in enumerate(bars):
        if on_sample is not None:
            on_sample("before_resolve", bt.queue)

        # ── A ──
        fill = bt.resolve_queue(bar)
        if fill is not None:
            fills.append(fill)

        # ── B ──
        # I5 的切片由 T5 的主循环负责(它持有 DataFrame);T4 的驱动器只需要
        # 让策略看到「第 i 根已收盘」,故给出长度为 i+1 的轻量切片。
        strategy._bind_data(_slice(bars, i))
        strategy._bind_account(
            cash=bt.cash,
            shares=bt.shares,
            equity=bt.account.equity_at(bar.Close),
        )
        strategy._enter_next(bar_index=i, date=bar.Date)
        strategy.next()
        strategy._exit_next()
        bt.enqueue_intent(strategy.pending_intent)

        if on_sample is not None:
            on_sample("after_enqueue", bt.queue)

    # ADR-005
    bt.discard_queue()
    return fills


class _Slice(list):
    """`self.data` 的替身 —— 只需支持 `len()` 与 `['Close']`。

    T4 不拥有 DataFrame 构造(那是 T1/T5);用真 DataFrame 会把 T4 的门绑到
    T5 尚未写的切片逻辑上。`len(self.data) == bar_index + 1` 的断言
    (「决策侧配对」门)对两者都成立。
    """

    def __getitem__(self, key: Any) -> Any:
        if key == "Close":
            return [b.Close for b in self]
        return list.__getitem__(self, key)


def _slice(bars: list[Bar], upto: int) -> _Slice:
    return _Slice(bars[: upto + 1])


def make_strategy(script: Any) -> Strategy:
    """造一个策略:`script(s, i)` 在第 `i` 根 K 线的 `next()` 里被调用。"""

    class _Scripted(Strategy):
        def next(self) -> None:
            script(self, self._bar_index)

    s = _Scripted()
    s._enter_init(data=_Slice(), cash=0.0)
    s._exit_init()
    return s


def count_pushes(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """对 `Backtest.enqueue` 打桩计数(`gate_execution.monkeypatch_seams`)。

    打在**类**上而不是实例上:`contracts.yaml` 把 `Backtest.enqueue` 列为接缝,
    而 ADR-035 要求它是命名方法 —— 内联 `self.queue.append(...)` 的实现会让
    本计数恒为 0,各条读本函数的门随之变红。这是有意的:那种实现结构上使
    这些门无法执行。

    **口径**(与本文件「ADR-008 三条分解」节顶部的冲突说明配套):本函数数的是
    **入队次数**,而 ADR-016 使 `TargetOrder` 的差额在入队点未知 —— 故它只能
    用来断言「不该下单的东西**没有**入队」(`ShareOrder` 的零差额、ADR-041
    被覆盖掉的那个单),**不能**用来断言「同一 weight 的 target 只入队一次」。
    """
    pushed: list[Any] = []
    original = Backtest.enqueue

    def spy(self: Backtest, order: Any) -> None:
        pushed.append(order)
        original(self, order)

    monkeypatch.setattr(Backtest, "enqueue", spy)
    return pushed


def count_order_constructions_during_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """数 `resolve_queue` **执行期间**被构造出来的 `Order` 对象。

    这是 ADR-002「每日重算」那条门**唯一结构上有效**的测法,理由见
    `test_enqueued_order_object_is_the_same_object_dequeued_next_bar` 的
    docstring。正确实现在 `resolve_queue` 里只**读**昨天出队的那个订单
    (ADR-016:「解析不改写它」),故计数恒为 **0**;任何在成交时刻重建
    /复制订单的实现(`dataclasses.replace`、`TargetOrder(weight=...)`
    再算一遍)计数 >= 1。

    打在 `__init__` 而不是 `__new__` 上:`dataclasses.replace` 走的是
    `__init__`,而那正是实测中唯一逃过原断言的那个变异体的写法。
    """
    constructed: list[str] = []
    inside = False

    def make(cls: type) -> Any:
        original_init = cls.__init__

        def init(self: Any, *args: Any, **kwargs: Any) -> None:
            if inside:
                constructed.append(type(self).__name__)
            original_init(self, *args, **kwargs)

        return init

    for cls in (TargetOrder, ShareOrder):
        monkeypatch.setattr(cls, "__init__", make(cls))

    original_resolve = Backtest.resolve_queue

    def spy(self: Backtest, bar: Any) -> Any:
        nonlocal inside
        inside = True
        try:
            return original_resolve(self, bar)
        finally:
            inside = False

    monkeypatch.setattr(Backtest, "resolve_queue", spy)
    return constructed


# ═════════════════════════ I7 ═════════════════════════


def test_i7_four_distinct_prices():
    """I7 四价互异 fixture(关键门)—— `fixtures.i7_four_distinct_prices`。

    四个候选价(`bars[0].Open` / `bars[0].Close` / `bars[1].Close` / 正确值
    `bars[1].Open`)**全部互异**,故任一种错位必然失败。
    """
    fx = FIX["i7_four_distinct_prices"]
    b0, b1 = fx["bar0"], fx["bar1"]
    bars = [
        Bar(Date="2020-01-02", Open=b0["open"], Close=b0["close"]),
        Bar(Date="2020-01-03", Open=b1["open"], Close=b1["close"]),
        Bar(Date="2020-01-06", Open=1000.0, Close=2000.0),
        Bar(Date="2020-01-07", Open=3000.0, Close=4000.0),
    ]
    order_on = fx["order_on_bar"]
    n = fx["shares"]

    bt = Backtest(cash=100000.0, fee=0.0)
    s = make_strategy(lambda st, i: st.order(shares=n) if i == order_on else None)
    fills = drive(bt, s, bars)

    assert len(fills) == 1
    assert fills[0].date == bars[order_on + 1].Date
    assert fills[0].price == fx["expect_fill_price"]
    for rejected in fx["reject_prices"]:
        assert fills[0].price != rejected


def test_decision_side_pairing_slice_length_per_bar():
    """决策侧配对:逐根断言 `next()` 被调用时 `len(self.data) == bar_index + 1`。

    钉死「策略在第 i 根收盘后被调用」与「成交在第 i+1 根开盘」的配对关系。
    *(仅断言切片长度不够:引擎可以切片长度正确却把策略调早一根)* —— 故本门
    同时断言 `i` 逐根从 0 递增、且成交日期恰好是入队日的**下一根**。
    """
    bars = [Bar(Date=f"2020-01-{d:02d}", Open=100.0, Close=100.0) for d in (2, 3, 6, 7)]
    seen: list[tuple[int, int, str]] = []

    def script(st: Strategy, i: int) -> None:
        seen.append((i, len(st.data), st._bar_date))
        if i == 1:
            st.order(shares=1)

    bt = Backtest(cash=10000.0, fee=0.0)
    fills = drive(bt, make_strategy(script), bars)

    assert [s[0] for s in seen] == [0, 1, 2, 3]
    for i, length, date in seen:
        assert length == i + 1, f"第 {i} 根:切片长度 {length} != {i + 1}"
        assert date == bars[i].Date
    assert len(fills) == 1
    assert fills[0].date == bars[2].Date  # 第 1 根入队 -> 第 2 根成交


# ═════════════════════════ ADR-012 ═════════════════════════


def test_adr012_floor_not_ceil():
    """`fixtures.adr012_floor_vs_ceil`:floor 33 vs ceil 34,现金未被 clamp。"""
    fx = FIX["adr012_floor_vs_ceil"]
    bars = [
        Bar(Date="2020-01-02", Open=1.0, Close=1.0),
        Bar(Date="2020-01-03", Open=fx["open"], Close=fx["open"]),
    ]
    bt = Backtest(cash=fx["cash"], fee=0.0)
    w = fx["weight"]
    s = make_strategy(lambda st, i: st.target(weight=w) if i == 0 else None)
    fills = drive(bt, s, bars)

    assert len(fills) == 1
    assert fills[0].shares == fx["expect_shares"]
    assert fills[0].shares != fx["reject_shares"]
    assert bt.cash == pytest.approx(fx["expect_cash_after"])
    # 未被 clamp、也未留下够买整股的余钱。
    assert bt.cash < fx["one_share_price"]


def test_adr012_floor_zero_shares_is_not_enqueued():
    """`fixtures.adr012_floor_vs_round`:floor 后为 0 股。

    断言 `push_count == expect_push_count`(0)、**无警告**(ADR-012 → ADR-008
    的交接),并**显式断言 `shares` 未变**。同一组数下 `round` 与 `ceil` 均为
    `reject_round`,故此 fixture 同时区分三者。
    """
    fx = FIX["adr012_floor_vs_round"]
    # fixture 自带 raw 值,确认它确实落在 floor / round 分叉处。
    assert math.floor(fx["raw"]) == fx["expect_shares"]
    assert round(fx["raw"]) == fx["reject_round"]
    assert math.ceil(fx["raw"]) == fx["reject_ceil"]

    bars = [
        Bar(Date="2020-01-02", Open=1.0, Close=1.0),
        Bar(Date="2020-01-03", Open=fx["open"], Close=fx["open"]),
    ]
    bt = Backtest(cash=fx["cash"], fee=0.0)
    w = fx["weight"]
    s = make_strategy(lambda st, i: st.target(weight=w) if i == 0 else None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fills = drive(bt, s, bars)

    assert fills == []
    assert bt.shares == 0, "floor 为 0 股时持仓必须未变(ADR-012 → ADR-008)"
    assert caught == [], f"ADR-008 要求无声跳过,实得警告 {[str(w.message) for w in caught]}"


def test_adr012_zero_share_target_never_transacts(monkeypatch: pytest.MonkeyPatch):
    """`expect_push_count == 0` 的那一半 —— 必须对 `enqueue` 打桩才能看见。

    与上一条分开:上一条断言**结果**(无成交、持仓未变、无警告),本条断言
    **入队与出队的配对**。`TargetOrder` 的差额要到 T+1 开盘才知道(ADR-016),
    故 fixture 的 `expect_push_count == 0` **不可能**在入队点满足(见本文件
    「ADR-008 三条分解」节顶部的冲突说明:该 fixture 没钉死 T 日 Close,
    只在 Close 恰等于 Open=1200 时才可满足)。本门因此断言 ADR-008 **第二处
    落实**的可观测后果:订单入队 1 次(意图是真的),出队时按 ADR-012 → ADR-008
    跳过,`len(fills) == 0`、账本不动。
    """
    fx = FIX["adr012_floor_vs_round"]
    bars = [
        Bar(Date="2020-01-02", Open=1.0, Close=1.0),
        Bar(Date="2020-01-03", Open=fx["open"], Close=fx["open"]),
    ]
    pushed = count_pushes(monkeypatch)
    bt = Backtest(cash=fx["cash"], fee=0.0)
    w = fx["weight"]
    s = make_strategy(lambda st, i: st.target(weight=w) if i == 0 else None)
    fills = drive(bt, s, bars)

    assert len(fills) == fx["expect_push_count"] == 0
    # 意图确实表达过一次(ADR-016:股数在 T+1 才算,故它必须先入队)。
    assert len(pushed) == 1
    assert isinstance(pushed[0], TargetOrder)


def test_order_shares_zero_is_never_enqueued(monkeypatch: pytest.MonkeyPatch):
    """ADR-008 的第一处落实:`order(shares=0)` 的差额此刻就知道是 0 → 不入队。

    与 `TargetOrder` 对照:后者的差额要到 T+1 开盘才知道(ADR-016),所以
    ADR-008 必须分两处落实。本门钉死前一处确实存在 —— 否则 `push_count` 会
    把一个注定不成交的单数进去。
    """
    bars = [Bar(Date="2020-01-02", Open=100.0, Close=100.0), Bar(Date="2020-01-03", Open=100.0, Close=100.0)]
    pushed = count_pushes(monkeypatch)
    bt = Backtest(cash=10000.0, fee=0.0)
    s = make_strategy(lambda st, i: st.order(shares=0) if i == 0 else None)
    fills = drive(bt, s, bars)

    assert pushed == []
    assert fills == []
    assert bt.shares == 0


# ═════════════════════════ ADR-008 三条分解 ═════════════════════════
#
# ┌─ 需人工裁决:`push_count == 1` 对 `target()` 与 ADR-016 结构冲突 ──────┐
# │                                                                        │
# │ §5 T4 的 ADR-008 前两条门要求「同一 `weight` 连续 10 日 →              │
# │ `push_count == 1`」,其中 `push_count` 是对 `Backtest.enqueue` 的打桩   │
# │ 计数(ADR-035:「入队;T4 的 push_count 打在这里」)。                  │
# │                                                                        │
# │ 但 **ADR-016 使这不可实现**:                                           │
# │   「`TargetOrder` 入队时只存**意图**(`weight`),不存股数。到 T+1       │
# │     开盘时,用 `cash + shares * Open_{T+1}` 与 `Open_{T+1}` 计算股数」  │
# │ 入队发生在 T 日收盘,那一刻 `Open_{T+1}` **尚不存在**,故差额未知,     │
# │ 「差额为 0 则不生成订单」(ADR-008)在入队点**无法判断**。             │
# │                                                                        │
# │ 唯一能让 `push_count == 1` 的写法是在入队前用 **T 日 Close** 预筛差额。 │
# │ 本文件实测过那条路(400k 组随机 cash/shares/Close/Open/weight):        │
# │ 「按 Close 预筛为 0、按 T+1 Open 的真实差额非 0」的组数**非零**(两次   │
# │ 独立复现分别得 1573 / 3411 组,量级随抽样区间变,**结论不变**)——      │
# │ 即预筛会**静默吞掉本该成交的订单**。那违反:                            │
# │   * ADR-020「T 日意图在 T+1 开盘**无条件执行**」;                      │
# │   * ADR-016 的全部理由(它存在就是为了不在 T 日锁定任何数值);         │
# │   * §7 第 1 条「功能开关不得绕过任何 ADR 决策」。                      │
# │ 且 `fixtures.adr012_floor_vs_round` 的 `expect_push_count: 0` 只在      │
# │ 「T 日 Close 恰等于 T+1 Open(=1200)」时才可满足 —— fixture 本身没有    │
# │ 钉死 Close,故该门在一般数据上不可满足。                                │
# │                                                                        │
# │ **处置**:ADR-016/020 是冻结决策(§7 第 8 条),门是可改的派生物,故     │
# │ 实现按 ADR 写,门改为断言**门的理由段真正要抓的那件事**(见下)——      │
# │ 这不是放宽,而是把一条自相矛盾的断言换成一条更强、可执行的断言。        │
# │ 该冲突已按 §7 第 5 条上报,等人工裁决是改门还是改 ADR。                 │
# └────────────────────────────────────────────────────────────────────────┘


def test_adr008_same_weight_ten_days_transacts_once(monkeypatch: pytest.MonkeyPatch):
    """同一 `weight` 连续 10 日 → **只成交 1 笔**,且第 2 日起每天都被跳过。

    门的理由段逐字:*(拦住「每天生成订单、成交时因钱不够静默 no-op」的实现
    —— 它成交次数也是 1)*。那种错误实现的可观测特征是**它在第 2 日起仍然
    记账**(靠「钱不够」碰巧 no-op),故本门直接钉死第 2 日起的**账本不动**
    与**出队必然跳过**,这比数 `enqueue` 更强:

      * 断言 `resolve_queue` 被调用 10 次,其中**恰好 1 次**返回 `Fill`、
        9 次返回 `None` —— ADR-008 的「不生成订单」在 ADR-016 下只能在出队点
        观测,这里逐次钉死;
      * 断言第 2 日起 `cash`/`shares` **逐日完全不变**(满仓后现金为 0,
        「钱不够所以 no-op」的实现在这里与正确实现同形,故必须再加下一条);
      * 断言无警告(ADR-008:不报警)。
    """
    bars = [Bar(Date=f"2020-02-{d:02d}", Open=100.0, Close=100.0) for d in range(1, 11)]
    bt = Backtest(cash=10000.0, fee=0.0)

    pushed = count_pushes(monkeypatch)
    resolved: list[Any] = []
    ledger: list[tuple[float, int]] = []
    original = Backtest.resolve_queue

    def spy(self: Backtest, bar: Any) -> Any:
        out = original(self, bar)
        resolved.append(out)
        ledger.append((self.cash, self.shares))
        return out

    monkeypatch.setattr(Backtest, "resolve_queue", spy)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fills = drive(bt, make_strategy(lambda st, i: st.target(weight=1.0)), bars)

    # ADR-008 门的 `push_count` 条款中**在 ADR-016 下仍可满足的那一半**:
    # 每根 K 线的意图都必须**经由 `enqueue` 接缝**(ADR-035),且全程**只有
    # 1 笔成交**。不可满足的是「push_count == 1」—— 它要求在入队点就知道
    # 差额,而 ADR-016 把差额的计算推迟到 T+1 开盘(见本节顶部冲突说明)。
    # 故这里钉死「入队次数 == 天数」与「成交次数 == 1」两端:一个绕过接缝
    # 的实现(push 不到 10)与一个重复成交的实现(fill > 1)都会变红。
    assert len(pushed) == len(bars) == 10, (
        f"每根 K 线的意图都必须经由 enqueue 接缝(ADR-035),实得 {len(pushed)}"
    )
    assert len(resolved) == len(bars) == 10
    assert sum(r is not None for r in resolved) == 1, (
        f"ADR-008:同一 weight 连续 10 日只该成交 1 笔,实得 "
        f"{sum(r is not None for r in resolved)}"
    )
    assert resolved[0] is None          # 第 0 根没有「昨天的意图」
    assert resolved[1] is not None      # 第 1 根成交
    assert all(r is None for r in resolved[2:]), "第 2 日起每天都必须被跳过"
    # 账本从第 1 根成交后起逐日不变 —— 「每天记一次账」的实现在这里暴露。
    assert ledger[1:] == [(0.0, 100)] * 9
    assert len(fills) == 1
    assert caught == []
    assert bt.shares == 100


def test_adr008_weight_half_ten_days(monkeypatch: pytest.MonkeyPatch):
    """`weight=0.5`(**钱有余**)连续 10 日 → 成交 1 次、警告 0、账本逐日不变。

    与上一条的区别:上一条满仓(现金花光),本条留下 5000 现金 —— 「钱不够
    所以静默 no-op」的实现在本条里**没有借口**:它每天都买得起,故它会在
    第 2 日起继续成交,被下面的 `sum(...) == 1` 与账本不变断言抓住。
    """
    bars = [Bar(Date=f"2020-02-{d:02d}", Open=100.0, Close=100.0) for d in range(1, 11)]
    bt = Backtest(cash=10000.0, fee=0.0)

    pushed = count_pushes(monkeypatch)
    resolved: list[Any] = []
    ledger: list[tuple[float, int]] = []
    original = Backtest.resolve_queue

    def spy(self: Backtest, bar: Any) -> Any:
        out = original(self, bar)
        resolved.append(out)
        ledger.append((self.cash, self.shares))
        return out

    monkeypatch.setattr(Backtest, "resolve_queue", spy)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fills = drive(bt, make_strategy(lambda st, i: st.target(weight=0.5)), bars)

    # 同上:`push_count == 1` 在 ADR-016 下不可满足,故钉死可满足的两端 ——
    # 「每根 K 线都经由 enqueue 接缝」与「只成交 1 次」。
    assert len(pushed) == len(bars) == 10, (
        f"每根 K 线的意图都必须经由 enqueue 接缝(ADR-035),实得 {len(pushed)}"
    )
    assert sum(r is not None for r in resolved) == 1
    assert all(r is None for r in resolved[2:])
    assert ledger[1:] == [(5000.0, 50)] * 9, (
        "钱有余时「每天生成订单」的实现会继续记账 —— 账本必须逐日不变"
    )
    assert len(fills) == 1
    assert caught == []
    assert bt.shares == 50
    assert bt.cash == pytest.approx(5000.0)


def test_adr008_enqueue_is_the_only_queue_writer(monkeypatch: pytest.MonkeyPatch):
    """`push_count` 门可执行的**前提**:`enqueue` 是队列的唯一写入口。

    上面两条门因 ADR-016 无法断言 `push_count == 1`(见本节顶部的冲突说明),
    但 ADR-035 把 `enqueue` 钉为 push 接缝这件事仍必须有门守住 —— 否则将来
    任何人内联 `self.queue.append(...)` 都不会变红,而 `contracts.yaml` 的
    `gate_execution.monkeypatch_seams` 就成了一句空话。

    做法:把 `enqueue` 打桩成**只记录、不写队列**,断言队列**始终为空** ——
    若实现有任何旁路写法,队列里就会出现东西。
    """
    bars = [Bar(Date=f"2020-02-{d:02d}", Open=100.0, Close=100.0) for d in range(1, 6)]
    pushed: list[Any] = []

    def blackhole(self: Backtest, order: Any) -> None:
        pushed.append(order)  # 刻意不写 self.queue

    monkeypatch.setattr(Backtest, "enqueue", blackhole)

    bt = Backtest(cash=10000.0, fee=0.0)
    fills = drive(bt, make_strategy(lambda st, i: st.target(weight=0.5)), bars)

    assert len(pushed) == len(bars), "每根 K 线的意图都必须经由 enqueue"
    assert bt.queue == [], (
        "enqueue 被打成黑洞后队列仍非空 —— 说明有旁路直接写 self.queue,"
        "ADR-035 的 push 接缝失效"
    )
    assert fills == [], "没有任何订单进过队列,故不该有成交"


def test_adr008_price_change_produces_second_fill():
    """价格变动使同一 `weight` 的目标股数改变 → **产生第二笔成交**。

    *(拦住反向错误:「weight 未变就永远跳过」的缓存意图 bug —— 它会让仓位
    永久冻结,且能通过原门的每一条)*

    数值全部写死:cash=10000、Open 恒 100 三根后变 50。
      第 1 根成交:floor(1.0*10000/100) = 100 股,现金 0。
      Open 变 50 后可投资产 = 0 + 100*50 = 5000,目标 floor(5000/50) = 100 股
        → 差额 0,**不成交**(满仓时跌价不产生交易,这是正确行为)。
      故要让目标股数改变,必须让**可投资产/价格**比值变 —— 用 weight=0.5:
        Open=100 时 50 股、现金 5000;Open=50 时可投资产 = 5000+50*50 = 7500,
        目标 floor(0.5*7500/50) = 75 股 → 差额 +25,**必须成交**。
    """
    bars = [
        Bar(Date="2020-03-02", Open=100.0, Close=100.0),
        Bar(Date="2020-03-03", Open=100.0, Close=100.0),
        Bar(Date="2020-03-04", Open=100.0, Close=100.0),
        Bar(Date="2020-03-05", Open=50.0, Close=50.0),
        Bar(Date="2020-03-06", Open=50.0, Close=50.0),
    ]
    bt = Backtest(cash=10000.0, fee=0.0)
    fills = drive(bt, make_strategy(lambda st, i: st.target(weight=0.5)), bars)

    assert len(fills) == 2, f"价格变动后必须产生第二笔成交,实得 {fills}"
    assert fills[0].shares == 50
    assert fills[0].price == 100.0
    assert fills[0].side == "BUY"
    assert fills[1].shares == 25
    assert fills[1].price == 50.0
    assert fills[1].side == "BUY"
    assert bt.shares == 75


# ═════════════════════════ ADR-016 ═════════════════════════


def test_adr016_shares_computed_at_next_open():
    """`fixtures.adr016_fill_at_next_open`:按 T+1 的 Open 算,不按 T 日 Close。"""
    fx = FIX["adr016_fill_at_next_open"]
    bars = [
        Bar(Date="2020-01-02", Open=fx["bar_t_close"], Close=fx["bar_t_close"]),
        Bar(Date="2020-01-03", Open=fx["bar_t1_open"], Close=fx["bar_t1_open"]),
    ]
    bt = Backtest(cash=fx["cash"], fee=0.0)
    w = fx["weight"]
    fills = drive(
        bt, make_strategy(lambda st, i: st.target(weight=w) if i == 0 else None), bars
    )

    assert len(fills) == 1
    assert fills[0].shares == fx["expect_shares"]
    assert fills[0].shares != fx["reject_shares"], (
        "按 T 日 Close 锁定股数的错误实现(ADR-016 禁止)"
    )
    assert fills[0].price == fx["bar_t1_open"]


def test_adr016_target_order_is_not_rewritten_by_resolution():
    """ADR-016:解析**不改写** `TargetOrder`,而是产出一个新的 `Fill`。

    §1 的值对象表逐字:「`TargetOrder` 只持 `weight`;T+1 的解析**不改写它**」。
    故出队的那个对象在成交后 `weight` 仍是原值,且它身上**没有** `shares`。
    """
    order = TargetOrder(weight=0.5)
    bt = Backtest(cash=10000.0, fee=0.0)
    bt.enqueue(order)
    fill = bt.resolve_queue(Bar(Date="2020-01-02", Open=100.0))

    assert isinstance(fill, Fill)
    assert order.weight == 0.5
    assert not hasattr(order, "shares")
    with pytest.raises(Exception):
        order.weight = 0.9  # frozen 值对象


# ═════════════════════════ ADR-014 ═════════════════════════


def test_adr014_denominator_is_investable_three_way():
    """`fixtures.adr014_denominator_three_way` —— 三向可分,全部数值写死。

    | 实现 | 第二次算法 | 终局持仓 |
    |---|---|---|
    | **正确**:分母 = 可投资产 | `floor(0.8×10000/100) − 50 = +30` | **80 股** |
    | 错误 ①:分母 = 剩余现金,算目标再减持仓 | `floor(0.8×5000/100) − 50 = −10` | **40 股** |
    | 错误 ②:分母 = 剩余现金,结果当作差额 | `floor(0.8×5000/100) = +40` | **90 股** |
    """
    fx = FIX["adr014_denominator_three_way"]
    bars = [
        Bar(Date="2020-01-02", Open=1.0, Close=1.0),
        Bar(Date="2020-01-03", Open=fx["open_1"], Close=fx["open_1"]),
        Bar(Date="2020-01-06", Open=fx["open_2"], Close=fx["open_2"]),
    ]
    bt = Backtest(cash=fx["cash"], fee=fx["fee_rate"])
    w1, w2 = fx["weight_1"], fx["weight_2"]

    def script(st: Strategy, i: int) -> None:
        if i == 0:
            st.target(weight=w1)
        elif i == 1:
            # 此刻 bar1 已成交,持仓应为 after_leg1_shares。
            assert st.shares == fx["after_leg1_shares"]
            assert st.cash == pytest.approx(fx["after_leg1_cash"])
            st.target(weight=w2)

    fills = drive(bt, make_strategy(script), bars)

    assert len(fills) == 2
    assert fills[0].shares == fx["after_leg1_shares"]
    # §5 T4 的 ADR-014 条款明文要求钉死第二笔的**方向**。正确实现的第二腿是
    # `floor(0.8*10000/100) - 50 = +30` → **BUY**;而错误 ①(分母 = 剩余现金)
    # 算出 `floor(0.8*5000/100) - 50 = -10` → **SELL**。故这一行把「方向」
    # 单独钉死,使错误 ① 在**第二笔的 side 上**就暴露,而不必等到终局持仓 ——
    # 差额的**符号**本身就是三向判别的一部分。
    assert fills[1].side == "BUY", (
        "ADR-014:第二腿必须是**买入** 30 股(分母 = 可投资产 10000)。"
        "若为 SELL,说明分母用了剩余现金 5000(错误 ①,差额 -10)"
    )
    assert fills[1].shares == (
        fx["expect_final_shares"] - fx["after_leg1_shares"]
    ), "第二腿股数 = 终局持仓 - 第一腿持仓(全部取自 fixture,不复用被测公式)"
    assert bt.shares == fx["expect_final_shares"]
    assert bt.shares != fx["reject_target_from_cash"]
    assert bt.shares != fx["reject_spend_cash"]


def test_adr014_investable_is_not_equity():
    """ADR-014:「可投资产」用 **Open**,`equity` 用 **Close**,两者不同。

    ADR-014 明文:「**它不是 `equity`**。… I3 不适用于成交时刻。」
    构造 Open != Close 使两条口径给出**不同**股数,断言取的是 Open 那条。

    账本状态由**正常成交**摆出来(不碰任何私有字段):初始 7500 现金,
    先 `ShareOrder(shares=50)` 在 Open=50 成交 → 花 2500,余
    **现金 5000 / 持仓 50 股**。

    成交日 Open=100、**前一日 Close=200**:
      可投资产(正确,ADR-014)= 5000 + 50*100 = 10000
                              → floor(1.0*10000/100) = 100 股 → 差额 **+50**
      equity 口径(错误,按 Close=200)= 5000 + 50*200 = 15000
                              → floor(15000/100) = 150 股 → 差额 **+100**
                              (且现金不够,会炸 I1 —— 错误实现连红法都不同)
    两者判然可分。
    """
    bt = Backtest(cash=7500.0, fee=0.0)
    bt.enqueue(ShareOrder(shares=50))
    bt.resolve_queue(Bar(Date="2020-01-02", Open=50.0, Close=200.0))
    assert (bt.cash, bt.shares) == (5000.0, 50)

    assert bt.investable_at(100.0) == pytest.approx(10000.0)
    bt.enqueue(TargetOrder(weight=1.0))
    fill = bt.resolve_queue(Bar(Date="2020-01-03", Open=100.0, Close=200.0))

    assert fill is not None
    assert fill.shares == 50, "差额必须按 Open 基准的可投资产算(ADR-014)"
    assert fill.shares != 100, "按上一日 Close(equity 口径)算的错误实现"
    assert bt.shares == 100
    assert bt.cash == pytest.approx(0.0)


# ═════════════════════════ ADR-020 ═════════════════════════


def test_adr020_gap_halving_still_executes_unconditionally():
    """ADR-020:构造 T+1 跳空腰斩 → 订单仍**无条件执行**(不跳过、不截断)。

    T 日 Close=100,T+1 Open=50(腰斩 -50%)。`target(1.0)` 必须仍然满仓:
    可投资产 = 10000 + 0 = 10000,floor(10000/50) = 200 股,**全部成交**。
    「跳空腰斩时引擎仍会忠实满仓」是 ADR-020 逐字承认的局限,不是 bug。
    """
    bars = [
        Bar(Date="2020-03-16", Open=100.0, Close=100.0),
        Bar(Date="2020-03-17", Open=50.0, Close=50.0),
    ]
    bt = Backtest(cash=10000.0, fee=0.0)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fills = drive(
            bt, make_strategy(lambda st, i: st.target(weight=1.0) if i == 0 else None), bars
        )

    assert len(fills) == 1, "跳空不得使订单被跳过(ADR-020)"
    assert fills[0].shares == 200, "不得截断(ADR-020:无条件执行)"
    assert fills[0].price == 50.0
    assert caught == []


def test_adr020_gap_up_order_shares_executes_or_fails_loudly():
    """跳空**上涨**时 `order(shares=n)` 的锁定股数:要么成交,要么响亮失败。

    ADR-016 的「理由」逐字:「若在 T 日锁定股数,T+1 跳空上涨会使成交额超过
    总资产 → 现金变负。本方案下永不超买。」—— 那个保证只覆盖 `TargetOrder`。
    `order(shares=n)` **本来就**是锁定股数(ADR-014),故它在跳空上涨下可能
    买不起;此时 I1 必须**响亮失败**(ADR-010),不得静默截断。
    """
    bt = Backtest(cash=10000.0, fee=0.0)
    bt.enqueue(ShareOrder(shares=100))
    with pytest.raises(ValueError, match="现金不足"):
        bt.resolve_queue(Bar(Date="2020-01-03", Open=500.0))


# ═════════════════════════ ADR-041 ═════════════════════════


def test_adr041_last_call_wins_two_targets():
    """同一 `next()` 内 `target(0.3)` 再 `target(0.8)` → 只成交一笔、按 `0.8`。

    数值写死:cash=10000、Open=100 → floor(0.8*10000/100) = 80 股
    (按 0.3 会是 30 股 —— 两者判然可分)。
    """
    bars = [
        Bar(Date="2020-01-02", Open=100.0, Close=100.0),
        Bar(Date="2020-01-03", Open=100.0, Close=100.0),
    ]

    def script(st: Strategy, i: int) -> None:
        if i == 0:
            st.target(weight=0.3)
            st.target(weight=0.8)

    bt = Backtest(cash=10000.0, fee=0.0)
    fills = drive(bt, make_strategy(script), bars)

    assert len(fills) == 1
    assert fills[0].shares == 80
    assert fills[0].shares != 30


def test_adr041_last_call_wins_target_then_order():
    """`target(0.5)` 再 `order(100)` → 按 `order(100)`(混用同理,ADR-041)。

    按 `target(0.5)` 会是 50 股;按 `order(100)` 是 100 股。
    """
    bars = [
        Bar(Date="2020-01-02", Open=100.0, Close=100.0),
        Bar(Date="2020-01-03", Open=100.0, Close=100.0),
    ]

    def script(st: Strategy, i: int) -> None:
        if i == 0:
            st.target(weight=0.5)
            st.order(shares=100)

    bt = Backtest(cash=10000.0, fee=0.0)
    fills = drive(bt, make_strategy(script), bars)

    assert len(fills) == 1
    assert fills[0].shares == 100
    assert fills[0].shares != 50


def test_adr041_last_call_zero_delta_discards_everything(
    monkeypatch: pytest.MonkeyPatch,
):
    """**末次差额为 0 的 case(关键)** —— `fixtures.adr041_last_call_zero_delta`。

    *(末次胜出后 ADR-008 生效,先前的 `order(100)` 必须被**丢弃**而非回退执行。
    缺此 case 会放过一种实现:「每次调用都入队,T+1 执行最后一个**非零差额**
    的」—— 它能通过前两个 case、通过 I4、通过所有 ADR-008 条款,却在此 case
    错误地成交 100 股。这也是 ADR-041 与 I4 唯一会对「哪个订单存活」产生分歧
    的地方)*
    """
    fx = FIX["adr041_last_call_zero_delta"]
    px = fx["open"]
    bars = [
        Bar(Date="2020-01-02", Open=px, Close=px),
        Bar(Date="2020-01-03", Open=px, Close=px),
        Bar(Date="2020-01-06", Open=px, Close=px),
    ]
    assert fx["sequence"] == ["order(100)", "target(0.5)"]

    def script(st: Strategy, i: int) -> None:
        if i == 0:
            st.target(weight=0.5)
        elif i == 1:
            # 当前 weight 恰为 current_weight,故 target(0.5) 的差额为 0。
            assert st.shares == fx["after_target_half_shares"]
            assert st.cash == pytest.approx(fx["after_target_half_cash"])
            st.order(shares=100)
            st.target(weight=fx["current_weight"])

    bt = Backtest(cash=fx["cash"], fee=0.0)
    pushed = count_pushes(monkeypatch)
    #: 每根 K 线「出队前」的队列长度。此前本列表被收集却从未断言 —— 一个
    #: 死局部变量看起来像覆盖,实际什么也没钉死。
    queue_len_before_resolve: list[int] = []

    def sample(phase: str, queue: list[Any]) -> None:
        if phase == "before_resolve":
            queue_len_before_resolve.append(len(queue))

    fills = drive(bt, make_strategy(script), bars, on_sample=sample)

    # 出队前的队列长度逐根钉死:第 0 根空(还没人下单);第 1 根与第 2 根
    # 各有**恰好一个**昨天入队的订单(I4 的上界 + 「队列真的存在」的下界)。
    # 第 2 根那一个正是末次胜出的零差额 TargetOrder —— 它必须被出队并丢弃,
    # 不得留在队列里(那会是 GTC,OQ-06 ⑤ 留给 v1)。
    assert queue_len_before_resolve == [0, 1, 1]
    assert bt.queue == [], "循环结束后队列必须是空的(ADR-005 的 discard_queue)"

    # 第 0 根入队 1 次(target 0.5);第 1 根的两次调用在策略侧收敛为 1 个意图,
    # 故 enqueue 最多再被调 1 次 —— 而那一次入的是末次的 TargetOrder。
    target_pushes = [p for p in pushed if isinstance(p, TargetOrder)]
    share_pushes = [p for p in pushed if isinstance(p, ShareOrder)]
    assert share_pushes == [], (
        "ADR-041:order(100) 被 target(0.5) 覆盖后必须被丢弃,不得入队"
    )
    assert len(target_pushes) == 2

    # 关键断言:第 2 根只能有 0 笔新成交(ADR-008 的零差额)。
    assert len(fills) == 1, f"末次差额为 0 → 不得新增成交,实得 {fills}"
    assert fills[0].shares == fx["after_target_half_shares"]
    assert bt.shares == fx["after_target_half_shares"]
    assert bt.cash == pytest.approx(fx["after_target_half_cash"])
    assert bt.investable_at(px) == pytest.approx(fx["investable"])


def test_adr041_zero_delta_day_adds_no_push_and_no_fill(
    monkeypatch: pytest.MonkeyPatch,
):
    """`expect_push_count == 0` / `expect_fills_added == 0` 的直读形式。

    上一条从完整时间线出发(入队次数含第 0 根的建仓单)。本条把 fixture 的
    `expect_push_count` 逐字用上:**单独**跑「已持仓 50 股 / 现金 5000」那一天,
    断言该天**新增**的入队与成交都是 0。
    """
    fx = FIX["adr041_last_call_zero_delta"]
    px = fx["open"]
    bt = Backtest(cash=fx["cash"], fee=0.0)
    bt.enqueue(TargetOrder(weight=fx["current_weight"]))
    bt.resolve_queue(Bar(Date="2020-01-03", Open=px))
    assert bt.shares == fx["after_target_half_shares"]
    assert bt.cash == pytest.approx(fx["after_target_half_cash"])

    # 从这里起计数 —— 只看「那一天」。
    pushed = count_pushes(monkeypatch)

    def script(st: Strategy, i: int) -> None:
        st.order(shares=100)
        st.target(weight=fx["current_weight"])

    s = make_strategy(script)
    s._bind_data(_Slice([Bar(Date="2020-01-06", Open=px, Close=px)]))
    s._bind_account(cash=bt.cash, shares=bt.shares, equity=bt.account.equity_at(px))
    s._enter_next(bar_index=1, date="2020-01-06")
    s.next()
    s._exit_next()
    bt.enqueue_intent(s.pending_intent)

    fill = bt.resolve_queue(Bar(Date="2020-01-07", Open=px))

    assert [type(p).__name__ for p in pushed] == ["TargetOrder"]
    assert fill is None, f"expect_fills_added == 0,实得 {fill}"
    assert bt.shares == fx["after_target_half_shares"]
    assert bt.cash == pytest.approx(fx["after_target_half_cash"])

    # ── `expect_push_count == 0` 的可满足部分 + 同一处人工裁决 ────────────
    #
    # fixture 的 `expect_push_count: 0` 对 **ShareOrder** 可逐字满足,且这正是
    # 门的理由段要抓的那件事:「先前的 `order(100)` 必须被**丢弃**而非回退
    # 执行」。下面这条断言就是它。
    assert len([p for p in pushed if isinstance(p, ShareOrder)]) == fx["expect_push_count"]

    # 但末次胜出的那个 **TargetOrder** 仍然入队 1 次,故**总**入队数是 1 而非 0。
    # 根因与本文件「ADR-008 三条分解」节顶部那个待裁决冲突**完全同一**:
    # ADR-016 规定 `TargetOrder` 入队时只存 `weight`、不存股数,而「差额为 0」
    # 要到 T+1 开盘用那天的 `Open` 才知道(此处 `current_weight = 0.5` 的差额
    # 为 0 恰恰是**出队时**算出来的)。要让总入队数为 0,只能在 T 日收盘用
    # Close 预筛 —— 那条路已实测会静默吞掉本该成交的订单(见该节),违反
    # ADR-016/020 与 §7 第 1 条。
    #
    # 故此处断言「零差额**不产生成交**、且被覆盖的 ShareOrder **未入队**」
    # —— 两者都是门的理由段指名要抓的,且都对错误实现可证伪(「执行最后一个
    # 非零差额订单」的实现会在此成交 100 股而变红)。「总 push_count == 0」
    # 的字面形式与 ADR-016 结构冲突,已按 §7 第 5 条上报待裁决。
    total_pushes_in_that_day = len(pushed)
    assert total_pushes_in_that_day == 1, (
        "ADR-041 的末次 TargetOrder 必须入队恰 1 次(其零差额只能在出队时发现);"
        f"实得 {total_pushes_in_that_day}"
    )


# ═════════════════════════ I4 + 队列同一性 ═════════════════════════


def test_i4_queue_length_sampled_at_both_points_and_identity_preserved():
    """**I4 带采样点,且必须证明队列真的存在**。

    在主循环每根 K 线的**入队后**与**出队前**各断言一次 `len(queue) <= 1`,
    断言断言执行次数 `== 2 * 交易日数`;**并在至少一个采样点断言
    `len(queue) == 1`**;**再断言 T 日入队的订单对象与 T+1 出队的是同一个**
    (`is` 比较)。

    *(「恒 `<= 1`」被 `len(queue) == 0` 恒成立满足 —— 一个完全绕过队列、把
    差额存成标量字段次日直接应用的实现能通过它、通过 I7、通过所有 ADR-008
    条款,而 ADR-006 说队列是「信号与成交分离的物理载体」,那个实现里它根本
    不存在。`is` 比较则钉死「T 日入队、T+1 出队」而非每日重算)*
    """
    bars = [Bar(Date=f"2020-04-{d:02d}", Open=100.0 + d, Close=100.0 + d) for d in range(1, 6)]

    assertions = 0
    saw_length_one = False
    sampled_before_resolve: list[list[Any]] = []

    def sample(phase: str, queue: list[Any]) -> None:
        nonlocal assertions, saw_length_one
        assert len(queue) <= 1, f"I4 在 {phase} 被违反:len={len(queue)}"
        assertions += 1
        if len(queue) == 1:
            saw_length_one = True
        if phase == "before_resolve":
            sampled_before_resolve.append(list(queue))

    bt = Backtest(cash=100000.0, fee=0.0)
    # 每根都下单 -> 每根 K 线的「入队后」采样点必然 len == 1。
    fills = drive(bt, make_strategy(lambda st, i: st.order(shares=1)), bars, on_sample=sample)

    assert assertions == 2 * len(bars), (
        f"采样点必须是每根 K 线两次(入队后 + 出队前):"
        f"实得 {assertions} != {2 * len(bars)}"
    )
    assert saw_length_one, "队列必须真的装过订单(ADR-006:信号与成交分离的物理载体)"
    assert len(fills) == len(bars) - 1  # 最后一根的单按 ADR-005 丢弃

    # `sampled_before_resolve` 此前被收集却从未断言(死局部变量)。钉死它:
    # 第 0 根出队前队列为空,其后每根**恰好**装着昨天那一个 ShareOrder。
    # 这把「至少一个采样点 len == 1」从存在命题加强为**逐根**命题 —— 一个
    # 只在首根入队、其后绕过队列的实现会在此变红。
    assert [len(q) for q in sampled_before_resolve] == [0] + [1] * (len(bars) - 1)
    for q in sampled_before_resolve[1:]:
        assert isinstance(q[0], ShareOrder) and q[0].shares == 1


def test_enqueued_order_object_is_the_same_object_dequeued_next_bar(
    monkeypatch: pytest.MonkeyPatch,
):
    """T 日入队的订单对象与 T+1 出队的是**同一个**(`is` 比较)。

    ADR-002 逐字:「故 T4 的门断言 T 日入队的订单对象与 T+1 出队的是同一个
    对象(`is` 比较),用以抓出『每日重算』的实现:它结果可能对,但纪律已经
    没了。」

    `TargetOrder` / `ShareOrder` 刻意用 `eq=False`(保留身份相等),故本门
    无法被误写成等价的 `==` 弱断言 —— 两个不同的 `ShareOrder(shares=1)` 在
    `==` 下也不相等。

    **本条单独不足以抓住它的目标 bug —— 配套条款见
    `test_resolve_queue_constructs_no_order_objects`。** 实测发现的洞:本条在
    委托前采样 `self.queue[0]`,故它只证明「**躺在队列里**的那个对象是昨天
    入队的」;它**没有**把那个对象与真正产出的 `Fill` 绑在一起。一个先正确
    `pop()`、再 `dataclasses.replace(_q)` 并按**新副本**算股数的实现
    ——「每日重算」的教科书写法 —— 在本条下采样到的仍是昨天那个对象,输出
    数值也完全相同(`Fill(shares=100, price=100.0)`、`cash=0.0`),42 条 T4
    门**全部通过**。那正是 ADR-002(DESIGN.md:150)写这条门要拦的实现。
    故真正的钉死落在配套条款上:断言 `resolve_queue` 执行期间构造出的
    `Order` 对象数**为 0**(正确实现 0,上述变异体 1)。
    """
    bars = [Bar(Date=f"2020-05-{d:02d}", Open=100.0, Close=100.0) for d in range(1, 5)]

    pushed = count_pushes(monkeypatch)
    dequeued: list[Any] = []
    original_resolve = Backtest.resolve_queue

    def spy(self: Backtest, bar: Any) -> Any:
        # 出队**前**采样 —— 这正是 ADR-035 要求 resolve_queue 是命名方法的理由。
        dequeued.append(self.queue[0] if self.queue else None)
        return original_resolve(self, bar)

    monkeypatch.setattr(Backtest, "resolve_queue", spy)

    bt = Backtest(cash=100000.0, fee=0.0)
    drive(bt, make_strategy(lambda st, i: st.order(shares=1)), bars)

    assert len(pushed) == len(bars)
    assert len(dequeued) == len(bars)
    assert dequeued[0] is None  # 第 0 根没有「昨天的意图」
    for day in range(1, len(bars)):
        assert dequeued[day] is pushed[day - 1], (
            f"第 {day} 根出队的对象必须**就是**第 {day - 1} 根入队的那个"
            f"(ADR-002;`is` 比较抓『每日重算』)"
        )
    # 身份相等:`==` 也无法被两个同值对象混过。
    assert ShareOrder(shares=1) != ShareOrder(shares=1)


def test_resolve_queue_constructs_no_order_objects(monkeypatch: pytest.MonkeyPatch):
    """ADR-002「每日重算」的**有效**钉死:`resolve_queue` 内**不得**构造 `Order`。

    DESIGN.md:150 逐字:`is` 比较存在的目的是「抓出『每日重算』的实现:它结果
    可能对,但纪律已经没了」。而「结果可能对」正是上一条断言失效的原因 ——
    「每日重算」的输出与正确实现**数值全同**,靠比对数值或比对「队列里躺着
    谁」都抓不到它。

    唯一的结构性差别:正确实现在成交时刻只**读**昨天那个订单(ADR-016:
    「解析**不改写** `TargetOrder`,而是产出一个新的 `Fill`」),故它在
    `resolve_queue` 里**构造 0 个** `Order`;而「每日重算」必须在那一刻把
    订单重建或复制一份(`dataclasses.replace` / 重新 `TargetOrder(...)`),
    计数 >= 1。本条就断言那个计数。

    判别力实测(变异体 `order = dataclasses.replace(self.queue.pop())`):
      * 正确实现 → 构造 0 个 → 通过
      * 变异体   → 构造 1 个 → 失败(而它能通过其余 42 条门的全部)

    两种订单各跑一次:`TargetOrder` 的股数到 T+1 才算(ADR-016),是最容易
    被写成「重算」的那种;`ShareOrder` 一并钉死,防止只对一条路径守纪律。
    """
    bars = [Bar(Date=f"2020-06-{d:02d}", Open=100.0, Close=100.0) for d in range(1, 5)]

    # ── TargetOrder:ADR-016 下股数在成交时刻才算,最易被实现成「重算」 ──
    constructed = count_order_constructions_during_resolve(monkeypatch)
    bt = Backtest(cash=10000.0, fee=0.0)
    fills = drive(bt, make_strategy(lambda st, i: st.target(weight=1.0)), bars)

    # 先确认本测真的跑到了成交路径 —— 否则「0 个构造」会因空转而假绿。
    assert len(fills) == 1, "ADR-008:同一 weight 只成交 1 笔(本测需要它真的成交)"
    assert bt.shares == 100
    assert constructed == [], (
        f"ADR-002:`resolve_queue` 必须只**读**昨天出队的订单(ADR-016:"
        f"「解析不改写它」),不得重建/复制 —— 那是『每日重算』。"
        f"实测在成交期间构造了 {len(constructed)} 个 Order:{constructed}"
    )


def test_resolve_queue_constructs_no_order_objects_share_order(
    monkeypatch: pytest.MonkeyPatch,
):
    """同上,走 `ShareOrder` 路径 —— 两条路径都不得在成交时刻重建订单。"""
    bars = [Bar(Date=f"2020-06-{d:02d}", Open=100.0, Close=100.0) for d in range(1, 5)]

    constructed = count_order_constructions_during_resolve(monkeypatch)
    bt = Backtest(cash=100000.0, fee=0.0)
    fills = drive(bt, make_strategy(lambda st, i: st.order(shares=1)), bars)

    assert len(fills) == len(bars) - 1, "最后一根的单按 ADR-005 丢弃"
    assert bt.shares == len(bars) - 1
    assert constructed == [], (
        f"ADR-002:`ShareOrder` 路径同样不得在 `resolve_queue` 里重建订单。"
        f"实测构造了 {len(constructed)} 个:{constructed}"
    )


def test_enqueue_overwrites_rather_than_appends():
    """ADR-041 在 `enqueue` 这一层:**覆盖**而非 append,故 I4 在构造上成立。

    连续入队三个订单,队列仍只有 1 个,且是**最后**那个。
    """
    bt = Backtest(cash=10000.0, fee=0.0)
    a, b, c = TargetOrder(weight=0.1), ShareOrder(shares=7), TargetOrder(weight=0.9)
    bt.enqueue(a)
    bt.enqueue(b)
    bt.enqueue(c)
    assert len(bt.queue) == 1
    assert bt.queue[0] is c


def test_resolve_queue_is_a_named_method_on_the_class():
    """ADR-035 / `gate_execution.monkeypatch_seams`:两个接缝必须可打桩。

    *(「必须是命名方法 —— 内联 `self.queue.pop()` 会让那条门无法执行」)*
    断言它们是**类**上的函数,而不是 `__init__` 里绑的实例闭包 —— 后者
    `monkeypatch.setattr(Backtest, ...)` 打不到。
    """
    seams = CONTRACTS["gate_execution"]["monkeypatch_seams"]
    assert "Backtest.enqueue" in seams
    assert "Backtest.resolve_queue" in seams
    assert callable(Backtest.__dict__["enqueue"])
    assert callable(Backtest.__dict__["resolve_queue"])

    bt = Backtest(cash=1.0, fee=0.0)
    # 实例上不得有同名属性遮蔽类方法(那会让类级打桩静默失效)。
    assert "enqueue" not in vars(bt)
    assert "resolve_queue" not in vars(bt)
    assert isinstance(bt.queue, list)


# ═════════════════════════ ADR-005 ═════════════════════════


def test_adr005_order_on_last_bar_is_silently_discarded():
    """ADR-005:最后一根 K 线下单 → 断言被丢弃且**无异常**(且无 warning)。"""
    bars = [
        Bar(Date="2020-01-02", Open=100.0, Close=100.0),
        Bar(Date="2020-01-03", Open=100.0, Close=100.0),
    ]
    bt = Backtest(cash=10000.0, fee=0.0)
    last = len(bars) - 1

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fills = drive(
            bt,
            make_strategy(lambda st, i: st.order(shares=10) if i == last else None),
            bars,
        )

    assert fills == []
    assert bt.shares == 0
    assert bt.cash == pytest.approx(10000.0)
    assert caught == [], "ADR-005 的「且无 warning」是缺失行为的断言"
    assert bt.queue == [], "循环结束后队列必须为空(丢弃作用于队列,§1)"


def test_single_bar_dataset_produces_no_trade():
    """ADR-034 的推论:仅 1 个交易日时「无 T+1 故任何订单被丢弃(ADR-005)」。"""
    bt = Backtest(cash=10000.0, fee=0.0)
    fills = drive(
        bt,
        make_strategy(lambda st, i: st.target(weight=1.0)),
        [Bar(Date="2020-01-02", Open=100.0, Close=100.0)],
    )
    assert fills == []
    assert bt.cash == pytest.approx(10000.0)


# ═════════════════════════ I2 经 order() ═════════════════════════


def test_i2_oversell_via_order_raises():
    """**I2 经 `order()`**:`order(shares=-999)` 在持仓 100 股时 → 报错。

    (ADR-009 / ADR-014:`n < 0` 使 `shares` 变负 → 报错,守护 I2)
    """
    bars = [
        Bar(Date="2020-01-02", Open=100.0, Close=100.0),
        Bar(Date="2020-01-03", Open=100.0, Close=100.0),
        Bar(Date="2020-01-06", Open=100.0, Close=100.0),
    ]

    def script(st: Strategy, i: int) -> None:
        if i == 0:
            st.order(shares=100)
        elif i == 1:
            assert st.shares == 100
            st.order(shares=-999)

    bt = Backtest(cash=100000.0, fee=0.0)
    with pytest.raises(ValueError, match="超过持仓"):
        drive(bt, make_strategy(script), bars)


def test_i2_sell_with_no_position_raises():
    """ADR-009:无持仓卖出 → 报错退出。"""
    bt = Backtest(cash=10000.0, fee=0.0)
    bt.enqueue(ShareOrder(shares=-1))
    with pytest.raises(ValueError, match="超过持仓"):
        bt.resolve_queue(Bar(Date="2020-01-02", Open=100.0))


def test_target_zero_sells_everything():
    """`target(weight=0.0)` → 清仓(ADR-015 的闭区间下界,`SELL` 方向)。

    数值:100 股、Open=100 → 目标 0 股、差额 -100 → SELL 100 股、现金回到 10000。
    """
    bars = [
        Bar(Date="2020-01-02", Open=100.0, Close=100.0),
        Bar(Date="2020-01-03", Open=100.0, Close=100.0),
        Bar(Date="2020-01-06", Open=100.0, Close=100.0),
    ]

    def script(st: Strategy, i: int) -> None:
        if i == 0:
            st.target(weight=1.0)
        elif i == 1:
            st.target(weight=0.0)

    bt = Backtest(cash=10000.0, fee=0.0)
    fills = drive(bt, make_strategy(script), bars)

    assert [f.side for f in fills] == ["BUY", "SELL"]
    assert [f.shares for f in fills] == [100, 100]
    assert bt.shares == 0
    assert bt.cash == pytest.approx(10000.0)


# ═════════════════════════ 手续费(ADR-007/043) ═════════════════════════


def test_fee_is_charged_both_directions_with_shadow_arithmetic():
    """ADR-043:买卖**双向均收费**,费率由本层(T4)乘出。

    影子算术全部写在本文件内,**不读 `fill.fee`** 来算期望值(T3 门的同一
    隔离要求):`rate` 字面写死 `0.001`。
    """
    rate = 0.001
    bars = [
        Bar(Date="2020-01-02", Open=100.0, Close=100.0),
        Bar(Date="2020-01-03", Open=100.0, Close=100.0),
        Bar(Date="2020-01-06", Open=200.0, Close=200.0),
    ]

    def script(st: Strategy, i: int) -> None:
        if i == 0:
            st.order(shares=50)
        elif i == 1:
            st.order(shares=-50)

    bt = Backtest(cash=10000.0, fee=rate)
    fills = drive(bt, make_strategy(script), bars)

    assert [f.side for f in fills] == ["BUY", "SELL"]
    # 影子侧自算,不读 fill.fee。
    buy_fee = 50 * 100.0 * rate       # 5.0
    sell_fee = 50 * 200.0 * rate      # 10.0
    assert fills[0].fee == pytest.approx(buy_fee)
    assert fills[1].fee == pytest.approx(sell_fee)
    # ADR-043:买入 cash -= gross + fee;卖出 cash += gross - fee。
    shadow = 10000.0 - (50 * 100.0 + buy_fee) + (50 * 200.0 - sell_fee)
    assert bt.cash == pytest.approx(shadow)
    assert bt.shares == 0


def test_fee_default_is_zero_and_negative_rate_is_rejected():
    """ADR-007 默认 `rate = 0.0`;ADR-039 的 `FeeRate` 要求 `rate >= 0`。"""
    assert Backtest(cash=1.0).fee_rate == 0.0
    with pytest.raises(ValueError, match="不得为负"):
        Backtest(cash=1.0, fee=-0.001)
    with pytest.raises(ValueError, match="有限数"):
        Backtest(cash=1.0, fee=float("nan"))


# ┌─ `target()` × 非零费率 —— 本文件此前的盲点 ────────────────────────────┐
# │                                                                        │
# │ T4 的全部钉死 fixture(`adr016_*` / `adr014_*` / `adr012_*` /          │
# │ `adr041_*`)的费率都是 **0**(`adr014_denominator_three_way` 的        │
# │ `fee_rate: 0.0` 是唯一写出来的那个),而唯一的非零费率门               │
# │ (`test_fee_is_charged_both_directions_with_shadow_arithmetic`)        │
# │ 只用 `order(shares=)` —— 它**绕过**目标股数公式。于是「定量时漏掉     │
# │ 手续费」在全门之下不可观测。                                           │
# │                                                                        │
# │ 这正是 §5 T3 门已经点名的那条纪律:「**必须用非零费率                  │
# │ `rate = 0.001`** —— `rate=0` 时手续费方向错误、漏算、双算全部不可      │
# │ 观测」。目标股数公式归 T4,故同一纪律在此适用。                        │
# │                                                                        │
# │ 下面两条门的期望值全部**手工算在本文件内**(T3/T4 的同一隔离要求):   │
# │ `rate` 字面写死 `0.001`,不读 `fill.fee`、不调被测代码算期望值。        │
# └────────────────────────────────────────────────────────────────────────┘


def test_target_full_position_under_nonzero_fee_stays_affordable():
    """ADR-010/016 的「永不超买」在 `weight=1.0` + `rate>0` 下必须成立。

    `weight=1.0` 在 ADR-015 的闭区间内(T2 的门断言它**不报错**),
    `--fee R` 是 ADR-017/039 的合法参数 —— 两者都合法,故其组合**不得**
    中止回测。ADR-016 的理由段逐字写明「I1 是不变式,**不该靠运气成立**
    ... 本方案下**永不超买**」,而 `account.py` 的注释正依赖该保证
    (「真实的负现金只会是浮点残差」)。

    手工算术(`rate` 字面写死):买一股的真实代价是
    `Open * (1 + rate) = 100 * 1.001 = 100.1`,故
    `floor(1.0 * 10000 / 100.1) == 99` 股,
    `gross = 9900`、`fee = 9.9`、`cash_after = 90.1`。

    **显式排除按裸 `Open` 定量的实现**:它给 `floor(10000/100) == 100` 股,
    成交额 `10000` 加手续费 `10` 共 `10010 > 10000` → `Account.apply` 抛
    I1 而整轮中止。故本门对 `reject_shares == 100` 做显式不等断言。
    """
    rate = 0.001
    bars = [
        Bar(Date="2020-01-02", Open=100.0, Close=100.0),
        Bar(Date="2020-01-03", Open=100.0, Close=100.0),
    ]

    def script(st: Strategy, i: int) -> None:
        if i == 0:
            st.target(weight=1.0)

    bt = Backtest(cash=10000.0, fee=rate)
    # 不得抛错 —— 这本身就是本门的第一条断言(ADR-020 的「无条件执行」)。
    fills = drive(bt, make_strategy(script), bars)

    assert len(fills) == 1
    assert fills[0].side == "BUY"
    # floor(10000 / (100 * 1.001)) = floor(99.900...) = 99
    assert fills[0].shares == 99
    # 按裸 Open 定量的实现:floor(10000/100) = 100 —— 买不起。
    assert fills[0].shares != 100
    assert fills[0].price == 100.0
    assert fills[0].fee == pytest.approx(99 * 100.0 * rate)   # 9.9

    # I1:成交后现金非负,且是手工算出的那个数。
    assert bt.shares == 99
    assert bt.cash == pytest.approx(10000.0 - 9900.0 - 9.9)   # 90.1
    assert bt.cash >= 0.0
    # 未被 clamp:剩余现金不够再买一股(含费)。
    assert bt.cash < 100.0 * (1.0 + rate)


def test_target_rebalance_up_under_nonzero_fee_stays_affordable():
    """0.5 → 1.0 的加仓在非零费率下也必须买得起(同上,但**已持仓**)。

    从空仓起只检验分母里的 `cash`;已持仓时分母是 ADR-014 的
    `cash + shares * Open`,故再测一次「加仓到满仓」才覆盖那条路径。

    手工算术(`rate = 0.001` 字面写死,`cash = 20000`、`Open = 10` 恒定):

      * 第 1 腿 `target(0.5)`:`floor(0.5 * 20000 / (10 * 1.001))`
        `= floor(999.000...) = 999` 股;`gross = 9990`、`fee = 9.99`,
        故 `cash_1 = 20000 - 9990 - 9.99 = 10000.01`。
      * 第 2 腿 `target(1.0)`:可投资产 `= 10000.01 + 999 * 10 = 19990.01`
        (ADR-014,用成交日的 `Open`);
        `floor(19990.01 / 10.01) = floor(1997.00...) = 1997` 股,
        差额 `= 1997 - 999 = +998`;`gross = 9980`、`fee = 9.98`,
        故 `cash_2 = 10000.01 - 9980 - 9.98 = 10.03`。

    **这组数把按裸 `Open` 定量的实现逼到中止**:它给
    `floor(19990.01 / 10) = 1999` 股、差额 `+1000`、代价
    `1000 * 10 * 1.001 = 10010 > 10000.01` → I1 抛错。故本门既断言终局
    持仓 `== 1997`、显式 `!= 1999`,也断言整轮**不抛错**。
    """
    rate = 0.001
    bars = [
        Bar(Date="2020-01-02", Open=10.0, Close=10.0),
        Bar(Date="2020-01-03", Open=10.0, Close=10.0),
        Bar(Date="2020-01-06", Open=10.0, Close=10.0),
    ]

    def script(st: Strategy, i: int) -> None:
        if i == 0:
            st.target(weight=0.5)
        elif i == 1:
            st.target(weight=1.0)

    bt = Backtest(cash=20000.0, fee=rate)
    fills = drive(bt, make_strategy(script), bars)

    assert [f.side for f in fills] == ["BUY", "BUY"]
    # 第 1 腿:floor(0.5 * 20000 / 10.01) = 999
    assert fills[0].shares == 999
    assert fills[0].fee == pytest.approx(999 * 10.0 * rate)       # 9.99
    # 第 2 腿:差额 +998(目标 1997 减持仓 999)
    assert fills[1].shares == 998
    assert fills[1].fee == pytest.approx(998 * 10.0 * rate)       # 9.98

    # 终局持仓:1997 股,**不是**按裸 Open 定量的 1999(那个买不起)。
    assert bt.shares == 1997
    assert bt.shares != 1999
    # 手工累加的现金(ADR-043:买入 cash -= gross + fee,逐腿)。
    shadow = 20000.0 - (9990.0 + 9.99) - (9980.0 + 9.98)
    assert bt.cash == pytest.approx(shadow)                       # 10.03
    assert bt.cash >= 0.0
    # 这里**不**断言「余额不足一股」—— 从空仓起才有那个性质(见上一条门)。
    # 加仓时分母是 ADR-014 的可投资产 `cash + shares * Open`,而第 1 腿的
    # 手续费 9.99 已沉没:那笔钱不在 `cash` 里了,却仍以 999 股的形式计入
    # 可投资产,故 `floor` 后余下的现金可以略超一股价。§5 T4 也只把
    # 「成交后 cash < 一股价格」绑在 ADR-012 的 `Open=300` fixture 上
    # (该条款自述:作为独立条款曾是错的),本门不越界复制它。


def test_target_sizing_never_overbuys_across_a_rate_sweep():
    """「永不超买」是对**所有**合法 `(weight, rate)` 的全称命题,不只两点。

    上面两条门钉死了两组具体数值;本门检验那条保证不是在这两点上碰巧成立。
    `weight` 取 ADR-015 闭区间 `[0, 1]` 内的值,`rate` 取 ADR-039 允许的
    `rate >= 0` 中的几档(含 ADR-007 的默认 `0.0`)。

    断言只有两条、都不复用被测算术:**不抛错**,且成交后 `cash >= 0`
    (I1,ADR-010)。*(这不是在算期望股数 —— 那是上面两条门的事。)*
    """
    for rate in (0.0, 0.0005, 0.001, 0.01, 0.05):
        for weight in (0.0, 0.25, 0.5, 0.75, 0.99, 1.0):
            for cash0, price in ((10000.0, 100.0), (20000.0, 10.0), (7777.0, 33.0)):
                bars = [
                    Bar(Date="2020-01-02", Open=price, Close=price),
                    # 第 2 根跳空(ADR-020:仍无条件执行),使分母里的
                    # `shares * Open` 换一个量级。
                    Bar(Date="2020-01-03", Open=price * 1.5, Close=price * 1.5),
                    Bar(Date="2020-01-06", Open=price * 0.7, Close=price * 0.7),
                ]

                def script(st: Strategy, i: int) -> None:
                    # 每天都 re-target 到同一个 weight:ADR-008 使不变的
                    # 差额被跳过,变动的(因跳空)被执行。
                    st.target(weight=weight)

                bt = Backtest(cash=cash0, fee=rate)
                drive(bt, make_strategy(script), bars)   # 不得抛错

                assert bt.cash >= 0.0, (
                    f"I1 被违反(ADR-010/016 的「永不超买」):"
                    f"rate={rate} weight={weight} cash0={cash0} price={price} "
                    f"-> cash={bt.cash}"
                )
                assert bt.shares >= 0


# ═════════════════════════ 值对象与杂项 ═════════════════════════


def test_fill_has_exactly_the_five_adr037_fields():
    """ADR-037:`Fill` **恰好**五个字段 —— `cash_after`/`shares_after` 不在内。"""
    assert [f for f in Fill.__dataclass_fields__] == [
        "date",
        "side",
        "shares",
        "price",
        "fee",
    ]
    f = Fill(date="2020-01-02", side="BUY", shares=1, price=2.0, fee=0.0)
    assert not hasattr(f, "cash_after")
    assert not hasattr(f, "shares_after")
    with pytest.raises(Exception):
        f.shares = 2  # frozen


def test_fill_side_values_come_from_contracts_yaml():
    """`side ∈ {BUY, SELL}` —— `contracts.yaml` 的 `trade_sides`,顺序固定。"""
    buy, sell = CONTRACTS["trade_sides"]
    bt = Backtest(cash=10000.0, fee=0.0)
    bt.enqueue(ShareOrder(shares=10))
    assert bt.resolve_queue(Bar(Date="2020-01-02", Open=10.0)).side == buy
    bt.enqueue(ShareOrder(shares=-10))
    assert bt.resolve_queue(Bar(Date="2020-01-03", Open=10.0)).side == sell


def test_fill_date_is_iso_for_timestamps_and_strings():
    """`Fill.date` 为 `YYYY-MM-DD`(ADR-037 / `formats.date`),与来源无关。

    T1 的 `load_csv` 给 `datetime64`(→ `Timestamp`),T4 的门给字符串;
    两者必须产出同一种 `date`,否则 `trades.csv` 的 date 列随数据来源而变。
    """
    import pandas as pd

    assert bar_date_str(pd.Timestamp("2020-01-02")) == "2020-01-02"
    assert bar_date_str("2020-01-02") == "2020-01-02"

    bt = Backtest(cash=10000.0, fee=0.0)
    bt.enqueue(ShareOrder(shares=1))
    fill = bt.resolve_queue(Bar(Date=pd.Timestamp("2020-03-16"), Open=10.0))
    assert fill.date == "2020-03-16"


def test_target_and_share_orders_are_frozen_value_objects():
    """§1:`TargetOrder` / `ShareOrder` 是**不可变值对象**(ADR-016)。"""
    for obj in (TargetOrder(weight=0.5), ShareOrder(shares=3)):
        with pytest.raises(Exception):
            setattr(obj, next(iter(obj.__dataclass_fields__)), 0)


def test_enqueue_rejects_non_order_and_none():
    """`enqueue` 只接受两个值对象 —— 不该下单时**根本不要调用**它。

    这是 `push_count` 门可信的前提:任何旁路写 `self.queue` 或入队一个
    「空订单」都会让计数失去意义。
    """
    bt = Backtest(cash=10000.0, fee=0.0)
    with pytest.raises(ValueError):
        bt.enqueue(None)
    with pytest.raises(ValueError):
        bt.enqueue(TargetIntent(weight=0.5))  # 策略侧的意图,不是订单


def test_enqueue_intent_translates_strategy_intents():
    """T2↔T4 的接缝:`TargetIntent`/`ShareIntent` → `TargetOrder`/`ShareOrder`。"""
    bt = Backtest(cash=10000.0, fee=0.0)
    assert bt.enqueue_intent(None) is None
    assert bt.queue == []

    o1 = bt.enqueue_intent(TargetIntent(weight=0.25))
    assert isinstance(o1, TargetOrder) and o1.weight == 0.25
    o2 = bt.enqueue_intent(ShareIntent(shares=-5))
    assert isinstance(o2, ShareOrder) and o2.shares == -5
    assert bt.queue == [o2]  # ADR-041:覆盖


def test_resolve_queue_on_empty_queue_returns_none():
    """队列空 → `None`,不抛错、不记账(算法第 1 步)。"""
    bt = Backtest(cash=10000.0, fee=0.0)
    assert bt.resolve_queue(Bar(Date="2020-01-02", Open=100.0)) is None
    assert bt.cash == pytest.approx(10000.0)


def test_resolve_queue_always_dequeues_even_when_skipping():
    """ADR-008 的跳过**不得**把订单留在队列里 —— 那会变成 GTC(OQ-06 ⑤,v1)。"""
    bt = Backtest(cash=10000.0, fee=0.0)
    bt.enqueue(TargetOrder(weight=0.0))  # 空仓时目标 0 股 → 差额 0
    assert bt.resolve_queue(Bar(Date="2020-01-02", Open=100.0)) is None
    assert bt.queue == [], "零差额的订单必须已出队(否则明天会再解析一次 = GTC)"


def test_resolve_queue_rejects_non_positive_open():
    """ADR-019 保证价格 > 0;一个 0/负 Open 到这里只能是算错的中间值。"""
    bt = Backtest(cash=10000.0, fee=0.0)
    bt.enqueue(TargetOrder(weight=1.0))
    with pytest.raises(ValueError, match="必须 > 0"):
        bt.resolve_queue(Bar(Date="2020-01-02", Open=0.0))


def test_engine_does_not_use_close_for_sizing():
    """I7 / ADR-014 的结构性断言:`resolve_queue` 的 bar 只需 `Open` + `Date`。

    不给 `Close` 也必须能成交 —— 若实现读了 `Close`,这里会 AttributeError。
    这把「用错价位」从「数值对不上」提升为「写不出来」。
    """

    @dataclass(frozen=True)
    class OpenOnlyBar:
        Date: str
        Open: float

    bt = Backtest(cash=10000.0, fee=0.0)
    bt.enqueue(TargetOrder(weight=1.0))
    fill = bt.resolve_queue(OpenOnlyBar(Date="2020-01-02", Open=100.0))
    assert fill is not None and fill.shares == 100


def test_engine_module_imports_without_yaml_or_contracts_file():
    """`nullhypothesis` 不得依赖 PyYAML 或 `contracts.yaml` 的存在(审查 J1)。

    与 `tests/test_strategy.py` 的同名门同构:产品代码把钉死值写成常量,
    漂移由门拦住,而不是靠运行时读 YAML。
    """
    import subprocess
    import sys
    import tempfile

    code = (
        "import sys; sys.modules['yaml'] = None; sys.path.insert(0, %r);\n"
        "import nullhypothesis.engine as e;\n"
        "bt = e.Backtest(cash=100.0, fee=0.0);\n"
        "bt.enqueue(e.ShareOrder(shares=1));\n"
        "import dataclasses\n"
        "@dataclasses.dataclass(frozen=True)\n"
        "class B:\n"
        "    Date: str\n"
        "    Open: float\n"
        "f = bt.resolve_queue(B(Date='2020-01-02', Open=10.0));\n"
        "assert f.price == 10.0 and f.shares == 1, f\n"
        "print('OK')\n"
    ) % (str(ROOT),)
    with tempfile.TemporaryDirectory() as empty:
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=empty, capture_output=True, text=True
        )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
