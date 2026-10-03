"""T3 的验收门:账本原语与不变式(ADR-003/007/009/010/035/043,I1/I2)。

T3 的边界(DESIGN.md §5 T3 门的前言,逐字):

    「T3 实现 `Account.apply(fill)` 等账本原语;`Fill` 由**调用方构造**
      (成交价由 T4 决定)。故本门不含任何「成交价从哪来」的断言。」

因此本文件里**没有** bar 列表、没有 `Open`/`Close` 的选取、没有逐日的
`equity[]` 序列、没有 floor 取整、没有「交易日」概念 —— 那些分别属于
T4(ADR-012/014/016、I7)与 T5(I3 逐日、I6)。本门只逐笔。

影子账本的隔离要求(T3 门逐字,这是本文件最重要的结构约束):
  * 影子账本的算术**全部写在本文件内**;
  * `rate` **字面写死为 0.001**(非零 —— rate=0 时手续费方向错误、漏算、
    双算三种缺陷全部不可观测);
  * **不得读 `fill.fee`** 来算影子值(那会让一个错误的 rate 在被测侧与
    期望侧同时出现,从而对消);
  * **不得调用 `Account` 的任何方法**来算影子值,**不得 import 被测模块的
    任何记账辅助函数**(含模块级私有函数)。

对应地,本文件从 `nullhypothesis.account` **只** import `Account` 这一个
名字 —— 它是被测对象本身。`BUY`/`SELL`/`EPSILON`/`_as_int_shares` 都不
import:side 的两个字面量在本文件里独立写死(它们的真相来源是
`contracts.yaml` 的 `trade_sides`,不是被测模块),容差也独立写死。

blindSpots(T3 门要求声明)—— 下列**未被本门测试**:
  * **浮点累积误差的长程行为**:本门最长的逐笔序列是 10 笔。数千笔成交后
    `cash` 的二进制浮点漂移是否仍在 0.01 容差内,**未测**。ADR 未给出
    精度契约(`rounding.decimals` 只约束**展示**,不约束账本内部表示),
    故本任务不替设计决定是否该用 `Decimal`。
  * **并发**:`Account` 无锁、非线程安全。ADR-028 的 `RUN_LOCK` 把阶段二
    的运行串行化,故单账本不会被并发触及;但该保证在阶段二的 `api.py`,
    不在本层,本门也不断言它。
  * **极值**:`price`/`cash` 取 `inf` / `nan` / 超大数时的行为未测。
    `nan` 会让所有比较为假,从而**绕过 I1/I2 的校验**——真实路径上
    ADR-019 已在载入期拒掉 NaN 价格,但账本自己不防。无 ADR 给出契约。
  * **ADR-007 的费率公式本身**(`fee = shares * price * rate`)在**生产侧**
    由谁计算:本门只验证账本按 ADR-043 的**方向**加减一个**已给定**的
    `fee`。「fee 是不是用对了 rate 算出来的」属成交层(T4),本门够不到。
  * `equity_at` 对**非常大的** `shares * close` 乘积的精度,未测。
"""

from __future__ import annotations

import pytest

from nullhypothesis.account import Account

# ═══════════════════════════ 本文件自有的常量 ═══════════════════════════
#
# 全部字面写死在测试内,**不** import 被测模块的同名常量 —— 否则被测模块
# 把 BUY 拼成 "BUI" 时两侧会一起错,门就瞎了。
# 真相来源:`contracts.yaml` 的 `trade_sides: ["BUY", "SELL"]`(ADR-037)。

BUY = "BUY"
SELL = "SELL"

#: T3 门的硬要求:**必须用非零费率**,字面写死。
RATE = 0.001

#: 对账容差,单位「分」(T3 门:容差 0.01)。
TOL = 0.01


class Fill:
    """**调用方构造的** `Fill`(ADR-037 的五个字段,恰好五个)。

    为什么门自己定义它:ADR-035 把 `Fill` 值对象钉在 `nullhypothesis/engine.py`
    (T4/T5 的产出物,§4.5 归属矩阵),该文件在 T3 阶段**尚不存在**。T3 门的
    前言本就规定 `Fill` 由「调用方构造」,而在本门里调用方就是测试自己。
    字段名与 ADR-037 逐字一致(`date` / `side` / `shares` / `price` / `fee`),
    故 T4 换成 `engine.Fill` 后 `Account.apply` 无需改动。

    `cash_after` / `shares_after` **不是** `Fill` 的字段(ADR-037/044)——
    它们是聚合根的 `trades_ledger`,这里不放。
    """

    __slots__ = ("date", "side", "shares", "price", "fee")

    def __init__(self, date: str, side: str, shares: int, price: float, fee: float) -> None:
        self.date = date
        self.side = side
        self.shares = shares
        self.price = price
        self.fee = fee


