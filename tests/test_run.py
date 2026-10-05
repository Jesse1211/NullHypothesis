"""T5 的验收门 · 主循环 + 跳空统计 + Summary。

期望值一律引用 `contracts.yaml` 的 key,不硬编码 —— 第 9 轮的实证表明,
门里复述的字面量会与契约各自漂移,而两边都绿。
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path

import pytest
import yaml

from nullhypothesis.data import load_csv
from nullhypothesis.run import RunResult, compute_gaps, run

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))
FX = CONTRACTS["fixtures"]
RATE = 0.001  # 影子账本用的字面费率 —— 见 test_i3_shadow_ledger 的隔离要求


# ───────────────────────────── 构造真实 CSV ─────────────────────────────


def _csv(tmp_path: Path, dates, opens, closes, name="d.csv") -> Path:
    rows = ["Date,Open,High,Low,Close,Volume"]
    for d, o, c in zip(dates, opens, closes):
        rows.append(f"{d},{o},{max(o, c) + 1},{min(o, c) - 1},{c},1000")
    p = tmp_path / name
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def _strategy(tmp_path: Path, body: str, name="s.py") -> Path:
    p = tmp_path / name
    p.write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "class S(Strategy):\n" + body,
        encoding="utf-8",
    )
    return p


BUY_AND_HOLD = "    def next(self):\n        self.target(weight=1.0)\n"
FLAT = "    def next(self):\n        self.target(weight=0.0)\n"


# ───────────────────────── I6 / I3:账本与曲线 ─────────────────────────


def test_i6_equity_length_equals_trading_days(tmp_path):
    d = _csv(tmp_path, ["2020-01-02", "2020-01-03", "2020-01-06"],
             [100, 105, 92], [100, 105, 92])
    r = run([_strategy(tmp_path, BUY_AND_HOLD)], load_csv(d), cash=10000.0)[0]
    assert len(r.equity) == 3 == r.summary.bars


def test_i3_shadow_ledger_every_trading_day(tmp_path):
    """I3 逐日对账 —— 用**独立影子账本**,不得退化为同义反复。

    隔离要求(与 T3 的门相同):算术全部写在本文件内、字面写死 rate、
    **不读 `fill.fee`**、不借被测代码算期望值。否则错误的费率会在两侧
    同时出现、互相对消。

    **读 `equity[i].cash` 而非 `account.cash`** —— §1 声明 Account
    不可被聚合外引用。

    Close 全不相同,使「用错了哪一根的 Close」必然失败。
    """
    dates = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
    opens = [100.0, 110.0, 120.0, 130.0]
    closes = [101.0, 113.0, 127.0, 137.0]          # 两两不同
    d = _csv(tmp_path, dates, opens, closes)
    df = load_csv(d)
    r = run([_strategy(tmp_path, BUY_AND_HOLD)], df, cash=10000.0, fee=RATE)[0]

    shadow_cash, shadow_shares = 10000.0, 0
    fills = {f.date: f for f in r.trades}
    for i, pt in enumerate(r.equity):
        f = fills.get(pt.date)
        if f is not None:
            gross = f.shares * f.price
            fee = gross * RATE                       # 自算,不读 f.fee
            assert f.fee == pytest.approx(fee, abs=1e-9), "引擎的 fee 与影子自算不符"
            if f.side == "BUY":
                shadow_cash -= gross + fee
                shadow_shares += f.shares
            else:
                shadow_cash += gross - fee
                shadow_shares -= f.shares
        assert pt.cash == pytest.approx(shadow_cash, abs=0.01)
        assert pt.shares == shadow_shares
        assert pt.equity == pytest.approx(
            shadow_cash + shadow_shares * closes[i], abs=0.01
        )


def test_fee_closure(tmp_path):
    """手续费既未漏算也未双算:影子侧自算的 sum(fee) 与现金总变动闭合。"""
    d = _csv(tmp_path, ["2020-01-02", "2020-01-03", "2020-01-06"],
             [100.0, 110.0, 120.0], [100.0, 110.0, 120.0])
    r = run([_strategy(tmp_path, BUY_AND_HOLD)], load_csv(d), cash=10000.0, fee=RATE)[0]
    gross = sum(f.shares * f.price * (1 if f.side == "BUY" else -1) for f in r.trades)
    fees = sum(f.shares * f.price * RATE for f in r.trades)   # 自算
    assert 10000.0 - r.equity[-1].cash == pytest.approx(gross + fees, abs=0.01)


# ───────────────────────── I5:策略只能看到过去 ─────────────────────────


def test_i5_slice_is_an_independent_copy(tmp_path):
    """三条:改写不影响结果、不抛 SettingWithCopyWarning、每次是新副本。"""
    dates = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
    opens, closes = [100, 110, 120, 130], [101, 113, 127, 137]
    d = _csv(tmp_path, dates, opens, closes)
    df = load_csv(d)

    clean = run([_strategy(tmp_path, BUY_AND_HOLD, "clean.py")], df, cash=10000.0)[0]

    # 两种写法:`.loc` 赋值(走 pandas 的 setitem 路径)与直写 `.values`
    # (绕过 pandas,直接动底层 ndarray —— 视图的话会改到原 DataFrame)。
    # 都写**数值列**:写 `Date` 列会触发与副本语义无关的 dtype FutureWarning。
    mutating = _strategy(
        tmp_path,
        "    def next(self):\n"
        "        self.data.loc[self.data.index[0], 'Open'] = -1.0\n"
        "        self.data['Close'].values[:] = 0.0\n"
        "        self.target(weight=1.0)\n",
        "mut.py",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")          # 视图会抛 SettingWithCopyWarning
        dirty = run([mutating], df, cash=10000.0)[0]

    # 原 DataFrame 必须毫发无损 —— 否则传出去的是视图
    assert df["Open"].tolist() == [float(o) for o in opens]
    assert df["Close"].tolist() == [float(c) for c in closes]

    assert [p.equity for p in clean.equity] == [p.equity for p in dirty.equity]


def test_i5_each_call_gets_a_fresh_copy(tmp_path):
    """第 i 根保存的引用,到第 j>i 根时行数仍为 i+1 —— 不是一份被反复 re-slice 的同一对象。"""
    d = _csv(tmp_path, ["2020-01-02", "2020-01-03", "2020-01-06"],
             [100, 110, 120], [100, 110, 120])
    keeper = _strategy(
        tmp_path,
        "    def init(self):\n        self._kept = None\n"
        "    def next(self):\n"
        "        if self._kept is None:\n            self._kept = self.data\n"
        "        assert len(self._kept) == 1, f'saved slice grew to {len(self._kept)}'\n"
        "        self.target(weight=0.0)\n",
        "keep.py",
    )
    run([keeper], load_csv(d), cash=10000.0)     # 断言在策略内,不抛即通过


def test_strategy_sees_exactly_bars_up_to_today(tmp_path):
    d = _csv(tmp_path, ["2020-01-02", "2020-01-03", "2020-01-06"],
             [100, 110, 120], [100, 110, 120])
    probe = _strategy(
        tmp_path,
        "    def next(self):\n"
        "        assert len(self.data) == self._bar_index + 1\n"
        "        self.target(weight=0.0)\n",
        "probe.py",
    )
    run([probe], load_csv(d), cash=10000.0)


# ───────────────────────── 退化场景 ─────────────────────────


def test_zero_trade_strategy_is_a_flat_line(tmp_path):
    d = _csv(tmp_path, ["2020-01-02", "2020-01-03", "2020-01-06"],
             [100, 110, 120], [100, 110, 120])
    r = run([_strategy(tmp_path, FLAT)], load_csv(d), cash=10000.0)[0]
    assert r.trades == []
    assert {p.equity for p in r.equity} == {10000.0}
    assert r.summary.trade_count == 0


def test_buy_and_hold_trades_once(tmp_path):
    """ADR-008:天天调 target(1.0),但只成交一次 —— 幂等是引擎的责任。"""
    d = _csv(tmp_path, [f"2020-01-{i:02d}" for i in (2, 3, 6, 7, 8)],
             [100, 110, 120, 130, 140], [100, 110, 120, 130, 140])
    r = run([_strategy(tmp_path, BUY_AND_HOLD)], load_csv(d), cash=10000.0)[0]
    assert r.summary.trade_count == 1


def test_single_row_csv_has_no_gaps_and_no_trades(tmp_path):
    """ADR-023:只有 1 行时没有 T+1,任何订单都被丢弃;跳空三值为 None 不抛异常。"""
    d = _csv(tmp_path, ["2020-01-02"], [100], [100])
    r = run([_strategy(tmp_path, BUY_AND_HOLD)], load_csv(d), cash=10000.0)[0]
    assert r.trades == []
    assert r.summary.bars == 1
    assert r.summary.final_equity == r.summary.initial_cash
    assert (r.summary.max_gap_pct, r.summary.max_gap_date,
            r.summary.mean_abs_gap_pct) == (None, None, None)


def test_last_bar_intent_is_discarded(tmp_path):
    """ADR-005:最后一根上的意图无法兑现 —— 兑现它需要一个不存在的明天。"""
    d = _csv(tmp_path, ["2020-01-02", "2020-01-03"], [100, 110], [100, 110])
    only_last = _strategy(
        tmp_path,
        "    def next(self):\n"
        "        if self._bar_index == 1:\n            self.target(weight=1.0)\n",
        "last.py",
    )
    r = run([only_last], load_csv(d), cash=10000.0)[0]
    assert r.trades == []


# ───────────────────────── ADR-019:排序后等价 ─────────────────────────


def test_shuffled_csv_yields_identical_equity(tmp_path):
    """pandas 排序后若留下陈旧 RangeIndex,之后每个 i+1 查找都读错 bar。"""
    dates = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
    opens, closes = [100, 110, 120, 130], [101, 113, 127, 137]
    ordered = _csv(tmp_path, dates, opens, closes, "ordered.csv")

    idx = [2, 0, 3, 1]
    shuffled = _csv(tmp_path, [dates[i] for i in idx], [opens[i] for i in idx],
                    [closes[i] for i in idx], "shuffled.csv")

    s = _strategy(tmp_path, BUY_AND_HOLD)
    a = run([s], load_csv(ordered), cash=10000.0)[0]
    b = run([s], load_csv(shuffled), cash=10000.0)[0]
    assert [p.equity for p in a.equity] == [p.equity for p in b.equity]


# ───────────────────────── 多策略隔离 ─────────────────────────


def test_strategy_results_are_isolated(tmp_path):
    """模块级可变状态、复用的 Backtest、importlib 缓存都会让这条失败。"""
    d = _csv(tmp_path, ["2020-01-02", "2020-01-03", "2020-01-06"],
             [100, 110, 120], [100, 110, 120])
    df = load_csv(d)
    a = _strategy(tmp_path, BUY_AND_HOLD, "a.py")
    b = _strategy(tmp_path, FLAT, "b.py")

    alone = run([a], df, cash=10000.0)[0]
    together = run([a, b], df, cash=10000.0)[0]
    assert [p.equity for p in alone.equity] == [p.equity for p in together.equity]


def test_identical_strategies_under_different_names(tmp_path):
    """run([A, A_copy]) 产生两条**相同**曲线,而不是一条。

    不能写 run([A, A]) —— ADR-037 规定同 stem 报错(在 CLI 层),
    那样会红掉正确实现。
    """
    d = _csv(tmp_path, ["2020-01-02", "2020-01-03", "2020-01-06"],
             [100, 110, 120], [100, 110, 120])
    a = _strategy(tmp_path, BUY_AND_HOLD, "a.py")
    a_copy = _strategy(tmp_path, BUY_AND_HOLD, "a_copy.py")
    rs = run([a, a_copy], load_csv(d), cash=10000.0)
    assert len(rs) == 2
    assert rs[0].strategy != rs[1].strategy
    assert [p.equity for p in rs[0].equity] == [p.equity for p in rs[1].equity]


# ───────────────────────── ADR-021:跳空统计 ─────────────────────────


def _gap_fixture_csv(tmp_path):
    f = FX["adr021_gaps"]
    return _csv(tmp_path, f["dates"], f["opens"], f["closes"], "gaps.csv"), f


def test_compute_gaps_matches_the_contract(tmp_path):
    d, f = _gap_fixture_csv(tmp_path)
    df = load_csv(d)
    gaps = compute_gaps(df)
    assert gaps == f["gaps_pct"]
    assert len(gaps) == len(f["dates"]) - 1, "第 1 根 K 线不产生跳空"


def test_gap_stats_are_three_way_discriminating(tmp_path):
    """三种错误实现各有一条:max() 而非 max(abs)、带符号均值、边界多一个 gap。"""
    d, f = _gap_fixture_csv(tmp_path)
    r = run([_strategy(tmp_path, FLAT)], load_csv(d), cash=10000.0)[0]
    s = r.summary

    assert s.max_gap_pct == f["expect_max_gap_pct"]
    assert s.max_gap_pct != f["reject_max_plain"], "按绝对值选取,不是 max()"
    assert s.max_gap_date == f["expect_max_gap_date"]
    assert s.mean_abs_gap_pct == f["expect_mean_abs_gap_pct"]
    assert s.mean_abs_gap_pct != f["reject_mean_signed"], "绝对值均值,不是带符号均值"


def test_gap_tie_breaks_to_the_earlier_date(tmp_path):
    """ADR-021 平手取较早 —— 最易被实现成「max() 碰巧返回哪个就哪个」。"""
    tie = FX["adr021_gap_tie"]["gaps_pct"]          # [+5, -5]
    dates = ["2020-01-02", "2020-01-03", "2020-01-06"]
    closes = [100.0, 100.0, 100.0]
    opens = [100.0, 100.0 * (1 + tie[0] / 100), 100.0 * (1 + tie[1] / 100)]
    d = _csv(tmp_path, dates, opens, closes, "tie.csv")
    r = run([_strategy(tmp_path, FLAT)], load_csv(d), cash=10000.0)[0]
    assert abs(tie[0]) == abs(tie[1]), "平手 case 必须真的平手"
    assert r.summary.max_gap_date == dates[1], "相等时取较早"


# ───────────────────────── Summary 的数值门 ─────────────────────────


def test_total_return_is_a_percentage_not_a_ratio(tmp_path):
    """整个 API 面最高后果的缺陷:比率未乘 100。

    T10 的 CLI↔API 对账两侧同源因而抓不到它,T13 的 I8 表只在 mock 上验前端。
    **此门是唯一能抓到它的地方**,所以它必须在有真实数据的 T5。
    """
    f = FX["t5_summary_values"]
    initial, final = f["initial_cash"], f["final_equity"]      # 10000 -> 12500
    # 一股 100 元:满仓买 100 股,收盘价涨到 125 → 期末正好 12500
    d = _csv(tmp_path, ["2020-01-02", "2020-01-03", "2020-01-06"],
             [100.0, 100.0, 100.0], [100.0, 100.0, 125.0], "ret.csv")
    r = run([_strategy(tmp_path, BUY_AND_HOLD)], load_csv(d), cash=initial)[0]
    s = r.summary

    assert s.final_equity == final
    assert s.total_return_pct == f["expect_total_return_pct"]
    assert s.total_return_pct != f["reject_ratio_unscaled"], "比率未乘 100"
    assert s.total_return_pct != f["reject_wrong_quotient"], "误用期末/初始之比"


def test_summary_scalar_fields(tmp_path):
    dates = ["2020-01-02", "2020-01-03", "2020-01-06"]
    d = _csv(tmp_path, dates, [100, 110, 120], [100, 110, 120])
    r = run([_strategy(tmp_path, BUY_AND_HOLD)], load_csv(d), cash=10000.0)[0]
    s = r.summary
    assert s.start == dates[0] and s.end == dates[-1]
    assert s.bars == len(dates)
    assert s.initial_cash == 10000.0
    assert s.trade_count == len(r.trades)          # ADR-042
    assert s.final_equity == r.equity[-1].equity


def test_trades_ledger_parallels_trades(tmp_path):
    """T7 不得 import account,故 cash_after/shares_after 必须由聚合根提供。"""
    d = _csv(tmp_path, ["2020-01-02", "2020-01-03", "2020-01-06"],
             [100, 110, 120], [100, 110, 120])
    r = run([_strategy(tmp_path, BUY_AND_HOLD)], load_csv(d), cash=10000.0)[0]
    assert len(r.trades_ledger) == len(r.trades)
    for (cash_after, shares_after) in r.trades_ledger:
        assert cash_after >= 0 and shares_after >= 0


def test_run_result_has_no_png_path():
    """png_path 的值含 run_id,而 run_id 由 CLI 层的 create_archive_dir 产生 ——
    聚合根不拥有输出目录(ADR-044)。"""
    assert "png_path" not in RunResult.__dataclass_fields__


# ═══════════ 门:买入持有的净值曲线必须对上价格曲线(ADR-049)═══════════
#
# v0 的验收标准是「一条曲线」,而**买入持有是唯一有封闭解的策略** ——
# 它的净值形状必须与价格形状完全一致。这是整条管线最强的一个外部校验:
# 引擎里任何一处把价格、股数、现金或估值搞错,这条门都会红,而曲线本身
# 看起来仍然完全合理。
#
# 精确表述(不是「大致成比例」):
#     equity[i] == shares[i] * Close[i] + cash[i]        逐日,零误差
#     (equity[i] - cash[i]) / Close[i] == 持股数          【完全恒定】
#
# **必须减掉 cash**:买不满整股会剩一点现金(实测 fee=0 时 0.10、
# fee=0.0013 时 6.78),那是个固定偏移。直接用 `equity/Close` 会看到
# 8.3e-03 的漂移 —— 那不是缺陷,是残余现金。不减 cash 的门要么写不出来,
# 要么得用一个松到抓不住真缺陷的容差。


def _bah_run(fee: float, cash: float = 100000.0):
    """跑一次买入持有,返回 (RunResult, Close 列表)。"""
    df = load_csv(ROOT / "data" / "synthetic.csv")
    r = run([ROOT / "strategies" / "buy_and_hold.py"], df, cash=cash, fee=fee)[0]
    return r, df["Close"].tolist()


@pytest.mark.parametrize("fee", [0.0, 0.0013])
def test_buy_and_hold_equity_decomposes_exactly(fee):
    """`equity == shares * Close + cash`,逐日**零误差**。

    这同时验了三件事:估值用的是 Close(ADR-003)、持股数没漂、
    现金没被重复计入。
    """
    r, closes = _bah_run(fee)
    assert len(r.equity) == len(closes)
    for i, p in enumerate(r.equity):
        assert p.equity == pytest.approx(p.shares * closes[i] + p.cash, abs=1e-9), (
            f"第 {i} 根({p.date}):equity={p.equity} 与 "
            f"shares*Close+cash={p.shares * closes[i] + p.cash} 不符"
        )


@pytest.mark.parametrize("fee", [0.0, 0.0013])
def test_buy_and_hold_tracks_price_with_a_constant_ratio(fee):
    """**买入持有的净值曲线对上价格曲线** —— 成交后比值完全恒定。

    `(equity - cash) / Close` 必须逐日**恒等于持股数**。任何一处把成交价
    记错、把股数算错、或把估值基准弄错,这个比值都会漂。
    """
    r, closes = _bah_run(fee)
    held = r.equity[-1].shares
    assert held > 0, "前提:买入持有最终必须真的持仓"

    # 第 0 根还没成交(T+1 语义),从第 1 根起比
    ratios = [
        (p.equity - p.cash) / closes[i]
        for i, p in enumerate(r.equity) if i >= 1
    ]
    assert min(ratios) == pytest.approx(held, abs=1e-9)
    assert max(ratios) == pytest.approx(held, abs=1e-9)
    assert max(ratios) - min(ratios) < 1e-9, (
        f"比值不恒定:{min(ratios)} .. {max(ratios)} —— 净值没有跟住价格"
    )


def test_buy_and_hold_fee_only_effect_is_fewer_shares():
    """手续费的**唯一**影响是少买几股,于是投入部分**永远**低一个固定比例。

    这是用户那句「两条曲线永远有相同的差值比例」的可执行形式。实测:
    3658/3663 = 0.9986349986,逐点一致到 1e-12。

    反向也断言:比值必须 **< 1**(真的更差)且 **> 0.99**(只是手续费,
    不是算错了量级)—— 否则「恒定」可以被一个恒为 1.0 的实现满足,
    那正是「手续费没生效」的样子。
    """
    a, _ = _bah_run(0.0)
    b, _ = _bah_run(0.0013)

    sa, sb = a.equity[-1].shares, b.equity[-1].shares
    assert sb < sa, f"收了手续费却没有少买股数:{sb} vs {sa}"

    inv_a = [p.equity - p.cash for p in a.equity][1:]
    inv_b = [p.equity - p.cash for p in b.equity][1:]
    ratios = [y / x for x, y in zip(inv_a, inv_b)]

    assert max(ratios) - min(ratios) < 1e-12, "差值比例不恒定"
    assert ratios[0] == pytest.approx(sb / sa, abs=1e-12), (
        "比例必须恰好等于股数比 —— 手续费的影响只经由股数发生"
    )
    assert 0.99 < ratios[0] < 1.0, (
        f"比例 {ratios[0]} 不在 (0.99, 1.0) 内 —— 要么手续费没生效"
        f"(恒为 1.0),要么量级算错了"
    )


@pytest.mark.parametrize("fee", [0.0, 0.0013])
def test_buy_and_hold_residual_cash_is_preserved_exactly(fee):
    """买不满整股剩下的现金必须**原样留着**,不得被抹掉。

    上面那两条比值门都减掉了 `cash`,所以它们对 `cash` 本身是**盲的** ——
    实测:在 `Account.apply` 里把买入后的小额余额清零(`< 10 → 0.0`),
    458 条门**全绿**。I3 的影子账本门用的是 `rate=0.001`,余额大于这个
    阈值,所以也碰不到。

    精确断言:成交后每一天的 `cash` 恒等于「初始资金 − 成交额 − 手续费」,
    且 > 0(本 fixture 下买不满整股必然有余额)。
    """
    r, _ = _bah_run(fee)
    f = r.trades[0]
    expect = 100000.0 - f.amount - f.fee
    assert expect > 0.0, "前提:本 fixture 下必须真的剩下一点现金"

    for p in r.equity[1:]:
        assert p.cash == pytest.approx(expect, abs=1e-9), (
            f"{p.date}:残余现金 {p.cash} != 初始 − 成交额 − 手续费 {expect}"
        )
    # 反向:它确实是个非零的小额,不是「恰好为 0 所以断言无意义」
    assert 0.0 < expect < f.price, (
        f"残余现金 {expect} 应在 (0, 一股价格) 内 —— 否则本门测不到东西"
    )


@pytest.mark.parametrize("fee", [0.0, 0.0013])
def test_equity_point_close_is_the_same_close_used_for_valuation(fee):
    """ADR-049:`EquityPoint.close` 必须是 CSV 的当日 Close **原值**。

    它是前端画价格对照曲线的唯一来源(ADR-025 禁止前端自己取价),
    所以不能是任何派生值。
    """
    r, closes = _bah_run(fee)
    for i, p in enumerate(r.equity):
        assert p.close == closes[i], f"第 {i} 根:close={p.close} != CSV {closes[i]}"


def test_zero_trade_strategy_equity_is_flat_while_price_moves():
    """反向对照:**不交易**时净值必须是水平线,而价格在动。

    没有这条,一个「把 equity 直接设成 Close * 常数」的错误实现会通过
    上面所有比值门 —— 它在买入持有下看起来完美。
    """
    df = load_csv(ROOT / "data" / "synthetic.csv")
    src = ROOT / "tests" / "_idle_for_curve.py"
    src.write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "class Idle(Strategy):\n"
        "    def next(self) -> None: pass\n",
        encoding="utf-8",
    )
    try:
        r = run([src], df, cash=100000.0, fee=0.0)[0]
    finally:
        src.unlink()

    assert r.trades == []
    eqs = {p.equity for p in r.equity}
    assert eqs == {100000.0}, f"零交易的净值不是水平线:{sorted(eqs)[:5]}"
    # 而价格确实在动 —— 证明上面那条不是因为数据本身平
    closes = {p.close for p in r.equity}
    assert len(closes) > 100, "前提:价格序列必须真的在变化"
