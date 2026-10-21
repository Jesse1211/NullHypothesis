"""T22 验收门 —— 滑点(ADR-056)+ I7 重写 + 可负担性修正(ADR-057)。

两条 ADR,两种性质的门:

* **ADR-056** 是**语义新增**:成交价从 `Open` 变成 `Open * (1 + dir * s)`。
  它的门全是**精确等式**(G1–G4、G9),因为 ADR-056 逐字要求「仍是精确等式,
  这是关键」—— 四个候选价 `50 / 100 / 400 / 200.1` 互异,故任一种错位必然失败。
* **ADR-057** 是**阻断级缺陷修正**:递减循环的单位成本漏掉 `s` 会让满仓
  (`weight = 1.0`)破 I1。它的门是**三值可分辨**(`998 / 999 / 1000`,
  `fixtures.adr057_slippage_affordability`)+ 一条真实栈门,手法与
  `adr014a_affordability` 当初钉 `3689` vs `3694` 完全一致。

**期望值全部取自 `contracts.yaml`,本文件不复述任何字面量**(J2 裁决:
「下列各门写出的数值是设计期的推导记录,构建期必须把它们钉进
`contracts.yaml`,测试代码只引用 key、不复述字面量」)。唯一的例外是
**结构性**常量(K 线日期字符串、`weight=0.5` 这类非期望值的输入档位),
它们不是任何门的期望输出。

脚手架复用 T4 的门(`Bar` / `drive` / `make_strategy`)—— 那三个是测试侧
的最小驱动器,ADR-002 的「出队先于入队」两行写在 `drive()` 里。重造一份会
让两个文件的 T+1 语义各自演化。

blindSpots(T22 门要求声明 —— 固定 bps 滑点的全部诚实边界,ADR-059):

  * **规模无关**:1 股与 10 倍日成交量收取同一比例成本。真实冲击约正比于
    `sqrt(订单量 / 日成交量)`,故本模型对极小单偏高、对大单**严重偏低**。
    框架**不读** `Volume`(`BarLike` 只声明 `Open` 与 `Date`),结构上无从
    知晓规模 —— 这是边界,不是待办,本门**无法**覆盖。
  * **价差与冲击被合并**成一个常数,二者不再可分;本门断言的只是那个常数
    被正确地施加,不涉及它是否是对的常数。
  * **跳空日双重低估**:成交发生在开盘,而开盘有效价差系统性宽于盘中;
    ADR-020 又要求跳空无条件成交,于是跳空日的真实成本被双重低估。本门
    用的 `s` 对所有 K 线相同,**不检验**这一点(框架无从检验)。
  * **一个 `s` 只服务一个标的** —— 大盘股与小盘股的价差相差一个数量级
    以上。CLI 只有一个 `--slippage`,本门不覆盖多标的。
  * **加了滑点的净值曲线依然偏乐观**:无永久冲击(隐含「策略容量无限」),
    暴跌开盘的集中卖出不受额外惩罚。`slippage = 0`(默认)的曲线**不代表
    可实现收益** —— 这条由 ADR-059 的声明(T24)负责输出到界面,本门只
    断言参数**被显示**(G13 的报告头),不断言文案。
  * **I1 的「不超买」仍是采样的,不是有界证明**:G10 与下面的 sweep 覆盖
    有限档位的 `(s, rate, weight, price)`,而三者都是连续区间。ADR-057 的
    修法正确性由「单位成本含全部单位级成本」这条**不变规则**承载,
    而规则本身无运行时守卫 —— 下一个加进来的单位成本若漏掉,本门不会红。
  * **本门不覆盖 `s` 对夏普/回撤的交互**(ADR-059 记明「夏普随 `s` 下降
    比总收益更陡」)—— 那两个指标由 T23 引入,届时由 T23 的门负责。
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import backtest
from nullhypothesis.engine import Backtest, ShareOrder, TargetOrder
from nullhypothesis.run import RunRequest, run

from test_engine_queue import Bar, drive, make_strategy

ROOT = Path(__file__).resolve().parent.parent
C = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))
FIX = C["fixtures"]
S = FIX["adr057_slippage_affordability"]
I7 = FIX["i7_four_distinct_prices"]
EXIT = C["exit_codes"]
BUY_AND_HOLD = ROOT / "strategies" / "buy_and_hold.py"
MA_CROSS = ROOT / "strategies" / "ma_cross.py"
SYNTHETIC = ROOT / "data" / "synthetic.csv"


def _one_fill(bt: Backtest, order, open_price: float) -> object:
    """入队一个订单,在**次日**(T+1)开盘成交,返回那笔 `Fill`。

    两根 K 线:第 1 根只入队(队列空,无可出队),第 2 根出队成交 ——
    ADR-002 的 T+1 语义。价格只在第 2 根上给,故「用了 T 日的 Open」这种
    实现会落在一个**不同**的数上。
    """
    bt.enqueue(order)
    fill = bt.resolve_queue(Bar(Date="2020-01-03", Open=open_price))
    assert fill is not None, "订单未成交 —— 差额不该为 0"
    return fill


# ═══════════════════ G1 / G2 · I7′ 的买卖成对 ═══════════════════
#
# ADR-056 把 I7 重写为:price == Open * (1 + dir * s),dir = +1 买 / -1 卖。
# **两条门必须成对** —— 只测买入无法区分 `(1 + s)` 与 `(1 + dir * s)`。


def test_g1_buy_fill_price_includes_slippage_exactly():
    """G1:买入成交价 == `Open * (1 + s)`,**精确相等**。"""
    bt = Backtest(cash=S["cash"], slippage=I7["slippage"])
    fill = _one_fill(bt, ShareOrder(shares=1), I7["bar1"]["open"])

    assert fill.price == I7["expect_fill_price_buy_slipped"], (
        "I7′(ADR-056):买入成交价必须是 Open * (1 + s),且是精确等式"
    )
    assert fill.price != I7["reject_price_no_slippage"], (
        "用了对的 K 线但**忘加滑点** —— 这正是 ADR-056 说的、重写后新增的鉴别力"
    )


def test_g2_sell_fill_price_subtracts_slippage_exactly():
    """G2:卖出成交价 == `Open * (1 - s)`。与 G1 成对,故 `dir` 不可省。

    先在 T+1 买入建仓(不带滑点:用一个**独立**的零滑点账本无法卖出,
    故这里用同一个账本买再卖,买入那笔的价格由 G1 守)。
    """
    bt = Backtest(cash=S["cash"], slippage=I7["slippage"])
    _one_fill(bt, ShareOrder(shares=1), I7["bar1"]["open"])

    sell = _one_fill(bt, ShareOrder(shares=-1), I7["bar1"]["open"])
    assert sell.price == I7["expect_fill_price_sell_slipped"], (
        "I7′ 的 dir = -1:卖出必须按 Open * (1 - s) —— 滑点天然双向惩罚"
        "(买贵、卖便宜)。若此值等于买入价,实现写的是 (1 + s) 而非 (1 + dir*s)"
    )
    assert sell.price != I7["expect_fill_price_buy_slipped"]
    assert sell.price != I7["reject_price_no_slippage"]


# ═══════════════════ G3 / G4 · 费与成交额都在滑点后价上 ═══════════════════


def test_g3_fee_is_charged_on_the_slipped_price():
    """G3:`fee = shares * price * rate`,`price` 为**滑点后**价(ADR-056)。

    与真实券商一致 —— 佣金按**成交金额**计。拒绝算在裸 `Open` 上的值。
    """
    bt = Backtest(cash=S["cash"], fee=S["g3_fee_rate"], slippage=I7["slippage"])
    fill = _one_fill(bt, ShareOrder(shares=S["g3_shares"]), S["i7_open"])

    assert fill.fee == S["expect_fee_on_slipped_price"]
    assert fill.fee != S["reject_fee_on_raw_open"], (
        "手续费算在裸 Open 上 —— ADR-056 明文要求算在滑点后的价格上"
    )


def test_g4_fill_amount_inherits_the_slipped_price():
    """G4:`Fill.amount` 继承滑点后价(ADR-046 + ADR-056)。

    否则前端显示的成交额与现金变动对不上 —— 成交额本就该是**实际**成交额。
    """
    bt = Backtest(cash=S["cash"], fee=S["g3_fee_rate"], slippage=I7["slippage"])
    before = bt.cash
    fill = _one_fill(bt, ShareOrder(shares=S["g3_shares"]), S["i7_open"])

    assert fill.amount == S["expect_amount_slipped"]
    # 现金变动 == amount + fee(ADR-043 的买入方向)。这条把 amount 与账本
    # 绑在一起:一个只改了 price 没改 amount 的实现在这里红。
    assert before - bt.cash == pytest.approx(fill.amount + fill.fee, abs=1e-9)
    assert fill.amount == fill.shares * fill.price


# ═══════════════════ G5 · s = 0 向后兼容 ═══════════════════


def test_g5_zero_slippage_is_pointwise_identical_to_the_old_i7():
    """G5:`s = 0` → `price == Open`,且 7 个钉死 fixture 逐字不变。

    这是 ADR-057「修法正确的关键证据」:`1847 / 3689 / 50 / 33 / 0 / 80 / 50`
    全部不变,且 `reject_divisor_change_half` 这条**反向门仍然有效**
    —— 修正没碰除数。
    """
    bt = Backtest(cash=S["cash"], slippage=S["default_slippage"])
    fill = _one_fill(bt, ShareOrder(shares=1), I7["bar1"]["open"])
    assert fill.price == I7["expect_fill_price"], "s = 0 必须逐点退化为原 I7"

    a = FIX["adr014a_affordability"]
    # 满仓 + 非零费率(ADR-014a 的两个钉死值),s = 0 下必须一字不变。
    for weight, expect in (
        (a["weight_full"], a["expect_shares_full"]),
        (a["weight_half"], a["expect_shares_half"]),
    ):
        bt = Backtest(cash=a["cash"], fee=a["fee_rate"], slippage=0.0)
        bt.enqueue(TargetOrder(weight=weight))
        got = bt.resolve_queue(Bar(Date="2020-01-03", Open=a["open"]))
        assert got is not None and got.shares == expect, (
            f"s = 0 时 ADR-014a 的钉死值被改动了:weight={weight}"
        )
    # 反向门:改除数的错误实现会给 reject_divisor_change_half。
    assert a["expect_shares_half"] != a["reject_divisor_change_half"]
    bt = Backtest(cash=a["cash"], fee=a["fee_rate"], slippage=0.0)
    bt.enqueue(TargetOrder(weight=a["weight_half"]))
    got = bt.resolve_queue(Bar(Date="2020-01-03", Open=a["open"]))
    assert got.shares != a["reject_divisor_change_half"], (
        "除数被改成含成本的价格 —— ADR-014/014a/057 三次逐字禁止"
    )

    # 其余四个:floor 两组 + 分母三向的终值。
    f = FIX["adr016_fill_at_next_open"]
    bt = Backtest(cash=f["cash"], slippage=0.0)
    bt.enqueue(TargetOrder(weight=f["weight"]))
    assert bt.resolve_queue(
        Bar(Date="2020-01-03", Open=f["bar_t1_open"])
    ).shares == f["expect_shares"]

    f = FIX["adr012_floor_vs_ceil"]
    bt = Backtest(cash=f["cash"], slippage=0.0)
    bt.enqueue(TargetOrder(weight=f["weight"]))
    assert bt.resolve_queue(
        Bar(Date="2020-01-03", Open=f["open"])
    ).shares == f["expect_shares"]

    f = FIX["adr012_floor_vs_round"]
    bt = Backtest(cash=f["cash"], slippage=0.0)
    bt.enqueue(TargetOrder(weight=f["weight"]))
    # 0 股 → ADR-008 无声跳过(expect_shares == 0)。
    assert bt.resolve_queue(Bar(Date="2020-01-03", Open=f["open"])) is None

    f = FIX["adr014_denominator_three_way"]
    bt = Backtest(cash=f["cash"], fee=f["fee_rate"], slippage=0.0)
    bt.enqueue(TargetOrder(weight=f["weight_1"]))
    bt.resolve_queue(Bar(Date="2020-01-03", Open=f["open_1"]))
    assert bt.shares == f["after_leg1_shares"]
    bt.enqueue(TargetOrder(weight=f["weight_2"]))
    bt.resolve_queue(Bar(Date="2020-01-06", Open=f["open_2"]))
    assert bt.shares == f["expect_final_shares"]


# ═══════════════════ G6 / G7 / G8 · ADR-057 的回归门 ═══════════════════
#
# 三值 998 / 999 / 1000 互异:1000 = 未修正(裸 Open 作单位成本)、
# 999 = 只含 s、998 = 同时含 s 与 rate。


def test_fixture_three_values_are_mutually_distinct():
    """门的鉴别力前提:三个候选值必须互异,否则上面三条门抓不到任何东西。"""
    assert len({
        S["expect_shares_slippage_only"],
        S["expect_shares_fee_and_slippage"],
        S["reject_shares_unfixed"],
    }) == 3


def test_g6_full_weight_with_slippage_does_not_break_i1():
    """G6:`cash=100000, Open=100, weight=1.0, rate=0, s=5bps` → 999 股。

    **拒绝 1000**(未修正时 `cash_after = -50.0` → `Account.apply` 抛 I1)。
    ADR-057 的阻断级缺陷正在这里:`weight = 1.0` 下 120 组参数里 87 组破 I1,
    而满仓是最常写的策略(两个内置策略都走这条路径)。
    """
    bt = Backtest(cash=S["cash"], fee=S["fee_rate_zero"], slippage=S["slippage"])
    bt.enqueue(TargetOrder(weight=S["weight_full"]))
    fill = bt.resolve_queue(Bar(Date="2020-01-03", Open=S["open"]))   # 不得抛错

    assert fill is not None
    assert fill.shares == S["expect_shares_slippage_only"]
    assert fill.shares != S["reject_shares_unfixed"], (
        "递减循环的单位成本漏了滑点 —— ADR-057 的阻断级缺陷"
    )
    assert bt.cash == pytest.approx(S["expect_cash_after_slippage_only"], abs=1e-9)
    assert bt.cash >= 0.0, "I1(ADR-010)"


def test_g7_full_weight_with_both_fee_and_slippage():
    """G7:`rate` 与 `s` **同时**存在 → 998 股(三值互异的第三个)。

    这条门抓的是「只加了 `s`、把 `rate` 丢了」或「只乘了一个」的实现 ——
    ADR-057 的不变规则是单位成本含**全部**单位级成本。
    """
    bt = Backtest(cash=S["cash"], fee=S["fee_rate"], slippage=S["slippage"])
    bt.enqueue(TargetOrder(weight=S["weight_full"]))
    fill = bt.resolve_queue(Bar(Date="2020-01-03", Open=S["open"]))

    assert fill is not None
    assert fill.shares == S["expect_shares_fee_and_slippage"]
    assert fill.shares != S["expect_shares_slippage_only"]
    assert fill.shares != S["reject_shares_unfixed"]
    assert bt.cash >= 0.0, "I1(ADR-010)"


def test_g8_adr014a_fixture_plus_slippage_stays_solvent():
    """G8:ADR-014a 原夹具(`Open=27.07, rate=0.0013`)+ 滑点 → 不抛错、现金非负。

    只断言不等式:ADR-057 没有为这一组钉死股数,而用被测算术去算期望值
    是同义反复(§5 T4 的前言逐字)。
    """
    bt = Backtest(cash=S["cash"], fee=S["fee_rate"], slippage=S["slippage"])
    bt.enqueue(TargetOrder(weight=S["weight_full"]))
    fill = bt.resolve_queue(Bar(Date="2020-01-03", Open=S["adr014a_open"]))

    assert fill is not None
    assert bt.cash >= 0.0, "I1(ADR-010):满仓 + 费率 + 滑点三者同时仍不得透支"


# ═══════════════════ G9 · 往返总成本 == 2s + 2·rate ═══════════════════


def test_g9_round_trip_cost_is_exactly_two_s_plus_two_rate():
    """G9:买→卖(同 `Open`)的净损**精确等于**名义额的 `2s + 2·rate`。

    同时验证手续费复合与**卖出符号** —— 一个把卖出也按 `(1 + s)` 成交的
    实现会在这里得到一个更小的损失(甚至盈利),故本门与 G2 互为冗余守卫。
    """
    bt = Backtest(cash=S["cash"], fee=S["g9_fee_rate"], slippage=S["slippage"])
    before = bt.cash

    buy = _one_fill(bt, ShareOrder(shares=S["g9_shares"]), S["g9_open"])
    sell = _one_fill(bt, ShareOrder(shares=-S["g9_shares"]), S["g9_open"])

    assert buy.side != sell.side
    assert bt.shares == 0
    net_loss = before - bt.cash
    assert net_loss == pytest.approx(S["expect_roundtrip_net_loss"], abs=1e-9), (
        "往返总成本必须是 2s + 2·rate(ADR-056:滑点双向惩罚 + ADR-043:双向收费)"
    )


# ═══════════════════ G10 · 卖出永不破 I1/I2 ═══════════════════


def test_g10_selling_never_breaks_i1_or_i2_for_any_legal_s():
    """G10:任意 `s ∈ [0, 0.1)` 全平仓 → 不抛错、`shares == 0`、`cash` 单调增。

    ADR-056 逐字:「卖出侧永不触发 I1/I2 —— 滑点只减少现金流入,不会让
    `shares` 变负」。`s` 取包含两端邻域的档位(上界开区间,故取 0.0999)。
    """
    for s in (0.0, S["slippage"], 0.01, 0.05, 0.0999):
        assert 0.0 <= s < S["max_slippage_exclusive"]
        bt = Backtest(cash=S["cash"], fee=S["g9_fee_rate"], slippage=s)
        _one_fill(bt, ShareOrder(shares=S["g9_shares"]), S["g9_open"])
        held, cash_before_sell = bt.shares, bt.cash
        assert held == S["g9_shares"]

        sell = _one_fill(bt, ShareOrder(shares=-held), S["g9_open"])

        assert bt.shares == 0, "I2(ADR-009):平仓后持仓恰为 0,不得变负"
        assert bt.cash >= 0.0, "I1(ADR-010)"
        assert bt.cash > cash_before_sell, (
            f"卖出必须使现金**单调增**(滑点只减少流入,不反向):s={s}"
        )
        assert sell.price < S["g9_open"] or s == 0.0


# ═══════════════ G12 · CLI 校验与 --fee 逐字对称 ═══════════════


@pytest.mark.parametrize("bad", ["-0.1", "0.1", "nan"])
def test_g12_cli_rejects_illegal_slippage_exactly_like_fee(tmp_path, bad):
    """G12:`-0.1` / `0.1` / `nan` 报错,退出码与 `--fee` **同一个**。

    上界取 `max_slippage_exclusive`(开区间,故该值本身非法)—— 与 `--fee`
    的校验逐字对称(ADR-039 的参数表)。
    """
    out = tmp_path / "out"
    args = ["--data", str(SYNTHETIC), "--strategy", str(BUY_AND_HOLD),
            "--out", str(out)]
    r = subprocess.run(
        [sys.executable, str(ROOT / "backtest.py"), *args, "--slippage", bad],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert r.returncode == EXIT["invalid_cli_args"], r.stdout + r.stderr
    assert "slippage" in r.stderr

    # 对称性:同一个值给 --fee 必须得到**同一个**退出码。
    r_fee = subprocess.run(
        [sys.executable, str(ROOT / "backtest.py"), *args, "--fee", bad],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert r_fee.returncode == r.returncode, (
        "--slippage 与 --fee 的校验必须逐字对称(ADR-056)"
    )


def test_g12_cli_accepts_zero_and_the_upper_neighbourhood(tmp_path):
    """`0.0` 通过 —— 与 `--fee` 的默认值同一个口径(ADR-039)。"""
    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, str(ROOT / "backtest.py"),
         "--data", str(SYNTHETIC), "--strategy", str(BUY_AND_HOLD),
         "--out", str(out), "--slippage", str(S["default_slippage"])],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert r.returncode == EXIT["ok"], r.stdout + r.stderr


def test_g12_help_carries_the_magnitude_hint():
    """ADR-056 的第 1 条机制:「参数存在即是声明」—— `--help` 要带量级提示。"""
    r = subprocess.run(
        [sys.executable, str(ROOT / "backtest.py"), "--help"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert "--slippage" in r.stdout
    assert "bps" in r.stdout, "ADR-056:--help 须补量级提示(0.0005 = 5bps)"


# ═══════════════ G13 · 默认值 + 报告头显示生效值 ═══════════════


def test_g13_default_is_zero_and_curve_is_pointwise_unchanged(tmp_path):
    """G13:不传 `--slippage` → `slippage == 0.0`,且曲线与基准**逐点相同**。

    「逐点相同」用两次**真实** `run()` 的 equity 序列比对:不传 vs 显式传 0。
    """
    import argparse

    ns = backtest._parse(["--data", str(SYNTHETIC), "--strategy", str(BUY_AND_HOLD)])
    assert isinstance(ns, argparse.Namespace)
    assert ns.slippage == S["default_slippage"], "ADR-039 的默认值是 0.0"

    from nullhypothesis.data import load_csv

    df = load_csv(SYNTHETIC)
    base = run([BUY_AND_HOLD], df, cash=S["cash"])
    zero = run([BUY_AND_HOLD], df, cash=S["cash"], slippage=S["default_slippage"])
    assert [p.equity for p in base[0].equity] == [p.equity for p in zero[0].equity], (
        "s = 0 的曲线必须与 ADR-049 的买入持有基准逐点相同(向后兼容)"
    )
    assert base[0].summary == zero[0].summary


def test_g13_report_header_shows_the_effective_slippage(tmp_path):
    """ADR-056 的第 2 条机制:报告里**显示生效值**,与 `fee` 并列。

    「slippage = 0」从此是一个**被显示的选择**,而非无人提及的隐含假设。
    """
    from nullhypothesis import report as report_mod
    from nullhypothesis.data import load_csv

    df = load_csv(SYNTHETIC)
    results = run([BUY_AND_HOLD], df, cash=S["cash"], slippage=S["slippage"])
    request = RunRequest(
        strategies=[BUY_AND_HOLD.name], data_file=SYNTHETIC.name,
        cash=S["cash"], fee=S["fee_rate"], slippage=S["slippage"],
    )
    report = backtest.build_report("20200102-000000", request, results)
    text = report_mod.render(report, tmp_path)

    fmt = C["formats"]["rate"]
    assert fmt.format(S["slippage"]) in text, (
        "报告头未显示生效的滑点率 —— ADR-056 的诚实性正挂在这个值可见上"
    )
    assert fmt.format(S["fee_rate"]) in text, "与 fee 并列(T24 门 7)"
    # 四位小数是必要的:两位会把 5bps 显示成「零」。
    assert fmt.format(S["slippage"]) != "0.00"


def test_g13_run_json_and_api_carry_slippage(tmp_path):
    """`slippage` 入 `run.json` 的 `request` 与 API 的 pydantic 模型(ADR-038)。"""
    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, str(ROOT / "backtest.py"),
         "--data", str(SYNTHETIC), "--strategy", str(BUY_AND_HOLD),
         "--out", str(out), "--slippage", str(S["slippage"])],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert r.returncode == EXIT["ok"], r.stdout + r.stderr
    payload = json.loads(
        (next(out.iterdir()) / C["filenames"]["run_json"]).read_text(encoding="utf-8")
    )
    assert payload["request"]["slippage"] == S["slippage"]

    import api

    body = api.RunRequestBody(strategies=[BUY_AND_HOLD.name],
                              data_file=SYNTHETIC.name)
    assert body.slippage == S["default_slippage"], "API 的默认值同为 0.0"
    body = api.RunRequestBody(strategies=[BUY_AND_HOLD.name],
                              data_file=SYNTHETIC.name,
                              slippage=S["slippage"])
    assert body.slippage == S["slippage"]
    # 取值校验与 fee 逐字对称 —— 上界本身非法。
    bad = api.RunRequestBody(strategies=[BUY_AND_HOLD.name],
                             data_file=SYNTHETIC.name,
                             slippage=S["max_slippage_exclusive"])
    rejected = api._reject_bad_values(bad)
    assert rejected is not None and rejected.status_code == 422


# ═══════════════ G14 · 不引入 churn(两组,缺第二组抓不到) ═══════════════


def _delta_next_day(cash: float, held: int, open_price: float, s: float) -> int:
    """持仓 `held`、现金 `cash` 时,次日满仓 re-target 的差额。

    直接用 `resolve_queue` 的返回值读差额:`None` ⇒ 差额 0(ADR-008 无声跳过),
    否则 `+shares`(买)/ `-shares`(卖)。本函数**不复算**任何期望值。
    """
    # 建仓只走聚合根的**合法路径**,不触碰 `_account`(§1:`Account` 不可被
    # 聚合外引用)。以 `held * unit + cash` 作初始资金买入 `held` 股,成交后
    # 账本恰好落在 `(cash, held)` —— 下面的两条 assert 就是这件事的断言。
    unit = open_price * (1.0 + s)
    bt = Backtest(cash=held * unit + cash, slippage=s)
    first = _one_fill(bt, ShareOrder(shares=held), open_price)
    assert first.shares == held
    assert bt.cash == pytest.approx(cash, abs=1e-6)

    bt.enqueue(TargetOrder(weight=S["weight_full"]))
    fill = bt.resolve_queue(Bar(Date="2020-01-06", Open=open_price))
    if fill is None:
        return 0
    return fill.shares if fill.side == "BUY" else -fill.shares


def test_g14a_no_churn_when_next_open_moves_up_down_or_flat():
    """G14(a):持仓后次日 `Open` 升/降/平 → 差额均为 0。

    **这一组单独不够** —— 修正后 `buy_and_hold` 的常态是 `cash = 50.05`,
    那个余额**恰好掩盖** churn:实测 (a) 的三种情形在**两种**循环形式下
    都通过。故必须有 (b)。
    """
    cash = S["expect_cash_after_slippage_only"]
    held = S["expect_shares_slippage_only"]
    base = S["open"]
    for factor in (1.01, 0.99, 1.0):
        delta = _delta_next_day(cash, held, base * factor, S["slippage"])
        assert delta == S["expect_churn_delta"], (
            f"次日 Open × {factor} 出现了非零差额 —— ADR-014a 初版的"
            f"「卖 1 股来回倒腾」缺陷复现"
        )


def test_g14b_no_churn_when_cash_is_nearly_zero():
    """G14(b)⭐:`cash ≈ 0` 的持仓情形 —— **唯一**能区分两种循环形式的那组。

    ADR-057 实测:`(Open=27.07, s=0.0005, 持仓 999, cash=0)` 下被废弃的
    `target * unit_cost > investable` 形式给差额 **−1**(凭空卖出一股),
    正确形式给 **0**。两个期望值在 `contracts.yaml` 里是**可分辨的两个数**。
    """
    assert S["expect_churn_delta"] != S["reject_churn_delta"]
    delta = _delta_next_day(
        S["churn_cash"], S["churn_shares_held"], S["churn_open"], S["slippage"]
    )
    assert delta == S["expect_churn_delta"]
    assert delta != S["reject_churn_delta"], (
        "递减循环被改成对【整个仓位】计费、或守卫被改成 `target > 0` —— "
        "那是 ADR-014a 的第一版,而它是个 bug(ADR-057 的构建期铁律逐字禁止)"
    )


def test_g14_no_buy_sell_direction_alternation_over_a_held_run():
    """G14 两组共同的那条:**不得出现买→卖→买**的方向交替。

    churn 的可观测签名是**方向交替**(ADR-014a 初版「在第二根 K 线上卖出
    1 股然后来回倒腾」),不是「有没有成交」—— 剩余现金恰好还够买一股时
    **买**是正确行为(`floor` 用尽余额是 ADR-012 的设计预期)。故本门断言
    的是方向序列的**单调性**:整段里最多出现一种 `side`。

    两种现金状态都跑:`cash ≈ 0`(G14b 的那组,被废弃的循环形式在这里
    会**卖出**)与「满仓成交后的常态」(自洽地由同一个 `Open` 产生,不是
    把另一组 fixture 的现金硬塞进来 —— 那会造出一个真实回测里不存在的
    状态,并把一次**合法**的追加买入误判成 churn)。
    """
    s = S["slippage"]
    open_price = S["churn_open"]
    held = S["churn_shares_held"]
    unit = open_price * (1.0 + s)

    for cash in (S["churn_cash"], unit - 0.01):
        bt = Backtest(cash=held * unit + cash, slippage=s)
        _one_fill(bt, ShareOrder(shares=held), open_price)
        assert bt.cash == pytest.approx(cash, abs=1e-6)

        sides: list[str] = []
        for i in range(6):
            bt.enqueue(TargetOrder(weight=S["weight_full"]))
            fill = bt.resolve_queue(
                Bar(Date=f"2020-02-{i + 1:02d}", Open=open_price)
            )
            if fill is not None:
                sides.append(fill.side)

        assert len(set(sides)) <= 1, (
            f"出现了买卖方向交替({sides})—— 这是 churn 的签名"
            f"(ADR-014a 初版的「卖 1 股来回倒腾」)。cash={cash}"
        )
        # 恒定价格下满仓不得**卖出**:滑点只让买入更贵,从不产生卖出意图。
        assert "SELL" not in sides, (
            f"恒定价格下满仓 re-target 卖出了股份({sides})—— 正是 ADR-057 的"
            f"警告框记载的那个被废弃循环形式的后果。cash={cash}"
        )
        assert bt.cash >= 0.0, "I1(ADR-010)"


# ═══════════════ G15 · I7 四价互异 fixture 仍然互异 ═══════════════


def test_g15_four_candidate_prices_remain_mutually_distinct():
    """门 15:I7 的四价互异 fixture 按 ADR-056 更新后,四个候选值仍**互异**。

    `50 / 100 / 400 / 200.1` —— 鉴别力完整保留,**而且增强了**:新增的
    `reject_price_no_slippage` 能抓「用了对的 K 线但忘加滑点」。
    """
    candidates = [
        I7["bar0"]["open"], I7["bar0"]["close"], I7["bar1"]["close"],
        I7["expect_fill_price_buy_slipped"],
    ]
    assert len(set(candidates)) == len(candidates)
    assert I7["expect_fill_price_buy_slipped"] not in I7["reject_prices"]
    # 滑点后期望值与「忘加滑点」的值不同,否则新增的那条鉴别力是空的。
    assert I7["expect_fill_price_buy_slipped"] != I7["reject_price_no_slippage"]
    # 精确可表示:ADR-056 要求 I7′ 仍是**精确等式**。
    assert I7["bar1"]["open"] * (1.0 + I7["slippage"]) == (
        I7["expect_fill_price_buy_slipped"]
    )
    assert I7["bar1"]["open"] * (1.0 - I7["slippage"]) == (
        I7["expect_fill_price_sell_slipped"]
    )


# ═══════════════ 全称命题的采样:(s, rate, weight) sweep ═══════════════


def test_i1_holds_across_a_slippage_and_rate_sweep():
    """ADR-057 的缺陷是「120 组里 87 组破 I1」—— 故 sweep 比单点门更贴近它。

    断言只有两条、都不复用被测算术:**不抛错**,且成交后 `cash >= 0`。
    `weight < 1` 本来就安全(`floor` 留下的 `(1-w)·investable` 吸收滑点);
    `weight = 1.0` 是缺陷精确落点,故必须在档位里。
    """
    for s in (0.0, 0.0005, 0.001, 0.01, 0.0999):
        for rate in (0.0, 0.0013, 0.05):
            for weight in (0.0, 0.5, 0.99, 1.0):
                for cash0, price in ((100000.0, 27.07), (100000.0, 27.30),
                                     (10000.0, 100.0), (7777.0, 0.37)):
                    bars = [
                        Bar(Date="2020-01-02", Open=price, Close=price),
                        Bar(Date="2020-01-03", Open=price * 1.5, Close=price * 1.5),
                        Bar(Date="2020-01-06", Open=price * 0.7, Close=price * 0.7),
                    ]

                    def script(st, i, w=weight):
                        st.target(weight=w)

                    bt = Backtest(cash=cash0, fee=rate, slippage=s)
                    drive(bt, make_strategy(script), bars)    # 不得抛错

                    assert bt.cash >= 0.0, (
                        f"I1 被违反(ADR-010 / ADR-057):s={s} rate={rate} "
                        f"weight={weight} cash0={cash0} price={price} -> {bt.cash}"
                    )
                    assert bt.shares >= 0, "I2(ADR-009)"


def test_backtest_rejects_negative_and_nonfinite_slippage():
    """聚合根侧的校验与 `fee` 同形:负滑点是「交易送钱」的另一种写法。"""
    with pytest.raises(ValueError, match="slippage"):
        Backtest(cash=S["cash"], slippage=-S["slippage"])
    with pytest.raises(ValueError, match="slippage"):
        Backtest(cash=S["cash"], slippage=math.nan)


# ═══════════════ REAL_STACK_GATE(G11)═══════════════
#
# ADR-057 逐字:「未修正的设计必定在此崩」(shares=3663, cash=-49.90)。
# `engine.py` 的注释自证:ADR-014a 的上一个 bug 正是被 T8 的 `--fee`
# **端到端**门抓到的 —— 同一位置、同一手法。


@pytest.mark.parametrize("strategy", ["buy_and_hold", "ma_cross"])
def test_g11_real_stack_cli_with_slippage_on_synthetic_csv(tmp_path, strategy):
    """G11:真实 `data/synthetic.csv` + `--slippage 0.0005` 跑完整 CLI。

    断言 ① 跑完**不抛错**、② 首笔成交价 == `27.313650`(`Open=27.30`)。
    `ma_cross` 同样跑一遍 —— 它也走 `target(weight=1.0)`。

    第 ② 条只对 `buy_and_hold` 断言:`ma_cross` 的首笔成交日由均线决定,
    不是第 2 根 K 线,故它的首笔价格不是这个 fixture 的值。对它断言的是
    **跑完不崩 + 每一笔成交价都带滑点方向**。
    """
    if not SYNTHETIC.is_file():
        pytest.fail(
            f"真实栈门需要 {SYNTHETIC} —— 它是 REAL_STACK_GATE 的基质,"
            "不得跳过(ADR-057 的阻断级缺陷正是被这条门抓到的)"
        )
    path = ROOT / "strategies" / f"{strategy}.py"
    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, str(ROOT / "backtest.py"),
         "--data", str(SYNTHETIC), "--strategy", str(path),
         "--out", str(out), "--slippage", str(S["slippage"])],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert r.returncode == EXIT["ok"], (
        "未修正的 ADR-057 必定在此崩(I1:cash 变负)。\n"
        + r.stdout + r.stderr
    )

    archive = next(out.iterdir())
    import csv

    rows = list(
        csv.DictReader(
            (archive / C["filenames"]["trades_csv"].format(stem=strategy))
            .read_text(encoding="utf-8").splitlines()
        )
    )
    assert rows, "真实数据上必须有成交"
    assert all(float(row["cash_after"]) >= 0.0 for row in rows), "I1 贯穿全程"

    if strategy == "buy_and_hold":
        # `trades.csv` 的 price 列按 ADR-038 的两位小数**显示**(`plot.py` 的
        # `f"{fill.price:.2f}"`),故这里断言的是显示值与精确值的一致性,
        # 精确等式由下面 `test_g11_real_stack_first_fill_price_is_exact` 断言
        # (同一条真实 CSV,进程内读未取整的 `Fill.price`)。
        assert float(rows[0]["price"]) == pytest.approx(
            S["expect_real_stack_fill_price"], abs=0.005
        )
        assert int(rows[0]["shares"]) != S["reject_real_stack_shares_unfixed"], (
            "未修正的递减循环会给这个股数,随后 cash = -49.90 → 抛 I1"
        )
    else:
        # 每一笔买入价 > Open 且每一笔卖出价 < Open 的方向性在 CSV 里不可直接
        # 读(没有 Open 列),故这里断言可读的那条:成交额 ≈ shares × price。
        #
        # **容差按 `shares` 放大**:`price` 与 `amount` 两列各自按 ADR-038 的
        # 两位小数**显示**,而 `amount` 的精确值是 `shares × 未取整的 price`
        # —— 把取整后的 price 乘回 3663 股会放大半分钱的取整误差到约 18 元。
        # 精确等式(`amount == shares × price`)已由 G4 在未取整的 `Fill` 上
        # 断言;这里只能断言显示值之间的一致性,故容差是**结构性**的,
        # 不是放宽后的同一条门。
        for row in rows:
            n = int(row["shares"])
            assert float(row["amount"]) == pytest.approx(
                n * float(row["price"]), abs=0.005 * n + 0.005
            )


def test_g11_real_stack_first_fill_price_is_exact():
    """G11 第 ② 条的**精确**等式:首笔成交价 == `27.313650`(`Open=27.30`)。

    为什么不从 `trades.csv` 读:那一列按 ADR-038 的两位小数**显示**
    (`plot.py` 的 `f"{fill.price:.2f}"` → `27.31`),而 ADR-056 要求 I7′ 是
    **精确等式**。精确值只在 `Fill.price` 上存在,故本门走进程内的 `run()`
    —— 与子进程门同一条真实 `data/synthetic.csv`、同一个 `s`,只是读未取整
    的那个数。**两条门必须都有**:子进程门证明「整条 CLI 不崩」(ADR-057
    的上一个 bug 正是被端到端门抓到的),本门证明「那个价是精确的那个价」。
    """
    if not SYNTHETIC.is_file():
        pytest.fail(f"真实栈门需要 {SYNTHETIC} —— 不得跳过")

    from nullhypothesis.data import load_csv

    df = load_csv(SYNTHETIC)
    assert float(df["Open"].iloc[1]) == S["real_stack_open"], (
        "fixture 的 real_stack_open 与真实 CSV 的第 2 根 K 线不符"
    )

    result = run([BUY_AND_HOLD], df, cash=S["cash"], slippage=S["slippage"])[0]
    assert result.trades, "真实数据上必须有成交"
    first = result.trades[0]
    assert first.price == S["expect_real_stack_fill_price"], (
        "I7′ 在真实数据上必须精确成立:第 2 根 K 线的 Open × (1 + s)"
    )
    assert first.shares != S["reject_real_stack_shares_unfixed"], (
        "未修正的递减循环给这个股数,随后 cash = -49.90 → 抛 I1"
    )
    assert result.trades_ledger[0][0] >= 0.0, "I1(ADR-010)"