class ShadowLedger:
    """影子账本 —— **算术全部在本文件内**,与被测代码零共享。

    它不是 `Account` 的副本:它只会按 ADR-043 的两条公式加减,连 I1/I2 都
    不检查(检查不变式是被测对象的职责,影子只负责「正确的数该是多少」)。

    `fee` 由影子**自己**用字面写死的 `RATE` 算(`ADR-007`),**绝不**读
    `fill.fee` —— 这是 T3 门点名的那条隔离要求。
    """

    def __init__(self, cash: float, shares: int = 0) -> None:
        self.cash = float(cash)
        self.shares = int(shares)
        # 闭合性断言用的独立累计量(全部由影子自算)
        self.total_fee = 0.0
        self.total_buy_gross = 0.0
        self.total_sell_gross = 0.0

    def fee_for(self, shares: int, price: float) -> float:
        """ADR-007 的手续费公式,在测试内独立实现:`shares * price * rate`。"""
        return shares * price * RATE

    def buy(self, shares: int, price: float) -> float:
        """ADR-043 买入:`cash -= shares * price + fee`。返回影子自算的 fee。"""
        fee = self.fee_for(shares, price)
        self.cash = self.cash - (shares * price + fee)
        self.shares = self.shares + shares
        self.total_fee += fee
        self.total_buy_gross += shares * price
        return fee

    def sell(self, shares: int, price: float) -> float:
        """ADR-043 卖出:`cash += shares * price - fee`。返回影子自算的 fee。"""
        fee = self.fee_for(shares, price)
        self.cash = self.cash + (shares * price - fee)
        self.shares = self.shares - shares
        self.total_fee += fee
        self.total_sell_gross += shares * price
        return fee


def make_fill(date: str, side: str, shares: int, price: float) -> Fill:
    """造一个 `Fill`,其 `fee` 用**测试侧**的 ADR-007 公式算出。

    注意方向:这里是「测试把自己算的 fee **喂给**被测代码」,而影子对账时
    再独立算一次同一个数。被测代码**从不**参与 fee 的计算,故一个错误的
    rate 不可能在两侧同时出现。
    """
    return Fill(date=date, side=side, shares=shares, price=price, fee=shares * price * RATE)


# ═══════════════════════ ADR-035:钉死的公开符号 ═══════════════════════


def test_adr035_pinned_names_exist() -> None:
    """ADR-035 把四个名字钉死;改名会让 T5 的 I3 门读不到账本状态。"""
    acct = Account(cash=10_000.0)
    assert hasattr(acct, "cash")
    assert hasattr(acct, "shares")
    assert callable(acct.apply)
    assert callable(acct.equity_at)


def test_cash_and_shares_are_read_only() -> None:
    """ADR-035 写明「只读属性」:可写就等于给 I1/I2 开了一条绕过校验的旁路。"""
    acct = Account(cash=10_000.0)
    with pytest.raises(AttributeError):
        acct.cash = 1.0            # type: ignore[misc]
    with pytest.raises(AttributeError):
        acct.shares = 1            # type: ignore[misc]
    assert acct.cash == 10_000.0
    assert acct.shares == 0


def test_initial_state() -> None:
    """开账即满足 I1/I2,且 `shares` 从 0 开始。"""
    acct = Account(cash=10_000.0)
    assert acct.cash == 10_000.0
    assert acct.shares == 0


# ═══════════════ 关键门:影子账本逐笔对账(非逐日,非同义反复) ═══════════════

#: 一串**调用方构造的** Fill。买卖交错,价格互异(量级可区分),使任何
#: 「方向错」「漏算 fee」「双算 fee」「把 shares 加反」都必然在某一笔暴露。
#: 日期只是 ADR-037 的字段,账本不读它 —— 这里给真实日期以证明账本确实不读。
LEDGER_SEQUENCE: list[tuple[str, str, int, float]] = [
    ("2020-01-02", BUY, 10, 100.0),    # 买入
    ("2020-01-03", BUY, 5, 120.0),     # 加仓,价不同
    ("2020-01-06", SELL, 3, 150.0),    # 部分减仓
    ("2020-01-07", BUY, 7, 90.0),      # 再加仓
    ("2020-01-08", SELL, 19, 200.0),   # 清仓(19 == 10+5-3+7)
    ("2020-01-09", BUY, 2, 300.0),     # 空仓后重新建仓
    ("2020-01-10", SELL, 1, 310.0),
    ("2020-01-13", SELL, 1, 50.0),     # 再次清仓,且价格远低于成本
    ("2020-01-14", BUY, 4, 75.0),
    ("2020-01-15", SELL, 4, 80.0),
]


