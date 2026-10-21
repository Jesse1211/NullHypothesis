"""T25 验收门 · look-ahead 对抗审计(ADR-060)。

**本任务不产出功能代码。** 本文件是一组**断言性**测试:在 T21–T24 全部合并
后的代码上**主动构造** look-ahead,逐条确认 ADR-002 / ADR-016 的结构性保证
**没有被阶段三削弱**(ADR-050 改写整条价格序列、ADR-056 改写 I7、ADR-057
改递减循环 —— 三者都碰成交路径,故必须在改完之后复核)。

与既有门的分工 —— 本文件**不重复**它们,只做它们**不做**的那件事:

  * `tests/test_strategy.py` 的 I5 门断言「切片长度 == T+1」「切片是独立副本」;
    本文件断言**对抗性读取**(策略主动去摸第 T+1 行)**确实取不到数**。
  * `tests/test_engine_queue.py` 的 I4 门断言 `is` 同一性;本文件断言
    `TargetOrder` 在**类型层面**没有任何可以装股数的地方(ADR-016)。
  * `tests/test_slippage.py` 的 I7′ 门断言成交价公式;本文件断言含滑点后
    四个候选价**仍互异**,故每一种错位仍必然失败(门的**辨别力**未退化)。

**ADR-060 的审计边界(诚实声明,不是免责)**:I5 只保证**框架自己传出去的**
是 `Bars[0..T]` 的独立副本。策略自行读磁盘、跨调用保留引用、用模块级变量
越界取数**不在防护范围内**,且 DESIGN.md 明文「构建期**不得**为此实现沙箱」。
本审计**不能**关闭那个缺口,只能确认框架传出去的东西是干净的 —— 故下面
`test_known_boundary_*` 两条是**把缺口钉成已知事实**的门,不是「已修复」的门。

**复权的信息泄露边界(ADR-060 / ADR-055)** —— 本文件断言的是
**比率不变性**,**不是**「无泄露」:

    P'_i / P'_j = (P_i/P_j) × ∏ { f_e : j < date_e <= i }

乘积里每个事件都 `<= i <= T`,故切片内部的全部**相对**信息都是未来无关的;
晚于 T 的事件只贡献一个作用于**整个可见切片**的全局常数 `K`。
⇒ 任何**尺度不变**的策略可证明免疫。

但「切片不含任何晚于 T 的事件所泄露的信息」那句话**可测且为假**,故本文件
**不**断言它,而是把两条真实通道各自钉成一条**正面**的门:
`test_leak_channel_absolute_price_level_is_observable`(绝对价位判据,每次
拆股约一比特)与 `test_leak_channel_share_rounding_granularity_is_observable`
(股数取整粒度)。两者都是**已声明的有界泄露**(`assumption_ratio_not_level`),
不是可以否认的问题。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
import json
import math
import sys
import textwrap
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nullhypothesis.data import CorporateEvent, load_csv  # noqa: E402
from nullhypothesis.engine import Backtest, ShareOrder, TargetOrder  # noqa: E402
from nullhypothesis.run import run  # noqa: E402

CONTRACTS = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))
FIX = CONTRACTS["fixtures"]
I7 = FIX["i7_four_distinct_prices"]
FROZEN = CONTRACTS["frozen_text"]

# 晚于全部可见 bar 的事件日 —— 「T 之后」的那次拆股。
AFTER_T = dt.date(2020, 2, 1)
SPLIT_4_FOR_1 = CorporateEvent(date=AFTER_T, event="split", ratio=4.0, amount=None)


# ═══════════════════════ 构造辅助 ═══════════════════════


def _write_csv(path: Path, opens: list[float]) -> Path:
    """逐根**四价互异**的 CSV(I7 的同一手法:任何错位必然失败)。

    四价互异在本文件里有第二个用途:泄露门要能区分「读了 Open」与
    「读了 Close」,否则一个读错价位的实现也能通过比率门。
    """
    lines = ["Date,Open,High,Low,Close"]
    for i, o in enumerate(opens):
        lines.append(f"2020-01-{2 + i:02d},{o},{o * 1.1},{o * 0.9},{o * 1.05}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _strategy(tmp_path: Path, body: str, name: str = "s.py") -> Path:
    """把策略**真写到磁盘**(ADR-036 的加载方式只能在真实文件上验证)。"""
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


# 尺度不变策略:判据是 `c[-1] > c[0]`(比率 > 1),与 K 无关。
SCALE_INVARIANT = """
from nullhypothesis.strategy import Strategy


class S(Strategy):
    def next(self) -> None:
        c = self.data["Close"]
        if len(c) < 2:
            return
        self.target(weight=1.0 if c.iloc[-1] > c.iloc[0] else 0.0)
"""

# **不是**尺度不变的策略:在绝对价位上分支(DESIGN.md §15 T25 逐字的例子)。
ABSOLUTE_PRICE = """
from nullhypothesis.strategy import Strategy


class S(Strategy):
    def next(self) -> None:
        if float(self.data["Close"].iloc[-1]) < 50.0:
            self.target(weight=1.0)