def test_shadow_ledger_matches_account_fill_by_fill() -> None:
    """**关键门**:逐笔 apply 后,影子侧的 cash/shares 与账本逐笔相等。

    影子侧的 `fee` 由本文件的 `ShadowLedger.fee_for` 自算(rate 字面写死),
    **不读 `fill.fee`**,**不调用 `Account` 的任何方法**。
    额外逐笔断言 `fill.fee` 与影子自算的 fee 相等(T3 门点名的那条)。
    """
    initial = 10_000.0
    acct = Account(cash=initial)
    shadow = ShadowLedger(cash=initial)

    for i, (date, side, shares, price) in enumerate(LEDGER_SEQUENCE):
        fill = make_fill(date, side, shares, price)

        # 影子侧:全部算术在本文件内
        if side == BUY:
            shadow_fee = shadow.buy(shares, price)
        else:
            shadow_fee = shadow.sell(shares, price)

        # 被测侧
        acct.apply(fill)

        # T3 门:每笔 apply 后断言两侧相等(容差 0.01)
        assert acct.cash == pytest.approx(shadow.cash, abs=TOL), (
            f"第 {i} 笔({side} {shares}@{price})后 cash 不符:"
            f"账本={acct.cash}, 影子={shadow.cash}"
        )
        assert acct.shares == shadow.shares, (
            f"第 {i} 笔({side} {shares}@{price})后 shares 不符:"
            f"账本={acct.shares}, 影子={shadow.shares}"
        )
        # T3 门:额外断言 `fill.fee` 与影子自算的 fee 逐笔相等
        assert fill.fee == pytest.approx(shadow_fee, abs=TOL), (
            f"第 {i} 笔的 fill.fee 与影子自算的 fee 不符"
        )

        # I1 / I2 全程成立
        assert acct.cash >= -TOL, f"第 {i} 笔后违反 I1"
        assert acct.shares >= 0, f"第 {i} 笔后违反 I2"

    # 序列终点:已清仓(10+5-3+7-19+2-1-1+4-4 == 0)
    assert acct.shares == 0
    assert shadow.shares == 0


def test_fee_closure_sum_is_neither_missed_nor_double_counted() -> None:
    """闭合性:影子**自算**的 `sum(fee)` 与现金总变动、成交额总和三者闭合。

    推导(全部来自 ADR-043,在本文件内独立写出):
        终态 cash = 初始 cash - Σ买入成交额 + Σ卖出成交额 - Σfee
    =>  Σfee = 初始 cash - 终态 cash - Σ买入成交额 + Σ卖出成交额

    这钉死「手续费既未漏算也未双算」。**不用 `fill.fee` 做这个闭合** ——
    那是在同一组数上做恒等式,错误的 rate 照样通过。
    """
    initial = 10_000.0
    acct = Account(cash=initial)
    shadow = ShadowLedger(cash=initial)

    for date, side, shares, price in LEDGER_SEQUENCE:
        if side == BUY:
            shadow.buy(shares, price)
        else:
            shadow.sell(shares, price)
        acct.apply(make_fill(date, side, shares, price))

    # 被测侧的现金总变动
    account_cash_delta = initial - acct.cash

    # 影子侧独立算出的「应有的现金总变动」
    expected_delta = shadow.total_buy_gross - shadow.total_sell_gross + shadow.total_fee

    assert account_cash_delta == pytest.approx(expected_delta, abs=TOL)

    # 反向解出 fee 总和,与影子自算的 Σfee 闭合
    implied_total_fee = (
        account_cash_delta - shadow.total_buy_gross + shadow.total_sell_gross
    )
    assert implied_total_fee == pytest.approx(shadow.total_fee, abs=TOL)

    # 漏算 / 双算的两个否证值必须不等于真值,否则本门区分不出来
    assert shadow.total_fee > TOL, "rate 必须非零,否则漏算与双算不可观测"
    assert implied_total_fee != pytest.approx(0.0, abs=TOL), "漏算 fee 的实现"
    assert implied_total_fee != pytest.approx(2 * shadow.total_fee, abs=TOL), (
        "双算 fee 的实现"
    )


# ═══════════════════ ADR-043:手续费双向,各一测 ═══════════════════


def test_adr043_buy_cash_decreases_by_gross_plus_fee() -> None:
    """ADR-043 买入:`cash -= shares * price + fee`。"""
    initial = 10_000.0
    acct = Account(cash=initial)

    shares, price = 10, 100.0
    gross = shares * price                 # 1000.0,测试内独立算
    fee = shares * price * RATE            # 1.0,测试内独立算

    acct.apply(make_fill("2020-01-02", BUY, shares, price))

    assert initial - acct.cash == pytest.approx(gross + fee, abs=TOL)
    # 符号钉死:买入的现金减少必须**大于**成交额(fee 是额外流出,不是折扣)
    assert initial - acct.cash > gross
    assert initial - acct.cash != pytest.approx(gross - fee, abs=TOL), (
        "买入把 fee 算成了折扣 —— 这是 ADR-043 要防的符号错误"
    )
    assert acct.shares == shares
    assert acct.cash == pytest.approx(initial - gross - fee, abs=TOL)


def test_adr043_sell_cash_increases_by_gross_minus_fee() -> None:
    """ADR-043 卖出:`cash += shares * price - fee`。

    这一条是 ADR-043 存在的直接原因 —— 原门写「现金扣减 == 成交额 + 手续费」
    对卖出字面错误。
    """
    acct = Account(cash=10_000.0)
    acct.apply(make_fill("2020-01-02", BUY, 10, 100.0))

    cash_before = acct.cash
    shares, price = 4, 150.0
    gross = shares * price                 # 600.0
    fee = shares * price * RATE            # 0.6

    acct.apply(make_fill("2020-01-03", SELL, shares, price))

    assert acct.cash - cash_before == pytest.approx(gross - fee, abs=TOL)
    # 符号钉死:卖出的现金增加必须**小于**成交额(fee 仍是流出)
    assert acct.cash - cash_before < gross
    assert acct.cash - cash_before != pytest.approx(gross + fee, abs=TOL), (
        "卖出把 fee 算成了收入 —— 最易犯的符号错误"
    )
    assert acct.shares == 6


def test_adr043_fee_flows_out_in_both_directions() -> None:
    """同一股数、同一价格买完即卖,净损失恰为**两笔** fee(方向各一次)。

    若任一方向的 fee 符号写反,净额会变成 0(两笔对消)或 -2*fee。
    """
    initial = 10_000.0
    acct = Account(cash=initial)
    shares, price = 10, 100.0
    fee_each = shares * price * RATE       # 1.0,测试内独立算

    acct.apply(make_fill("2020-01-02", BUY, shares, price))
    acct.apply(make_fill("2020-01-03", SELL, shares, price))

    assert acct.shares == 0
    assert initial - acct.cash == pytest.approx(2 * fee_each, abs=TOL)
    # 两个否证值:符号写反时会落在这里
    assert initial - acct.cash != pytest.approx(0.0, abs=TOL)
    assert initial - acct.cash != pytest.approx(-2 * fee_each, abs=TOL)


# ═══════════════════ ADR-003:估值函数单独测 ═══════════════════


def test_equity_at_distinct_closes_against_shadow() -> None:
    """ADR-003:给一组**互异**的 Close 常量,断言 `equity_at` 等于影子重算值。

    影子侧的 `cash`/`shares` 由本文件独立累加得出(不读账本),故本断言
    不是 `x == x`:账本的 cash/shares 自身错了,这里也会红。

    *(T3 没有 bar 列表、没有「交易日」概念,故只给常量 Close,不给序列 ——
      逐日形式只属于 T5 的 I3。)*
    """
    initial = 10_000.0
    acct = Account(cash=initial)
    shadow = ShadowLedger(cash=initial)

    for date, side, shares, price in LEDGER_SEQUENCE[:4]:
        if side == BUY:
            shadow.buy(shares, price)
        else:
            shadow.sell(shares, price)
        acct.apply(make_fill(date, side, shares, price))

    assert shadow.shares > 0, "fixture 必须留有持仓,否则 shares*close 项恒为 0"

    # 互异的 Close 常量(量级可区分),使「用了错的那一个」必然失败
    closes = [1.0, 37.5, 100.0, 123.45, 999.99, 0.0]
    assert len(set(closes)) == len(closes), "Close 必须互异"

    seen = []
    for close in closes:
        expected = shadow.cash + shadow.shares * close   # 测试内独立算
        got = acct.equity_at(close)
        assert got == pytest.approx(expected, abs=TOL), f"close={close} 时估值不符"
        seen.append(got)

    # equity_at 对不同 Close 必须给出不同结果(否则它忽略了 shares 项)
    assert len(set(seen)) == len(closes), "equity_at 似乎没用上 close"


def test_equity_at_is_pure_and_does_not_mutate_state() -> None:
    """`equity_at` 是纯函数:ADR-003 的估值不是一笔交易,不得改账本。"""
    acct = Account(cash=10_000.0)
    acct.apply(make_fill("2020-01-02", BUY, 10, 100.0))
    cash_before, shares_before = acct.cash, acct.shares

    for close in (50.0, 100.0, 250.0):
        acct.equity_at(close)

    assert acct.cash == cash_before
    assert acct.shares == shares_before


def test_equity_at_with_no_position_equals_cash() -> None:
    """空仓时 `equity == cash`,与任何 Close 无关(ADR-036 的 `init()` 口径同源)。"""
    acct = Account(cash=10_000.0)
    for close in (1.0, 100.0, 10_000.0):
        assert acct.equity_at(close) == pytest.approx(10_000.0, abs=TOL)


# ═══════════════════ I1:cash >= 0(ADR-010) ═══════════════════


def test_i1_buy_that_would_overdraw_cash_raises() -> None:
    """I1:构造使 `cash` 将变负的 `Fill` → 报错。

    影子侧独立算出这笔买入需要 10_010.0 + fee,而账本只有 10_000.0。
    """
    initial = 10_000.0
    acct = Account(cash=initial)

    shares, price = 100, 100.1          # gross = 10_010.0 > initial
    assert shares * price > initial, "fixture 必须真的超支"

    with pytest.raises(ValueError) as exc:
        acct.apply(make_fill("2020-01-02", BUY, shares, price))

    assert "I1" in str(exc.value) or "现金" in str(exc.value)
    # 验不过不改:账本必须停在原状态
    assert acct.cash == initial
    assert acct.shares == 0