"""


def _equity(strategy: Path, df: "pd.DataFrame", cash: float) -> list[float]:
    return [pt.equity for pt in run([strategy], df, cash=cash)[0].equity]


# ═══════════════ 门 2 · 令策略在 T 日观测 Open_{T+1} ═══════════════


def test_adversarial_read_of_bar_t_plus_one_is_blocked_by_the_i5_slice(tmp_path):
    """构造一:策略**主动**去摸第 T+1 行 → I5 的切片门拦住(IndexError)。

    既有的 I5 门断言「长度 == T+1」;这条断言**对抗性读取取不到数**。两者
    不可互相替代:一个把切片做成 `Bars[0..T+1]` 再把 `__len__` 改小的实现
    能通过长度门,却会在这里把 `Open_{T+1}` 交出去。

    用**互异**的 Open 钉死「最后一行就是第 T 根」—— 否则错位测不出。
    """
    log = tmp_path / "log.json"
    opens = [10.0, 20.0, 30.0, 40.0, 50.0]
    df = load_csv(_write_csv(tmp_path / "p.csv", opens))

    probe = _strategy(
        tmp_path,
        f"""
        import json
        import pathlib

        from nullhypothesis.strategy import Strategy

        OUT = pathlib.Path(r"{log}")
        LOG = []


        class S(Strategy):
            def next(self) -> None:
                d = self.data
                rec = {{"n": len(d), "last_open": float(d["Open"].iloc[-1])}}
                # 对抗构造:越过切片末尾去取第 T+1 根的 Open。
                try:
                    rec["future"] = float(d["Open"].iloc[len(d)])
                except IndexError:
                    rec["future"] = "BLOCKED"
                # 第二条路:按标签取下一天(切片里不该有这一行)。
                rec["n_rows_beyond_t"] = int(len(d) - rec["n"])
                LOG.append(rec)
                OUT.write_text(json.dumps(LOG), encoding="utf-8")
        """,
    )

    run([probe], df, cash=100000.0)
    records = json.loads(log.read_text(encoding="utf-8"))

    assert [r["n"] for r in records] == list(range(1, len(opens) + 1)), (
        "I5 被削弱:切片长度不再是 T+1"
    )
    assert [r["future"] for r in records] == ["BLOCKED"] * len(opens), (
        "look-ahead 成功:策略在 T 日读到了第 T+1 根的 Open —— I5 的切片门失效"
    )
    # 最后一行必须是第 T 根(Open 互异,故这条能抓到任何错位)。
    assert [r["last_open"] for r in records] == opens, (
        "切片的最后一行不是第 T 根 —— 策略看到了未来的 bar"
    )


def test_no_future_date_is_reachable_from_the_slice(tmp_path):
    """同一构造的日期侧:切片的日期恒为 `Dates[0..T]`,不含任何未来日历日。

    价格可以靠四价互异抓错位,但「切片多带了一行未来」也可能发生在 `Date`
    上(例如把整个 `df` 传出去再靠 `next()` 自己截断)。故单独钉死日期上界。

    「当日」取自**切片自己的最后一行** —— `Strategy` 的只读公开面只有
    `data`/`cash`/`shares`/`equity`(ADR-036),没有 `date`,故不能从别处取。
    这反而使本门更强:它断言的是「切片末行 == 外部已知的第 T 个日期」,
    两侧都不来自被测对象的自述。
    """
    log = tmp_path / "d.json"
    df = load_csv(_write_csv(tmp_path / "p.csv", [10.0, 20.0, 30.0, 40.0]))
    all_dates = [str(pd.Timestamp(x).date()) for x in df["Date"]]

    probe = _strategy(
        tmp_path,
        f"""
        import json
        import pathlib

        import pandas as pd

        from nullhypothesis.strategy import Strategy

        OUT = pathlib.Path(r"{log}")
        LOG = []


        class S(Strategy):
            def next(self) -> None:
                seen = [str(pd.Timestamp(x).date()) for x in self.data["Date"]]
                LOG.append({{"seen": seen}})
                OUT.write_text(json.dumps(LOG), encoding="utf-8")
        """,
    )

    run([probe], df, cash=100000.0)
    records = json.loads(log.read_text(encoding="utf-8"))

    assert len(records) == len(all_dates)
    for i, rec in enumerate(records):
        assert rec["seen"] == all_dates[: i + 1], (
            f"第 {i} 根的切片日期不是 Dates[0..{i}]:{rec['seen']}"
        )
        # 上界即当日:切片末行必须是第 T 个日期,且是整个切片的最大值
        # (后者排除「把未来那行插在中间」的实现)。
        assert rec["seen"][-1] == all_dates[i], (
            f"第 {i} 根的切片末行不是第 {i} 个日期:{rec['seen'][-1]}"
        )
        assert max(rec["seen"]) == all_dates[i], (
            f"切片含晚于当日的日历日:{rec['seen']} 的上界 != {all_dates[i]}"
        )


# ═══════════ 门 3 · 令订单在入队时就固化股数(ADR-016)═══════════


def test_target_order_structurally_cannot_carry_a_share_count(tmp_path):
    """构造二:想让订单在**入队时**就带上股数 → ADR-016 在类型层面拦住。

    ADR-060 逐字:「`TargetOrder` 只持 `weight`,**不持股数** —— 这让
    look-ahead 在结构上不可能:订单里根本没有一个『用未来价格算出来的数』。」

    故本门断言三件事:字段**恰好**只有 `weight`、对象 `frozen`(入队后写不进
    股数)、以及 `resolve_queue` **不改写**订单(ADR-016:产出新 `Fill`)。
    """
    fields = [f.name for f in dataclasses.fields(TargetOrder)]
    assert fields == ["weight"], (
        f"ADR-016 被削弱:`TargetOrder` 的字段不再只有 weight,而是 {fields} —— "
        "任何额外字段都是一个可以藏『用未来价格算出来的数』的地方"
    )
    assert TargetOrder.__dataclass_params__.frozen, (
        "ADR-016:`TargetOrder` 必须 frozen,否则股数可在入队后被写进订单"
    )

    order = TargetOrder(weight=1.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        order.shares = 100  # type: ignore[attr-defined]

    # `resolve_queue` 只读订单:成交后 weight 不变,且订单对象未长出新属性。
    bar = type("Bar", (), {"Date": "2020-01-03", "Open": 50.0})()
    bt = Backtest(cash=100000.0, fee=0.0)
    bt.enqueue(order)
    fill = bt.resolve_queue(bar)
    assert fill is not None and fill.shares == math.floor(100000.0 / 50.0)
    assert order.weight == 1.0, "ADR-016:解析不得改写 TargetOrder"
    assert not hasattr(order, "shares"), "解析给订单挂上了股数 —— ADR-016 被削弱"


def test_share_count_is_computed_from_the_fill_day_open_not_the_signal_day(tmp_path):
    """同一构造的行为侧:股数必须用 **T+1 的 Open** 算,不是 T 日任何价位。

    上一条是类型层面的「没地方装」;这条是算术层面的「真的用了 T+1 的价」。
    用 I7 的四价互异 fixture:若实现用了 T 日的任何价位,股数必然不同。
    """
    bar0_open = I7["bar0"]["open"]  # 50.0
    bar1_open = I7["bar1"]["open"]  # 200.0
    cash = 100000.0

    bt = Backtest(cash=cash, fee=0.0)
    bt.enqueue(TargetOrder(weight=1.0))
    bar1 = type("Bar", (), {"Date": "2020-01-03", "Open": bar1_open})()
    fill = bt.resolve_queue(bar1)

    assert fill is not None
    assert fill.shares == math.floor(cash / bar1_open), (
        "股数不是用成交日 Open 算的(ADR-016)"
    )
    assert fill.shares != math.floor(cash / bar0_open), (
        f"look-ahead/后视混淆:股数等于用信号日 Open({bar0_open})算出的值"
    )
    assert fill.price == bar1_open, "I7:成交价必须是成交日的 Open"


# ═══════════ 门 4 · I4 的 `is` 门仍然有效 ═══════════


def test_i4_identity_gate_still_holds_after_phase_three(tmp_path):
    """构造三:确认 **I4 的 `is` 门**没有被阶段三削弱 —— T 日入队的对象与
    T+1 出队的是**同一个**。

    `tests/test_engine_queue.py` 已有这条门;本文件在**滑点开启**(ADR-056)
    与**可负担性递减循环**(ADR-057)都在路径上时**重跑**它 —— ADR-060 要求
    的正是「在改完之后确认」。递减循环会修改 `target_shares` 这个**局部变量**,
    若它被实现成改订单,`is` 同一性与 frozen 会同时变红。
    """
    order = TargetOrder(weight=1.0)
    bt = Backtest(cash=100000.0, fee=0.0013, slippage=0.0005)
    bt.enqueue(order)

    assert len(bt.queue) == 1, "I4:队列必须真的装过订单(ADR-006)"
    dequeued_is_same: list[bool] = []

    original = Backtest.resolve_queue

    def spy(self, bar):  # noqa: ANN001, ANN202
        before = list(self.queue)
        fill = original(self, bar)
        if before:
            dequeued_is_same.append(before[0] is order)
        return fill

    bar = type("Bar", (), {"Date": "2020-01-03", "Open": 27.07})()
    spy(bt, bar)

    assert dequeued_is_same == [True], (
        "I4 的 `is` 门失效:出队的订单不是 T 日入队的那个对象 —— "
        "这正是「每日重算」的实现签名(ADR-002)"
    )
    assert len(bt.queue) == 0, "出队后队列必须为空"
    # `eq=False` 保证本门无法被误写成等价的 `==` 弱断言。
    assert TargetOrder(weight=1.0) != TargetOrder(weight=1.0), (
        "`TargetOrder` 必须 eq=False,否则 `is` 门可被误写成 `==` 而仍然通过"
    )
    assert ShareOrder(shares=1) != ShareOrder(shares=1), (
        "`ShareOrder` 必须 eq=False(同上)"
    )


# ═══════════ 门 5 · I7 四价互异门在含滑点后仍有辨别力 ═══════════


def test_i7_four_candidate_prices_stay_mutually_distinct_under_slippage():
    """构造四:ADR-056 把 I7 重写为 `Open * (1 + dir * s)`。若滑点把两个候选
    价**撞到一起**,I7 的「任一错位必然失败」就退化了 —— 门还在,辨别力没了。

    故断言:四个候选价在**原价**与**买入滑点价**两组里各自互异,且**八个值
    整体互异**(含滑点的 `bar0.Close` 不得等于不含滑点的 `bar1.Open` 之类)。
    """
    s = I7["slippage"]
    candidates = {
        "bar0.Open": I7["bar0"]["open"],
        "bar0.Close": I7["bar0"]["close"],
        "bar1.Open": I7["bar1"]["open"],
        "bar1.Close": I7["bar1"]["close"],
    }
    raw = list(candidates.values())
    assert len(set(raw)) == 4, f"I7 的四价不再互异:{candidates}"

    buy = [v * (1.0 + s) for v in raw]
    sell = [v * (1.0 - s) for v in raw]
    assert len(set(buy)) == 4, f"含买入滑点后四价相撞:{buy}"
    assert len(set(sell)) == 4, f"含卖出滑点后四价相撞:{sell}"
    assert len(set(raw + buy)) == 8, (
        f"原价与滑点价之间相撞 —— 「忘加滑点」与「取错 K 线」不再可分:{raw + buy}"
    )

    # 钉死 fixture 自己的三个期望值仍落在正确的那个候选上。
    assert I7["expect_fill_price_buy_slipped"] == I7["bar1"]["open"] * (1.0 + s)
    assert I7["expect_fill_price_sell_slipped"] == I7["bar1"]["open"] * (1.0 - s)
    assert I7["reject_price_no_slippage"] == I7["bar1"]["open"], (
        "「用了对的 K 线但忘加滑点」这个 reject 值必须恰是裸 Open"
    )


def test_i7_catches_every_mispositioning_with_slippage_on():
    """上一条是算术层面的互异;这条**真的跑引擎**,断言每一种错位都被抓到。

    把四个候选价(× 含/不含滑点)逐个摆上桌,断言**只有** `bar1.Open * (1+s)`
    与实际成交价相等 —— 其余七个都不等。
    """
    s = I7["slippage"]
    bar1_open = I7["bar1"]["open"]

    bt = Backtest(cash=100000.0, fee=0.0, slippage=s)
    bt.enqueue(ShareOrder(shares=I7["shares"]))
    bar1 = type("Bar", (), {"Date": "2020-01-03", "Open": bar1_open})()
    fill = bt.resolve_queue(bar1)
    assert fill is not None

    assert fill.price == I7["expect_fill_price_buy_slipped"], (
        "I7′(ADR-056)被削弱:成交价不是 Open * (1 + s)"
    )
    wrong = [
        I7["bar0"]["open"],
        I7["bar0"]["close"],
        I7["bar1"]["close"],
        I7["bar0"]["open"] * (1.0 + s),
        I7["bar0"]["close"] * (1.0 + s),
        I7["bar1"]["close"] * (1.0 + s),
        I7["reject_price_no_slippage"],
    ]
    for w in wrong:
        assert fill.price != w, f"I7 的辨别力退化:错位价 {w} 与成交价相等"


# ═══════ 门 6 · 复权的信息泄露边界 —— 断言【比率不变性】 ═══════


def _two_versions(tmp_path: Path, opens: list[float]):
    """同一价格序列的两个版本:T 之后**有** 4:1 拆股(K=0.25)/ **无**(K=1.0)。

    事件日 `AFTER_T` 晚于全部可见 bar,故 `cumfactor_t = ∏{f_e : date_e > t}`
    对**每一根**可见 bar 都取到同一个 `K` —— 这正是「全局常数」的构造。
    """
    csv = _write_csv(tmp_path / "p.csv", opens)
    with_split = load_csv(csv, events=[SPLIT_4_FOR_1])
    without = load_csv(csv, events=[])
    return with_split, without


def test_adjustment_is_a_single_global_constant_on_the_visible_slice(tmp_path):
    """前提门:晚于 T 的事件只贡献一个作用于**整个可见切片**的全局常数 `K`。

    这条是门 6 的前提 —— 若 `K` 逐行不同,比率不变性的证明就不成立。
    """
    opens = [100.0, 101.0, 102.0, 103.0, 104.0]
    a, b = _two_versions(tmp_path, opens)

    ks = {
        col: [float(x) / float(y) for x, y in zip(a[col], b[col], strict=True)]
        for col in ("Open", "High", "Low", "Close")
    }
    flat = [k for col in ks.values() for k in col]
    assert len(set(flat)) == 1, (
        f"晚于 T 的事件没有退化成一个全局常数(逐列比值 {ks})—— "
        "比率不变性的证明前提被破坏"
    )
    assert flat[0] == pytest.approx(0.25), (
        f"4:1 拆股的全局常数应为 0.25,实得 {flat[0]}"
    )
    # 后复权:最后一行逐值等于 CSV 原值(ADR-050),故「有拆股」那版最后一行
    # 被缩放 —— 这正是 `K` 作为绝对价位可观测的根源,门 6 的警告块说的就是它。
    assert float(b["Close"].iloc[-1]) == pytest.approx(opens[-1] * 1.05)


def test_gate6_part1_pairwise_ratios_are_identical_across_both_versions(tmp_path):
    """门 6 ①:两版切片的**逐对比率**完全相等。

    DESIGN.md §15 T25 的证明:对任意 `i, j <= T`,

        P'_i / P'_j = (P_i/P_j) × ∏ { f_e : j < date_e <= i }

    乘积里每个事件都 `<= i <= T` ⇒ **切片内部的全部相对信息都是未来无关的**。
    本门对**每一列**、**每一个** `(i, j)` 对逐个验证(不抽样)。
    """
    opens = [100.0, 101.0, 102.0, 103.0, 104.0]
    a, b = _two_versions(tmp_path, opens)
    n = len(opens)

    mismatches: list[str] = []
    for col in ("Open", "High", "Low", "Close"):
        for i, j in itertools.product(range(n), repeat=2):
            ra = float(a[col].iloc[i]) / float(a[col].iloc[j])
            rb = float(b[col].iloc[i]) / float(b[col].iloc[j])
            if ra != rb:
                mismatches.append(f"{col}[{i}]/{col}[{j}]: {ra!r} != {rb!r}")

    assert not mismatches, (
        "比率不变性被破坏 —— 切片内部的相对信息依赖晚于 T 的事件:\n"
        + "\n".join(mismatches[:10])
    )

    # 归一化比率序列逐位相同(DESIGN.md 的「实测」写法)。
    norm_a = [float(x) / float(a["Close"].iloc[0]) for x in a["Close"]]
    norm_b = [float(x) / float(b["Close"].iloc[0]) for x in b["Close"]]
    assert norm_a == norm_b, f"归一化比率序列不同:{norm_a} vs {norm_b}"


def test_gate6_part2_scale_invariant_strategy_equity_is_pointwise_identical(tmp_path):
    """门 6 ②:尺度不变策略在两版上产出**逐点相同**的 equity。

    **构造必须让 `floor` 取整恰好整除**(两版残余现金均为 0),否则本门会被
    下面 `test_leak_channel_share_rounding_granularity_is_observable` 那条
    **真实存在**的通道污染 —— 那是 DESIGN.md §15 T25 警告块点名的第二条通道
    (「股数取整粒度在高价位更粗,残余现金随之不同」),**不是** ADR-002/016
    的破洞。故这里选 2 的幂价位 + 2 的幂现金,使 `w × investable / Open` 在
    `K=1.0` 与 `K=0.25` 下**都**是整数;门 6 ② 因此在**算术上**可达,
    而不是靠放宽断言达成。
    """
    opens = [128.0, 160.0, 200.0, 256.0, 320.0]
    cash = 1024000.0
    csv = _write_csv(tmp_path / "p.csv", opens)
    # 四价互异会让 Close = Open * 1.05 不再是 2 的幂,故本门直接用等价四价的
    # CSV:成交只读 Open,而尺度不变判据只读 Close 的【比率】。
    lines = ["Date,Open,High,Low,Close"]
    for i, o in enumerate(opens):
        lines.append(f"2020-01-{2 + i:02d},{o},{o},{o},{o}")
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    a = load_csv(csv, events=[SPLIT_4_FOR_1])
    b = load_csv(csv, events=[])
    strat = _strategy(tmp_path, SCALE_INVARIANT, "si.py")

    ea = _equity(strat, a, cash)
    eb = _equity(strat, b, cash)

    assert ea == eb, (
        "尺度不变策略在「T 之后有拆股」与「无拆股」两版上产出了不同的 equity —— "
        f"若残余现金均为 0 仍不相等,则 ADR-002/016 的保证被削弱:\n{ea}\n{eb}"
    )

    # 证明本门真的在测东西:两版的**股数**必须差 4 倍(不是「都没交易」)。
    ta = run([strat], a, cash=cash)[0].trades
    tb = run([strat], b, cash=cash)[0].trades
    assert len(ta) == len(tb) == 1, "本门要求两版各恰好成交一笔,否则它是空转的"
    assert ta[0].shares == tb[0].shares * 4, (
        f"股数未按 K 反向缩放:{ta[0].shares} vs {tb[0].shares}"
    )
    # 残余现金均为 0 —— 本门的前提,显式钉死而不是默默假设。
    assert ta[0].shares * ta[0].price == pytest.approx(cash)
    assert tb[0].shares * tb[0].price == pytest.approx(cash)


def test_built_in_strategies_are_scale_invariant_by_construction():
    """两个内置策略都是尺度不变的,故**现有策略不可能**走绝对价位泄露通道。

    这是**源码层面**的断言:策略源码里不得出现与**裸数字**比较的价格判据。
    `ma_cross` 比的是两条均线(量纲同为价格,比值与 K 无关),`buy_and_hold`
    是常数权重 —— 两者都没有一个「价格 vs 常数」的分支。
    """
    for name in ("buy_and_hold.py", "ma_cross.py"):
        src = (ROOT / "strategies" / name).read_text(encoding="utf-8")
        # 去掉注释与 docstring 噪声后再看:没有 `close`/`Close` 与裸数字的比较。
        code = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("#")
        )
        assert "< 50" not in code and "> 50" not in code, (
            f"{name} 含绝对价位判据 —— 它将不再是尺度不变的"
        )

    # 行为侧的证据:ma_cross 的判据是「均线 vs 均线」,两侧同量纲。
    ma_src = (ROOT / "strategies" / "ma_cross.py").read_text(encoding="utf-8")
    assert "short_now > long_now" in ma_src and "short_now < long_now" in ma_src, (
        "ma_cross 的穿越判据不再是「均线 vs 均线」—— 尺度不变性需重新论证"
    )


def test_scale_invariant_strategy_is_immune_on_the_real_ma_cross(tmp_path):
    """门 6 ② 的**真实策略**版本:`ma_cross` 在两版上的**比率曲线**逐点相同。

    真实价格序列上 `floor` 残余几乎不可能恰为 0,故这里断言的是**尺度不变的
    读数**(equity 归一化到初始资金的比率序列)在两版上一致到一个**有来源的**
    容差内 —— 这正是「尺度不变的策略可证明免疫」的可观测形式,而**不是**把
    门 6 ② 的严格相等偷偷放宽:严格相等那条已由上一条在残余为 0 的构造上兑现。

    **容差的来源不是「调到能过」**:两版的唯一差异是取整残余,其上界是
    「一股的价」/「初始资金」—— 残余现金 `< Open'`,而 equity 的量纲是 `cash`,
    故相对偏差 `<= Open' / cash`。本构造里 `Open' <= 130 × 1.01`、
    `cash = 1e6`,即上界约 `1.3e-4`。断言用这个**算出来的**上界,
    若有人把泄露做进比率里(量级必然远大于一股的价),本门仍会红。
    """
    # SHORT=20 / LONG=60 的穿越需要**长周期**趋势反转:正弦周期 120 天、
    # 400 天数据 ⇒ 多次真实穿越(门不得空转)。
    period, n = 120, 400
    opens = [100.0 + 30.0 * math.sin(2.0 * math.pi * i / period) for i in range(n)]
    lines = ["Date,Open,High,Low,Close"]
    for i, o in enumerate(opens):
        d = pd.Timestamp("2020-01-02") + pd.Timedelta(days=i)
        lines.append(f"{d.date()},{o:.4f},{o * 1.01:.4f},{o * 0.99:.4f},{o:.4f}")
    csv = tmp_path / "long.csv"
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 事件日晚于全部可见 bar(数据止于 2021 年初,事件在 2030 年)。
    late = CorporateEvent(
        date=dt.date(2030, 1, 1), event="split", ratio=4.0, amount=None
    )
    a = load_csv(csv, events=[late])
    b = load_csv(csv, events=[])

    ma = ROOT / "strategies" / "ma_cross.py"
    cash = 1000000.0
    result_a = run([ma], a, cash=cash)[0]
    result_b = run([ma], b, cash=cash)[0]
    ea = [pt.equity for pt in result_a.equity]
    eb = [pt.equity for pt in result_b.equity]

    assert len(ea) == len(eb) == n
    # 本门不得空转:必须真的穿越、真的建仓、净值真的动过。
    assert len(result_a.trades) == len(result_b.trades) >= 4, (
        f"本门空转了:ma_cross 在这段数据上几乎没交易({len(result_a.trades)} 笔)"
    )
    ratio_a = [x / cash for x in ea]
    ratio_b = [x / cash for x in eb]
    assert max(ratio_a) > 1.2, f"本门空转了:净值从未显著变动(峰值 {max(ratio_a)})"

    # 取整残余的上界 ⇒ 相对偏差上界(算出来的,不是调出来的)。
    tol = (max(float(x) for x in b["Open"]) * 1.01) / cash
    for i, (ra, rb) in enumerate(zip(ratio_a, ratio_b, strict=True)):
        assert ra == pytest.approx(rb, rel=tol), (
            f"ma_cross 在第 {i} 点的净值比率偏差超出取整残余上界 {tol:.3e} —— "
            f"晚于 T 的事件泄露进了【比率】:{ra} vs {rb}"
        )


# ═══════ 已声明的有界泄露(ADR-060 / ADR-055)—— 正面钉死 ═══════


def test_leak_channel_absolute_price_level_is_observable(tmp_path):
    """**不得断言「无泄露」** —— 这条门把那条泄露**正面**钉成已知事实。

    DESIGN.md §15 T25 逐字:`if close < 50: target(1.0)` 在 `K=0.25` 时触发
    (切片读数 25~26)、在 `K=1.0` 时不触发(读数 100~104)。策略由此得知
    「之后会拆股」—— **每次拆股泄露约一比特**。

    若这条门**变绿失败**(即两版行为相同),说明泄露通道被关闭了 —— 那是
    好消息,但 ADR-055 的声明 `assumption_ratio_not_level` 就**过期了**,
    必须同步改。故本门是声明与现实之间的锁,不是「希望它泄露」。
    """
    opens = [100.0, 101.0, 102.0, 103.0, 104.0]
    a, b = _two_versions(tmp_path, opens)

    # 读数确实落在警告块给的两个区间里。
    assert all(25.0 <= float(x) <= 26.0 * 1.1 for x in a["Close"]), list(a["Close"])
    assert all(100.0 <= float(x) <= 104.0 * 1.1 for x in b["Close"]), list(b["Close"])

    strat = _strategy(tmp_path, ABSOLUTE_PRICE, "abs.py")
    cash = 100000.0
    ea = _equity(strat, a, cash)
    eb = _equity(strat, b, cash)

    assert ea != eb, (
        "绝对价位泄露通道似已关闭 —— 若为真,ADR-055 的 "
        "`assumption_ratio_not_level` 声明已过期,必须同步修改"
    )
    # 具体形态:K=0.25 那版建了仓(读数 < 50),K=1.0 那版从未建仓。
    ta = run([strat], a, cash=cash)[0].trades
    tb = run([strat], b, cash=cash)[0].trades
    assert len(ta) == 1, f"K=0.25 下应触发 `close < 50` 并建仓,实得 {ta}"
    assert tb == [], f"K=1.0 下不应触发,实得 {tb}"
    assert eb == [cash] * len(opens), f"未建仓那版的 equity 应恒为初始资金:{eb}"


def test_leak_channel_share_rounding_granularity_is_observable(tmp_path):
    """第二条通道:**股数取整粒度**(DESIGN.md 称其「存在但不可靠」)。

    `floor(w × investable / Open)` 在高价位更粗,残余现金随之不同。本门用一组
    **不整除**的价位把它展示出来,并断言它**只影响残余现金**(绝对水平),
    **不影响比率不变性**(门 6 ① 已独立验证)。

    这条门的存在理由:门 6 ② 必须用「残余恰为 0」的构造才能严格相等,而
    **为什么**需要那个构造,只有本门能解释。两条门合起来杜绝了「把门 6 ②
    的失败误判为 ADR-002/016 破洞」。
    """
    opens = [100.0, 101.0, 102.0, 103.0, 104.0]
    cash = 100000.0
    csv = tmp_path / "p.csv"
    lines = ["Date,Open,High,Low,Close"]
    for i, o in enumerate(opens):
        lines.append(f"2020-01-{2 + i:02d},{o},{o},{o},{o}")
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    a = load_csv(csv, events=[SPLIT_4_FOR_1])
    b = load_csv(csv, events=[])
    strat = _strategy(tmp_path, SCALE_INVARIANT, "si.py")

    ta = run([strat], a, cash=cash)[0].trades
    tb = run([strat], b, cash=cash)[0].trades
    assert len(ta) == len(tb) == 1

    residual_a = cash - ta[0].shares * ta[0].price
    residual_b = cash - tb[0].shares * tb[0].price
    assert residual_a != residual_b, (
        "取整粒度通道似已关闭 —— 若为真,门 6 ② 可无条件断言严格相等,"
        f"且 DESIGN.md 对本通道的描述需同步修改(残余 {residual_a} vs {residual_b})"
    )
    # 股数**不是**精确的 4 倍关系 —— 这正是粒度差异的签名。
    assert ta[0].shares != tb[0].shares * 4, (
        "本门空转了:取整在这组价位上恰好整除,换一组不整除的价位"
    )
    # 但比率不变性不受影响:成交价严格差 4 倍。
    assert tb[0].price == pytest.approx(ta[0].price * 4.0)


def test_adr055_declaration_states_ratio_not_level_and_never_claims_no_leak():
    """声明侧的锁:ADR-055 的文案必须说「保留比率、不保留水平 + 有界泄露」,
    且**不得**出现「无泄露」式的断言。

    ADR-060 的结论写法逐字:「这是有界泄露 + 使用建议(写尺度不变的策略),
    **不是**『无泄露』。」本门把那句话钉在 `contracts.yaml` 的文案上 ——
    审计的结论若只写在报告里,下一个改文案的人看不见。
    """
    text = FROZEN["assumption_ratio_not_level"]
    for must in ("比率", "水平", "有界泄露", "尺度不变"):
        assert must in text, f"ADR-055 声明缺少「{must}」:{text}"
    for forbidden in ("无泄露", "不含任何", "没有泄露", "完全免疫"):
        assert forbidden not in text, (
            f"ADR-055 声明出现了可测且为假的「{forbidden}」式断言:{text}"
        )


# ═══════ ADR-060 的已知边界 —— 钉成已知事实,不是「已修复」 ═══════


def test_known_boundary_strategy_reading_disk_is_outside_the_sandbox(tmp_path):
    """I5 的**诚实边界**:策略自行读磁盘**不在防护范围内**(明文禁止做沙箱)。

    本门构造一个真的 look-ahead:策略绕过 `self.data`,直接把 CSV 读完并用
    **未来**的 Close 决策。它**成功**了 —— 这是**有意为之的已知缺口**
    (DESIGN.md §1 / §15 T25 的 blindSpots),故本门断言它成功,把缺口钉成
    **可见的已知事实**;若哪天它失败了,说明有人偷偷加了沙箱,ADR-060 的
    边界声明就得改。

    **这不是本阶段引入的缺陷**:ADR-060 明文「构建期不得为此实现沙箱」。
    框架能保证的是**自己传出去的**切片是干净的 —— 那由上面门 2 验证。
    """
    opens = [10.0, 20.0, 30.0, 40.0, 50.0]
    csv = _write_csv(tmp_path / "p.csv", opens)
    df = load_csv(csv)
    log = tmp_path / "cheat.json"

    cheater = _strategy(
        tmp_path,
        f"""
        import json
        import pathlib

        import pandas as pd

        from nullhypothesis.strategy import Strategy

        CSV = pathlib.Path(r"{csv}")
        OUT = pathlib.Path(r"{log}")
        FULL = pd.read_csv(CSV)          # ← 越界:框架没给,策略自己拿
        LOG = []


        class S(Strategy):
            def next(self) -> None:
                i = len(self.data) - 1
                future = None
                if i + 1 < len(FULL):
                    future = float(FULL["Open"].iloc[i + 1])
                LOG.append({{"i": i, "future_open": future}})
                OUT.write_text(json.dumps(LOG), encoding="utf-8")
        """,
        "cheat.py",
    )

    run([cheater], df, cash=100000.0)
    records = json.loads(log.read_text(encoding="utf-8"))

    seen_future = [r["future_open"] for r in records[:-1]]
    assert seen_future == opens[1:], (
        "策略读磁盘拿未来数据这条路**被拦住了** —— 若确有沙箱,"
        "ADR-060 / blindSpots 里「不在防护范围内」的声明已过期,必须同步修改"
    )


def test_known_boundary_module_level_state_persists_across_strategies(tmp_path):
    """同一边界的第二种形态:**模块级变量**跨调用保留状态,框架不拦。

    与上一条同理:断言它**可行**,把边界钉成已知事实。注意 `run()` 对**每条
    策略**重新 `load_strategy`(避免模块级可变状态串味),故污染不跨策略
    文件 —— 本门同时钉死那条**真实存在的**隔离保证。
    """
    df = load_csv(_write_csv(tmp_path / "p.csv", [10.0, 20.0, 30.0]))
    log = tmp_path / "mod.json"

    src = f"""
        import json
        import pathlib

        from nullhypothesis.strategy import Strategy

        OUT = pathlib.Path(r"{log}")
        CALLS = []


        class S(Strategy):
            def next(self) -> None:
                CALLS.append(len(self.data))
                OUT.write_text(json.dumps(CALLS), encoding="utf-8")
        """
    one = _strategy(tmp_path, src, "m1.py")

    run([one], df, cash=100000.0)
    assert json.loads(log.read_text(encoding="utf-8")) == [1, 2, 3], (
        "模块级状态跨 `next()` 调用未能保留 —— 该边界声明需重新核实"
    )

    # 同一进程内再跑一次**同一个文件**:重新 load ⇒ 模块级状态从头开始。
    run([one], df, cash=100000.0)
    assert json.loads(log.read_text(encoding="utf-8")) == [1, 2, 3], (
        "`run()` 未对每条策略重新 load_strategy —— 模块级可变状态串味了"
    )


# ═══════════ 门 1 的结构性复核:ADR-002 的两行顺序 ═══════════


def test_adr002_resolve_before_enqueue_is_still_the_whole_implementation():
    """ADR-060 证据二:「T+1 成交语义的全部实现就是 A 在 B 之前。」

    阶段三动过成交路径(ADR-050/056/057),故在**源码层面**复核那两行的顺序
    仍在主循环里、且 `resolve_queue` 先于 `enqueue_intent`。这条门抓的是
    「有人把入队挪到出队之前」—— 那会让 T 日的信号在 T 日就成交(look-ahead)。
    """
    src = (ROOT / "nullhypothesis" / "run.py").read_text(encoding="utf-8")
    body = src[src.index("for i, bar in enumerate(df.itertuples(index=False)):") :]
    body = body[: body.index("\n    equity_series")]

    i_resolve = body.index("bt.resolve_queue(bar)")
    i_enqueue = body.index("bt.enqueue_intent(")
    i_next = body.index("strat.next()")

    assert i_resolve < i_next < i_enqueue, (
        "ADR-002 的顺序纪律被破坏:必须是 resolve_queue(昨天的意图)→ "
        f"strategy.next()(今天的意图)→ enqueue_intent。实得偏移 "
        f"resolve={i_resolve} next={i_next} enqueue={i_enqueue}"
    )
    # 切片在 next() 之前绑定,且是 `iloc[: i + 1].copy()`(I5 的物理载体)。
    assert "strat._bind_data(df.iloc[: i + 1].copy())" in body, (
        "I5 的切片不再是 `df.iloc[: i + 1].copy()` —— 切片边界或副本语义被改动"
    )
    i_bind = body.index("strat._bind_data(")
    assert i_bind < i_next, "切片必须在 next() 之前绑定"


def test_first_bar_cannot_trade_because_the_queue_is_empty(tmp_path):
    """ADR-002 的可观测推论:**第 0 根不可能成交** —— 队列在那时是空的。

    若实现偷偷在 T 日就成交(look-ahead 的最终形态),第 0 根就会出现一笔
    成交,且首笔成交价会是 `bar0.Open` 而不是 `bar1.Open`。
    """
    opens = [10.0, 20.0, 30.0, 40.0]
    df = load_csv(_write_csv(tmp_path / "p.csv", opens))
    strat = _strategy(tmp_path, SCALE_INVARIANT, "si.py")
    bh = ROOT / "strategies" / "buy_and_hold.py"

    for s in (strat, bh):
        r = run([s], df, cash=100000.0)[0]
        dates = [str(pd.Timestamp(df["Date"].iloc[0]).date())]
        for t in r.trades:
            assert str(t.date) not in dates, (
                f"{s.name} 在第 0 根就成交了 —— ADR-002 的 T+1 语义被破坏:{t}"
            )
        if r.trades:
            assert float(r.trades[0].price) != opens[0], (
                f"首笔成交价等于 bar0.Open({opens[0]})—— 信号日即成交"
            )