def test_i1_overdraw_by_fee_alone_raises() -> None:
    """I1 的边界:成交额刚好等于全部现金,但**加上 fee 就超支** → 报错。

    这一条单独存在的理由:只校验 `gross <= cash` 的实现能通过上一条
    (那里 gross 自身就超了),却会在这里把 `cash` 变成 `-fee`。
    ADR-043 规定买入要扣 `gross + fee`,fee 不是可选项。
    """
    initial = 10_000.0
    acct = Account(cash=initial)

    shares, price = 100, 100.0
    assert shares * price == initial, "fixture:成交额必须恰好等于全部现金"
    assert shares * price * RATE > TOL, "fee 必须大于容差,否则这条门不可观测"

    with pytest.raises(ValueError):
        acct.apply(make_fill("2020-01-02", BUY, shares, price))

    assert acct.cash == initial
    assert acct.shares == 0


def test_i1_spending_exactly_all_cash_including_fee_is_allowed() -> None:
    """反向边界:算上 fee 刚好花光(`cash` 落在 0)**不得**报错。

    理由:ADR-012 的 `floor` 恰好用尽余额是**设计预期**
    (`fixtures.adr012_floor_vs_ceil` 的 `cash_after` 就是这么来的)。
    一个把容差写成「`cash` 必须 > 0」的实现会红掉正确的 T4 成交。
    """
    shares, price = 10, 100.0
    gross = shares * price
    fee = gross * RATE
    initial = gross + fee               # 1000.0 + 1.0,测试内独立算

    acct = Account(cash=initial)
    acct.apply(make_fill("2020-01-02", BUY, shares, price))

    assert acct.shares == shares
    assert acct.cash == pytest.approx(0.0, abs=TOL)
    assert acct.cash >= 0.0, "I1:夹到 0,不得留下负残差"


def test_i1_sell_fee_cannot_overdraw_a_zero_cash_account() -> None:
    """I1 在**卖出**方向:卖出净流入为正,故不该因 fee 而透支。

    这条钉死「卖出的 fee 是从流入里扣,而不是额外从 cash 里扣两次」。
    """
    shares, price = 10, 100.0
    gross = shares * price
    fee = gross * RATE
    initial = gross + fee

    acct = Account(cash=initial)
    acct.apply(make_fill("2020-01-02", BUY, shares, price))
    assert acct.cash == pytest.approx(0.0, abs=TOL), "前置:现金已花光"

    # 现金为 0 时卖出:ADR-043 的 `+= gross - fee` 为正流入,必须成功
    acct.apply(make_fill("2020-01-03", SELL, shares, price))
    assert acct.shares == 0
    assert acct.cash == pytest.approx(gross - fee, abs=TOL)


def test_i1_negative_initial_cash_raises() -> None:
    """开账即违反 I1 → 报错(不变式不能等到第一笔成交才成立)。"""
    with pytest.raises(ValueError):
        Account(cash=-1.0)


# ═══════════════════ I2 / ADR-009:shares >= 0,仅多头 ═══════════════════


def test_i2_sell_more_than_held_raises() -> None:
    """I2:使 `shares` 变负的 `Fill` → 报错(ADR-009 的「超额卖出」)。"""
    acct = Account(cash=10_000.0)
    acct.apply(make_fill("2020-01-02", BUY, 10, 100.0))
    cash_before, shares_before = acct.cash, acct.shares

    with pytest.raises(ValueError) as exc:
        acct.apply(make_fill("2020-01-03", SELL, 11, 100.0))

    msg = str(exc.value)
    assert "I2" in msg or "持仓" in msg
    # 验不过不改
    assert acct.cash == cash_before
    assert acct.shares == shares_before


def test_adr009_sell_with_no_position_raises() -> None:
    """ADR-009:**无持仓卖出** → 报错(与超额卖出各一测)。"""
    acct = Account(cash=10_000.0)
    assert acct.shares == 0

    with pytest.raises(ValueError):
        acct.apply(make_fill("2020-01-02", SELL, 1, 100.0))

    assert acct.cash == 10_000.0
    assert acct.shares == 0


def test_adr009_selling_exactly_the_whole_position_is_allowed() -> None:
    """边界的另一侧:卖光(`shares` 落在 0)合法 —— ADR-009 说 `shares ∈ [0, ∞)`。

    一个把守卫写成 `new_shares <= 0 → raise` 的实现会红掉所有清仓成交,
    而那正是买入持有策略在最后一天最自然的动作。
    """
    acct = Account(cash=10_000.0)
    acct.apply(make_fill("2020-01-02", BUY, 10, 100.0))
    acct.apply(make_fill("2020-01-03", SELL, 10, 100.0))
    assert acct.shares == 0


def test_i2_negative_initial_shares_raises() -> None:
    """开账即违反 I2 → 报错(ADR-009 不允许任何时刻为负)。"""
    with pytest.raises(ValueError):
        Account(cash=10_000.0, shares=-1)


def test_adr009_no_short_via_negative_shares_in_fill() -> None:
    """ADR-037 的 `shares` 恒为正:负 shares 的 `Fill` 不是「做空」,是非法输入。

    若账本接受 `BUY` + 负 shares,它就成了一条绕过 ADR-009 的做空通道
    (买入分支会让 `self.shares` 减少,而 I2 的检查点在卖出分支)。

    从**非零持仓**出发,理由同 `test_unknown_side_raises`:空仓时
    `SELL -10` 会让 `new_shares = 0 - (-10) = +10`,I2 不触发;而 `BUY -10`
    让 `new_shares` 变 -10,**I2 会代替 shares 校验抛错** —— 于是删掉
    `if shares <= 0:` 守卫后 BUY 那一支仍绿。持仓 50 时 `BUY -10` 得
    `new_shares = 40 >= 0`,I2 过,只有 shares 校验能拦住它。
    """
    acct = Account(cash=10_000.0, shares=50)
    for side in (BUY, SELL):
        with pytest.raises(ValueError) as excinfo:
            acct.apply(Fill("2020-01-02", side, -10, 100.0, 1.0))
        assert "shares" in str(excinfo.value), (
            f"{side} + 负 shares 的报错应指向 `shares`,实得 {str(excinfo.value)!r}"
        )
        assert acct.shares == 50
        assert acct.cash == 10_000.0


# ═══════════════════ 非法 Fill:不得被静默吞掉 ═══════════════════


def test_unknown_side_raises() -> None:
    """`side ∉ {BUY, SELL}`(`contracts.yaml` 的 `trade_sides`)→ 报错。

    静默忽略会让一个拼错的 side 变成「既不买也不卖」的无声 no-op ——
    账本不动,而 `trades.csv` 里却多一行。

    这个测试必须从**非零持仓 + 充足现金**出发,否则它是空断言:若账本
    `shares == 0`,一个未知 side 会落进 `else:  # SELL` 分支,并由 **I2
    守卫**(持仓 0 卖不出 1 股)抛出 `ValueError` —— 于是把 `apply` 里的
    side 校验整段删掉,测试照样绿。上一轮的门正是用这个变异体(删除 side
    校验)验出了该缺口:29 个账本测试全过。

    因此本测试双管齐下把两条旁路都堵死:
      1. 状态:`shares=50` 且现金远超一笔 1 股成交 —— 未知 side 若被当成
         SELL,持仓够卖(50 >= 1)、I2 过;若被当成 BUY,现金够付、I1 过;
         两条不变式都无法代替 side 校验抛错。
      2. 消息:断言错误消息里出现 `side`(I1 的消息讲「现金不足」、I2 的
         消息讲「卖出股数超过持仓」,都不含该词),使「碰巧抛了个
         ValueError」不足以通过。
    并在每个 bad side 后断言账本**状态未变** —— 这同时排除「抛错前已经
    把 cash/shares 改了」的脏中间态(本模块的「先算后验」契约)。
    """
    acct = Account(cash=10_000.0, shares=50)
    for bad in ("buy", "sell", "Buy", "SHORT", "", "COVER"):
        with pytest.raises(ValueError) as excinfo:
            acct.apply(Fill("2020-01-02", bad, 1, 100.0, 0.1))
        message = str(excinfo.value)
        assert "side" in message, (
            f"side={bad!r} 的报错必须指向 `side` 本身,实得 {message!r} —— "
            f"若它来自 I1/I2 守卫,说明该测试没有真正行使 side 校验"
        )
        # 非法成交整笔不发生(先算后验):状态必须与循环开始时逐字相同。
        assert acct.cash == 10_000.0
        assert acct.shares == 50

    # 同一账本随后的**合法**成交仍精确记账 —— 被拒的那几笔没留下任何残留。
    acct.apply(make_fill("2020-01-05", SELL, 10, 100.0))
    assert acct.shares == 40
    assert acct.cash == pytest.approx(10_000.0 + 1_000.0 - 1_000.0 * RATE, abs=TOL)


def test_unknown_side_is_not_rescued_by_invariant_guards() -> None:
    """未知 side 在**四种**不变式组合下都必须由 side 校验拦住。

    `test_unknown_side_raises` 固定了一个「两条不变式都过」的状态。本测试
    补上其余三种:side 校验必须在 I1/I2 **之前**生效,否则一个未知 side 的
    报错原因会随账本状态漂移 —— 空仓时报「持仓不足」、缺钱时报「现金不足」
    —— 调用方(T4)拿到的诊断就指错了方向,而真正的病因是一个拼错的常量。

    注意这里的断言是**消息内容**,不只是「抛了 ValueError」:三种状态下
    I1/I2 本来就会抛错,只断言异常类型的话本测试同样是空断言。
    """
    bad = "SHORT"
    states = [
        # (cash, shares, 说明)
        (10_000.0, 50, "现金足、持仓足:I1/I2 都不会抛"),
        (10_000.0, 0, "现金足、空仓:若落进 SELL 分支,I2 会抛"),
        (0.0, 50, "无现金、持仓足:若落进 BUY 分支,I1 会抛"),
        (0.0, 0, "无现金、空仓:I1/I2 任一都可能抛"),
    ]
    for cash, shares, why in states:
        acct = Account(cash=cash, shares=shares)
        with pytest.raises(ValueError) as excinfo:
            acct.apply(Fill("2020-01-02", bad, 1, 100.0, 0.1))
        message = str(excinfo.value)
        assert "side" in message, (
            f"{why}:未知 side 的报错应指向 `side`,实得 {message!r}"
        )
        assert acct.cash == cash
        assert acct.shares == shares


def test_zero_share_fill_raises() -> None:
    """0 股成交 → 报错。

    ADR-008/012:差额为 0 时**在成交层**就该静默跳过、不生成订单。0 股的
    `Fill` 到了账本只能说明上游漏了那个 early return;静默接受会让
    ADR-042 的「交易次数 == len(fills)」偏大,并往 `trades.csv` 写一条空行。
    """
    acct = Account(cash=10_000.0)
    with pytest.raises(ValueError):
        acct.apply(Fill("2020-01-02", BUY, 0, 100.0, 0.0))


def test_fractional_shares_raise_not_silently_floored() -> None:
    """小数股 → 报错(ADR-012:「小数股不支持」)。

    关键是**不得静默取整**:账本里的 `int(3.7)` 等于在账本层偷做 ADR-012
    的 floor,而取整归 T4(§4 变更说明)。账本替上游决定,就等于把一个
    取整缺陷藏进了正确的那一侧。
    """
    acct = Account(cash=10_000.0)
    with pytest.raises(ValueError):
        acct.apply(Fill("2020-01-02", BUY, 3.7, 100.0, 0.37))  # type: ignore[arg-type]
    assert acct.shares == 0
    # 整数值的 float(如 3.0)是同一个数,允许
    acct.apply(Fill("2020-01-02", BUY, 3.0, 100.0, 0.3))       # type: ignore[arg-type]
    assert acct.shares == 3


def test_negative_fee_raises() -> None:
    """负 fee → 报错(§1 的 `FeeRate` 值对象:`rate >= 0`,ADR-039/043)。

    负 fee 是「交易所倒给你钱」,它会让 ADR-043 的两条公式都失去符号意义。
    """
    acct = Account(cash=10_000.0)
    with pytest.raises(ValueError):
        acct.apply(Fill("2020-01-02", BUY, 1, 100.0, -0.1))


def test_negative_price_raises() -> None:
    """负 price → 报错(ADR-019 已保证载入后价格 > 0,故负价只能是算错的)。"""
    acct = Account(cash=10_000.0)
    with pytest.raises(ValueError):
        acct.apply(Fill("2020-01-02", BUY, 1, -100.0, 0.0))


def test_non_numeric_shares_raise() -> None:
    """`shares` 不是数 → 报错(不是 `TypeError`,也不是静默转换)。

    `"10"` 最危险:`int("10")` 会成功,于是一个把股数当字符串传的上游
    (CSV 直读、JSON 反序列化)会被静默接纳,而 `shares * price` 在
    `str * float` 上才炸,错误出现在离病因很远的地方。
    `None` / `list` 则会让 `self.shares` 变成非 int,污染 I2 的比较。
    """
    for bad in ("10", None, [10], 10 + 0j):
        acct = Account(cash=10_000.0, shares=50)
        with pytest.raises(ValueError) as excinfo:
            acct.apply(Fill("2020-01-02", BUY, bad, 100.0, 0.1))  # type: ignore[arg-type]
        assert "shares" in str(excinfo.value)
        assert acct.shares == 50
        assert acct.cash == 10_000.0


def test_bool_shares_raise_not_treated_as_one() -> None:
    """`shares=True` → 报错,而不是「1 股」。

    `bool` 是 `int` 的子类,所以一个幼稚的 `isinstance(value, int)` 会让
    `True` 悄悄变成 1 股成交。股数来自上游的取整计算,一个布尔值到这里
    只能是逻辑错误(例如把「要不要买」误传成「买多少」)。
    """
    acct = Account(cash=10_000.0, shares=50)
    with pytest.raises(ValueError):
        acct.apply(Fill("2020-01-02", BUY, True, 100.0, 0.1))  # type: ignore[arg-type]
    assert acct.shares == 50, "True 不得被当成 1 股"
    assert acct.cash == 10_000.0


def test_i1_tolerance_accepts_float_residue_but_not_real_overdraft() -> None:
    """I1 的容差只覆盖**浮点残差**,不覆盖真实透支(上一轮记录在案的缺口)。

    两侧都必须测,否则容差的宽度不可观测:
      * 容差**太紧**(严格 `new_cash < 0`)会把一笔「正好花光现金」的合法
        成交(ADR-012 的 `floor` 恰好用尽余额是设计预期)报成透支;
      * 容差**太松**(例如放到 1.0)会让真实的一块钱透支静默通过,ADR-010
        的「v0 无杠杆、全额付款」就失守了。
    这里用 `cash` 与成交额的差额直接构造两侧的值,不 import 被测模块的
    `EPSILON`(隔离要求:容差在本文件内字面写死为 TOL=0.01)。
    """
    # ── 负侧残差(-0.005,在 0.01 容差内)→ 接受,且 cash 夹到 0 ──────
    acct = Account(cash=1_000.0)
    # 成交额 + fee = 1000.005 → new_cash = -0.005
    acct.apply(Fill("2020-01-02", BUY, 10, 100.0, 0.005))
    assert acct.shares == 10
    assert acct.cash == pytest.approx(0.0, abs=TOL)
    assert acct.cash >= 0.0, "I1:容差内的负残差必须夹到 0,不得留下负现金"

    # ── 真实透支(-0.02,超出容差)→ 报错 ────────────────────────────
    acct2 = Account(cash=1_000.0)
    with pytest.raises(ValueError) as excinfo:
        acct2.apply(Fill("2020-01-02", BUY, 10, 100.0, 0.02))
    assert "I1" in str(excinfo.value)
    assert acct2.cash == 1_000.0, "被拒的成交不得改动现金"
    assert acct2.shares == 0


def test_rejected_fill_leaves_ledger_exact_for_the_next_one() -> None:
    """被拒的成交之后,**后续一笔仍精确记账**(上一轮记录在案的缺口)。

    「先算后验、验不过不改」只有配上这条才完整:只断言被拒那一笔的状态
    不变,仍可能漏掉「抛错前改了某个累计量」这类残留。这里让一笔超支的
    买入被拒,再做一笔**合法**买入,并用影子账本独立算出期望值。
    """
    acct = Account(cash=2_000.0)
    shadow = ShadowLedger(cash=2_000.0)

    # 1) 超支买入被拒(100 股 @100 = 10000 > 2000)。影子**不**记这一笔。
    with pytest.raises(ValueError):
        acct.apply(make_fill("2020-01-02", BUY, 100, 100.0))
    assert acct.cash == 2_000.0
    assert acct.shares == 0

    # 2) 合法买入必须精确 —— 不多扣、不少扣被拒那一笔的任何部分。
    shadow.buy(10, 100.0)
    acct.apply(make_fill("2020-01-03", BUY, 10, 100.0))
    assert acct.shares == shadow.shares == 10
    assert acct.cash == pytest.approx(shadow.cash, abs=TOL)
    # 否证值:若被拒那笔的 gross 或 fee 漏进了账本,cash 会偏离这个数。
    assert acct.cash == pytest.approx(2_000.0 - 1_000.0 - 1.0, abs=TOL)


def test_negative_close_in_equity_at_raises() -> None:
    """负 Close 估值 → 报错,而不是静默产出一条负净值曲线。"""
    acct = Account(cash=10_000.0)
    with pytest.raises(ValueError):
        acct.equity_at(-1.0)


# ═══════════════════ 结构:账本不碰 T4 的职责 ═══════════════════


def test_account_does_not_read_fill_date() -> None:
    """账本**没有「交易日」概念**(T3 门的括注):`date` 缺失也该能记账。

    这把「账本按日期做了什么」这一类越界实现钉死:account.py 若去解析
    `fill.date`(排序、算间隔、建索引),它就侵入了 ADR-042 与 T5 的职责。
    """

    class DatelessFill:
        """只有 ADR-043 记账所需的四个字段,**没有** `date`。"""

        side = BUY
        shares = 5
        price = 100.0
        fee = 5 * 100.0 * RATE

    acct = Account(cash=10_000.0)
    acct.apply(DatelessFill())        # type: ignore[arg-type]
    assert acct.shares == 5
    assert acct.cash == pytest.approx(10_000.0 - 500.0 - 0.5, abs=TOL)


def test_account_exposes_no_pricing_or_sizing_api() -> None:
    """T3 的边界:账本不含任何「成交价从哪来」「该买多少股」的逻辑。

    ADR-012(floor)/014(target 的分母)/016(T+1 开盘算股数)全归 T4;
    §4 的变更说明把它们从 T3 移走的理由是「T3 只依赖 T1、无法产生成交价」。
    若这些名字出现在 `Account` 上,职责就又漂回来了。
    """
    acct = Account(cash=10_000.0)
    for forbidden in (
        "target", "order", "resolve", "resolve_queue", "enqueue", "queue",
        "fee_rate", "rate", "compute_shares", "target_shares", "buy", "sell",
        "data", "bars", "equity",   # `equity` 的逐日序列归 T5;本层只有 equity_at
    ):
        assert not hasattr(acct, forbidden), (
            f"`Account` 不该有 `{forbidden}` —— 那是 T4/T5 的职责"
        )
