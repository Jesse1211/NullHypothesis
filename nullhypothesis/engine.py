"""聚合根 `Backtest` —— 订单队列与成交(T4)。

本模块是 ADR-002「T 日 Close 决策、T+1 日 Open 成交」的**物理载体**。
T4 只拥有其中的「队列 + 成交」部分;主循环、`equity[]`/`trades[]`、
`Summary`/跳空统计是 T5 的产出物(§4.5 归属矩阵),故本文件此刻**不含**
`run()` 与 `compute_gaps()` —— 它们由 T5 在同一模块内**追加**。

契约来源(每条都逐字可查):

  * **ADR-035** —— 本模块的公开符号被钉死,构建期不得变更:

        Backtest.queue              -> list      # 订单队列,T4 的门对它采样
        Backtest.enqueue(order)     -> None      # 入队;push_count 打在这里
        Backtest.resolve_queue(bar) -> Fill|None # 出队并成交

    `resolve_queue` **必须是命名方法**:T4 的门要在「出队前」采样队列、并用
    `is` 比对「T 日入队的对象 == T+1 出队的对象」。内联 `self.queue.pop()`
    会让那条门**结构上无法执行**(§4.5 自检规则二)。`contracts.yaml` 的
    `gate_execution.monkeypatch_seams` 把 `Backtest.enqueue` 与
    `Backtest.resolve_queue` 列为打桩接缝 —— 打桩只能打到模块级/类级的命名
    属性,故两者都不得内联,也不得在 `__init__` 里绑成实例闭包。

  * **ADR-002** —— 实现上的核心规则是「出队先于入队」:

        for bar in bars:
            resolve_queue(bar)      # A 结算【昨天】的意图
            strategy.next()         # B 问【今天】的意图,入队

    「T+1 成交语义的全部实现就是 A 在 B 之前。」该顺序由 T5 的主循环落地;
    本模块提供的两个方法必须**各自只做一件事**,从而使那个顺序在调用点可见。
    `resolve_queue` 因此**不许**顺手把新意图入队,`enqueue` 也**不许**顺手
    成交 —— 否则顺序纪律被藏进方法内部,调换两行不再改变行为,而 ADR-002
    说整条纪律就挂在这两行的顺序上。

  * **ADR-016** —— `TargetOrder` 入队时只存**意图**(`weight`),不存股数。
    到 T+1 开盘才用 `cash + shares * Open_{T+1}` 与 `Open_{T+1}` 算股数;
    解析**不改写** `TargetOrder`,而是产出一个新的 `Fill`。故 `TargetOrder`
    是 `frozen=True` 的值对象,`resolve_queue` 只读它。

  * **ADR-014** —— 「可投资产」的精确定义:

        可投资产_{T+1开盘} ≔ cash + shares * Open_{T+1}

    **它不是 `equity`**(ADR-003 的 `cash + shares * Close`)。成交发生在开盘,
    那一刻当日 Close 尚不存在;ADR-014 明文写「I3 不适用于成交时刻」。本模块
    因此**不调用** `Account.equity_at()` 来算分母 —— 那个方法是收盘基准,
    T3 的 docstring 也写明「T4 要算目标股数时不该调用本方法」。

    ADR-014 钉死的公式是 `floor(w * 可投资产 / Open)` —— **除数就是 `Open`**。
    本模块**不得**把它改成 `Open * (1 + rate)`:那会让每一笔 `rate > 0` 的
    目标成交漂移(`weight=0.5` 从 1847 变 1844,而那里从无超买风险),属
    修改仓位语义而非修 bug(ADR-014a 的「为什么不改除数」逐字如此)。

  * **ADR-012** —— 目标股数 `floor`。算出 0 股则按 ADR-008 无声跳过。
  * **ADR-014a**(2026-10-03 增补)—— **可负担性约束**。钉死的公式在
    `weight ≈ 1` 且 `rate > 0` 时会超买并破坏 I1(实测 `cash=100000`、
    `rate=0.0013`、`Open=27.07`、`weight=1.0` → 3694 股、`gross+fee =
    100126.58`,超支 126.58)。故算出目标后**仅在超支时递减**:

        target = floor(w * investable / Open)
        while target > 0 and target * Open * (1 + rate) > investable:
            target -= 1

    `weight = 0.5` 仍是 **1847**(与原公式完全一致,约束不触发);
    `weight = 1.0` 是 **3689**。数值见 `contracts.yaml` 的
    `fixtures.adr014a_affordability`。这恢复了 ADR-010/016 的「I1 不该靠
    运气成立」「本方案下永不超买」,影响面最小。
  * **ADR-008** —— 差额为 0 则**不生成订单、不报警、静默跳过**。幂等是引擎的
    责任。本模块在**两处**落实它:`enqueue` 之前(T5 读 `pending_intent` 后
    调 `enqueue_intent`,零差额的 `ShareOrder` 不入队)与 `resolve_queue` 内
    (`TargetOrder` 的差额只有到 T+1 开盘才知道,故只能在出队时跳过)。
  * **ADR-041** —— 末次胜出:队列中始终只有一个订单(I4)。`enqueue` 因此
    **覆盖**而非 append。
  * **ADR-020** —— T 日意图在 T+1 开盘**无条件执行**,不论跳空。本模块
    **没有**任何「价格偏离过大就跳过/截断」的分支,这是有意的缺失行为。
  * **ADR-007/043** —— `fee = shares * price * rate`,买卖双向均收取。费率属
    本层(成交层);T3 的 `Account` 只按方向加减已算好的 `fill.fee`。
  * **ADR-009** —— 仅多头。`order(shares=n)` 的 `n < 0` 使 `shares` 变负时
    **报错**(守护 I2)。报错由 `Account.apply` 抛出(它拥有持仓真相);本层
    不预先吞掉它。
  * **ADR-005** —— 最后一根 K 线上的订单被**静默丢弃**(无 warning)。那是
    主循环「跑完不再 resolve」的自然结果:队列里剩下的订单无人出队。本模块
    因此**没有**任何「丢弃」方法 —— ADR-005 的来源自述是「其循环结束后不再
    调用 `_process_orders()`」,且「『且无 warning』是**缺失行为**的断言」。
    把缺失行为实现成一个需要被调用的动作,会让「忘了调」变成新的失败模式。
    丢弃作用于**队列**而非订单(§1 的注:不赋予订单生命周期)。
  * **ADR-037** —— `Fill` 恰好五个字段:`date` / `side` / `shares` / `price`
    / `fee`。`cash_after` / `shares_after` **不是** `Fill` 的字段,由聚合根在
    产生每个 `Fill` 时并行记录(ADR-044 的 `trades_ledger`),那是 T5。

不变式(本模块守护的两条):

  * **I4** 队列长度 `<= 1`(ADR-041)—— 由 `enqueue` 的覆盖语义在构造上保证。
  * **I7** 成交价 `== 成交日的 Open`(ADR-002)—— `resolve_queue` 只从传入的
    `bar` 读 `Open`,本模块**不持有** bar 列表,故结构上取不到别的价位。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .account import BUY, SELL, Account
from .strategy import ShareIntent, TargetIntent

# `strategy` → `engine` 的依赖方向与任务 DAG 一致(§4:T4 依赖 T2),且
# `strategy.py` 不 import `engine`(它的模块注释逐字写明「本模块不 import
# `engine`,也不计算股数」),故无循环。`TargetIntent` / `ShareIntent` 在 T2 的
# `__all__` 里,是 T2→T4 交接接缝的两个真实值对象 —— `enqueue_intent` 按
# `isinstance` 对它们派发(ADR-014:两种下单语义正交、无重载)。

__all__ = [
    "TargetOrder",
    "ShareOrder",
    "Fill",
    "Order",
    "BarLike",
    "Backtest",
    "bar_date_str",
]


# ═════════════════════════ 值对象 ═════════════════════════
#
# §1 的实体/值对象表:`TargetOrder` / `ShareOrder` 是**值对象**,不可变。
# 退化为值对象(而非参考实现里带 `cancel()` 的实体)的理由是 **I4**:队列中
# 任一时刻最多一个订单(ADR-041 的末次胜出),故永远无需区分两个订单。
# 若 v1 引入 GTC(OQ-06 ⑤),I4 失效,它们须回归实体。
#
# 为什么 `eq=False`:T4 的门要断言「T 日入队的订单对象与 T+1 出队的是**同一个**」
# (`is` 比较)。`is` 不受 `__eq__` 影响,但 dataclass 默认的值相等会让两个
# **不同**的 `TargetOrder(weight=0.8)` 比较为 `==` —— 任何用 `==` 代替 `is`
# 的实现/测试都会在「每日重算」的错误实现上静默通过。保留默认的身份相等,
# 使 `==` 与 `is` 在这两个类上重合,那条门就无法被误写成等价的弱断言。
# (不可变性仍由 `frozen=True` 保证,它才是 ADR-016「解析不改写它」的要求。)


@dataclass(frozen=True, eq=False)
class TargetOrder:
    """`target(weight=w)` 的订单(ADR-014 / ADR-016)。

    **只持 `weight`,不持股数。** 股数在 T+1 开盘由 `resolve_queue` 用
    `floor(weight * 可投资产 / Open)` 算出(ADR-012/014/016),且解析
    **不改写本对象** —— 产出的是一个新的 `Fill`。
    """

    weight: float


@dataclass(frozen=True, eq=False)
class ShareOrder:
    """`order(shares=n)` 的订单(ADR-014)。

    差额就是 `n` 本身:`n > 0` 买、`n < 0` 卖。`n < 0` 使持仓变负时按
    ADR-009 报错(守护 I2),该报错发生在成交时刻(持仓在那时才确定)。
    """

    shares: int


#: 队列元素的联合类型。I4 使队列长度恒 `<= 1`,故它永远是「0 或 1 个 Order」。
Order = TargetOrder | ShareOrder


@dataclass(frozen=True)
class Fill:
    """成交(ADR-037 钉死字段,构建期不得变更;`amount` 由 ADR-046 补入)。

    T4 的门读 `fill.shares` / `fill.price` / `fill.fee` / `fills[0].date` /
    `fills[1].side`,故字段名与顺序都不可改。

    * `date` —— `YYYY-MM-DD`(`contracts.yaml` 的 `formats.date`)
    * `side` —— `BUY` / `SELL`(`contracts.yaml` 的 `trade_sides`)
    * `shares` —— **恒为正**,方向由 `side` 承载(ADR-037)
    * `price` —— 成交日的 `Open`(I7)
    * `amount` —— `shares * price`,**不含手续费**(ADR-046)。由后端提供是
      因为 ADR-025 规定「乘、除、加、减一律在后端」,而批准稿的交易清单有
      「金额」列 —— 前端算 `shares × price` 会在 I8 上开口子
    * `fee` —— `shares * price * rate`(ADR-007/043)

    `cash_after` / `shares_after` 不在此处:ADR-037 明文「不是 `Fill` 的字段」,
    它们是 `RunResult.trades_ledger` 的并行序列(ADR-044),归 T5。
    """

    date: str
    side: str
    shares: int
    price: float
    fee: float
    # ADR-046 的新字段放在**末尾**且有默认值,不插在 `fee` 之前 ——
    # ADR-037 钉死了前五个字段的名字**与顺序**,而 T3 的门大量使用位置构造
    # (`Fill("2020-01-02", BUY, 1, 100.0, 0.1)`)。插在中间会让第 5 个位置
    # 参数从 `fee` 静默变成 `amount`:那些门测的是账本对**恶意输入**的反应,
    # 错位后它们仍然「通过」,但测的已经不是原来那件事了。
    #
    # 默认值取 0.0 而非必填,是因为 `Account` 从不读 `amount`(它自己按
    # shares × price 记账),故调用方构造的影子 Fill 无须提供它。真实成交
    # 一律由 `resolve_queue` 显式传入。
    amount: float = 0.0


# ═════════════════════════ bar 的结构化类型 ═════════════════════════
#
# 为什么用 Protocol 而不要求一个具体的 `Bar` 类:§1 把 `Bar` 列为值对象,但
# ADR-035 没有把它钉死为 `engine.py` 的公开符号,而 T1 的 `load_csv` 返回的是
# **DataFrame**(index 0..n-1,`Date` 为 datetime64 的列)。T5 的主循环最自然
# 的写法是 `df.itertuples()`,其产物是 namedtuple 而不是任何自定义类。
# 要求具体类会逼 T5 为每根 K 线多造一个对象,而 `resolve_queue` 真正用到的
# 只有两个字段。(与 T3 用 `FillLike` 的理由同构。)


@runtime_checkable
class BarLike(Protocol):
    """`resolve_queue` 对 bar 的全部要求:`Open` 与 `Date`。

    **只有 `Open`** —— 这是 I7(成交价 == 成交日的 Open)在类型层的投影:
    本模块结构上拿不到 `Close`/`High`/`Low`,故「用错价位」的实现写不出来。
    """

    @property
    def Open(self) -> float: ...

    @property
    def Date(self) -> Any: ...


def bar_date_str(date: Any) -> str:
    """把 bar 的日期规范成 `YYYY-MM-DD`(`contracts.yaml` 的 `formats.date`)。

    T1 的 `load_csv` 把 `Date` 转成 `datetime64`,故 `itertuples()` 给出的是
    `pandas.Timestamp`;而 T4 的门会手工构造 bar 并直接写字符串日期。两者都要
    产出同一种 `Fill.date`,否则 `trades.csv` 的 `date` 列(ADR-037:`YYYY-MM-DD`)
    会按数据来源而变。

    `strftime` 优先于 `str()`:`str(Timestamp("2020-01-02"))` 给
    `'2020-01-02 00:00:00'`,带上了 ADR-037 不要的时分秒。
    """
    strftime = getattr(date, "strftime", None)
    if strftime is not None:
        return str(strftime("%Y-%m-%d"))
    return str(date)


# ═════════════════════════ 聚合根 ═════════════════════════


class Backtest:
    """聚合根(§1)。唯一入口,持有账本与订单队列,守护不变式。

    T4 的职责边界:**队列 + 成交**。价格序列、主循环、`equity[]`、`Summary`
    归 T5(§4.5),故本阶段的 `Backtest` 不持有 bar 列表 —— `resolve_queue`
    从**参数**接收当日 bar。这不是省略:不持有 bar 列表使 I7 在构造上成立
    (取不到别的价位),也使 T4 的门可以不造完整数据就跑成交语义。

    生命周期(§1)`created -> running -> finished` 属 T5 的主循环;T4 不实现
    状态机,以免与 T5 追加的 `run()` 争同一个字段。
    """

    def __init__(self, cash: float, fee: float = 0.0) -> None:
        """
        Args:
            cash: 初始资金,交给 `Account`(T3)守 I1。
            fee: 费率 `rate`(ADR-007,默认 `0.0`;ADR-039 要求 `rate >= 0`)。
                注意是**费率**而不是每笔费用额 —— `fee = shares * price * rate`。
        """
        rate = float(fee)
        if not math.isfinite(rate):
            raise ValueError(f"fee(费率)必须是有限数(ADR-039):实得 {fee!r}")
        if rate < 0.0:
            # ADR-039 / §1 的 `FeeRate` 值对象:`rate >= 0`。负费率是「交易
            # 送钱」,会让净值曲线凭空增长,且 `Account` 的 fee >= 0 校验会在
            # 第一笔成交才发现 —— 那时错误已经跑了一整轮构造。
            raise ValueError(f"fee(费率)不得为负(ADR-039 的 rate >= 0):实得 {rate}")

        #: 账本 —— **聚合内部实体,不可被聚合外引用**(§1 的实体表逐字;
        #: ADR-044 重申 T6/T7 不得触及它)。故它是 `_account` 而**不是**公开
        #: 属性:公开它等于邀请聚合外的代码去读/改账本,而 §1 说 `Account`
        #: 「无独立身份、无 id」、随聚合消亡。聚合外需要的三个标量由本类的
        #: 只读转发属性 `cash` / `shares` / `equity_at(close)` 提供
        #: (ADR-035 只钉死了 `queue`/`enqueue`/`resolve_queue`,加性地增加
        #: 转发属性不破坏那个名字契约,§7 第 1 条允许加性变更)。
        self._account = Account(cash=cash)
        self.fee_rate = rate

        #: 订单队列(ADR-035 钉死的名字;T4 的门对它采样)。
        #:
        #: 是**真实的 list**,不是「把差额存成标量字段」的等价物:ADR-006 说
        #: 队列是「信号与成交分离的物理载体」,而 T4 的 I4 门明文要抓的就是
        #: 那种实现 —— 它能通过「恒 <= 1」、通过 I7、通过所有 ADR-008 条款,
        #: 但队列在它里面根本不存在。
        self.queue: list[Order] = []

    # ───────────────────────── 入队(B) ─────────────────────────

    def enqueue(self, order: Order) -> None:
        """把订单放入队列。**末次胜出**(ADR-041),故**覆盖**而非 append。

        ADR-035 钉死本名字,`contracts.yaml` 的 `gate_execution.monkeypatch_seams`
        把它列为打桩接缝 —— T4 的 `push_count` 门对本方法计数。因此:
        每一次「决定要下单」都**必须**经过这里,不得有任何旁路写
        `self.queue`;而**不该**下单的情形(ADR-008 的零差额、ADR-012 的
        0 股)必须在**调用之前**就被拦掉,否则 `push_count` 会把被跳过的单
        也数进去,而那正是该门要区分的两种错误实现。

        I4(队列长度 <= 1)由本方法在构造上保证:先清空再放入。

        ADR-041 的「先前调用被丢弃而非排队」在这里是一行 —— 但要注意它与
        T2 的 `Strategy.pending_intent`(同样末次胜出)是**两层**:策略侧在
        一个 `next()` 内收敛到一个意图;本方法则保证跨 bar 也只有一个订单
        (昨天未成交的单被今天的覆盖)。
        """
        if order is None:
            raise ValueError(
                "enqueue(None):不该下单时请根本不要调用 enqueue —— "
                "ADR-008 的零差额与 ADR-012 的 0 股必须在入队前跳过,"
                "否则 T4 的 push_count 门数到被跳过的单。"
            )
        if not isinstance(order, (TargetOrder, ShareOrder)):
            raise ValueError(
                f"enqueue 只接受 TargetOrder / ShareOrder(§1 的值对象),"
                f"实得 {type(order).__name__}"
            )
        self.queue.clear()
        self.queue.append(order)

    def enqueue_intent(
        self, intent: TargetIntent | ShareIntent | None
    ) -> Order | None:
        """把 T2 的 `Strategy.pending_intent` 翻译成订单并入队。

        这是 T2 与 T4 的接缝:`strategy.py` 的注释写明「T4 把它们翻译成自己
        的队列元素」,而 ADR-035 把队列元素的类名留给了本模块。两个意图类
        (`TargetIntent`/`ShareIntent`)与两个订单类(`TargetOrder`/`ShareOrder`)
        因此是一一对应的,本方法做那次翻译。

        **派发用 `isinstance` 而不是 `getattr` 探属性**:`TargetIntent` 与
        `ShareIntent` 是 `strategy.py` 里真实可导入的 frozen 值对象(T2 已把
        它们列入 `__all__`),按类型派发才让「只有这两种意图」这件事在代码里
        可读;按属性探测会把任何恰好带 `weight` 字段的对象当成目标仓位意图,
        而 ADR-014 说这两种语义**正交、无重载** —— 正交关系只能靠类型表达。

        **ADR-008 的第一处落实**(第二处在 `resolve_queue`):`order(shares=0)`
        的差额恒为 0,此刻就知道,故**不入队**。`TargetOrder` 的差额要到 T+1
        开盘用那天的 `Open` 才知道(ADR-016),无法在此判断 —— 这正是 ADR-008
        必须分两处落实的原因,不是遗漏。

        Returns:
            实际入队的订单;`None` 表示没有意图或按 ADR-008 跳过。
            返回值使 T5 的主循环可以在「入队后」采样(T4 的 I4 门要求在
            入队后与出队前各采样一次)。
        """
        if intent is None:
            return None

        if isinstance(intent, TargetIntent):
            order: Order = TargetOrder(weight=float(intent.weight))
        elif isinstance(intent, ShareIntent):
            n = int(intent.shares)
            if n == 0:
                # ADR-008:差额为 0 则不生成订单、不报警、静默跳过。
                return None
            order = ShareOrder(shares=n)
        else:
            raise ValueError(
                f"无法识别的意图(ADR-014 只有 target(weight=) 与 "
                f"order(shares=) 两种,对应 strategy.TargetIntent / "
                f"strategy.ShareIntent):实得 {intent!r}"
                f"({type(intent).__name__})"
            )

        self.enqueue(order)
        return order

    # ───────────────────────── 出队并成交(A) ─────────────────────────

    def resolve_queue(self, bar: BarLike) -> Fill | None:
        """结算**昨天**的意图:出队、按 `bar.Open` 成交、记账,返回 `Fill`。

        ADR-035 钉死本名字并明文要求它是**命名方法** ——「内联 `self.queue.pop()`
        会让那条门无法执行」。T4 的门在此采样「出队前」的队列,并用 `is` 比对
        「T 日入队的订单对象与 T+1 出队的是同一个」,用以抓出「每日重算」的
        实现:它结果可能对,但 ADR-002 的纪律已经没了。

        算法(ADR-002/008/012/014/016/020 各自贡献一步,顺序不可换):

          1. **出队**。队列空 → 返回 `None`。弹出必须在最前面:ADR-016 说
             解析不改写订单,故此后只读它。
          2. **算可投资产**(ADR-014):``investable = cash + shares * bar.Open``。
             用成交日的 `Open`,**不是** `equity`(Close 基准),那一刻当日
             Close 尚不存在。
          3. **算目标股数**:`TargetOrder` →
             `floor(weight * investable / Open)`(ADR-014 钉死的公式,
             ADR-012 的 floor、ADR-016 的 T+1 才算),再按 **ADR-014a**
             的可负担性约束**仅在超支时递减**,最后**减当前持仓**得差额;
             `ShareOrder` → 差额就是 `shares` 本身(ADR-014)。

             除数是裸 `Open`,**不是** `Open * (1 + rate)` —— 改除数会让
             每一笔 `rate > 0` 的目标成交漂移(ADR-014a 的「为什么不改除数」)。
             ADR-014a 的递减循环只在 `target * Open * (1 + rate) > investable`
             时转,故 `rate == 0`(ADR-007 默认值)时一次都不转。
          4. **ADR-008**:差额为 0 → 返回 `None`。不报警、不记账、不产生
             `Fill`(故 ADR-042 的交易次数不会偏大)。订单已在第 1 步出队,
             按 ADR-041 被丢弃而非回退执行。
          5. **算手续费**(ADR-007):`fee = shares * price * rate`,`shares`
             取**绝对值**(ADR-037:`Fill.shares` 恒为正,方向由 `side` 承载)。
          6. **记账**(ADR-043 的方向由 `Account` 承担),再返回 `Fill`。
             顺序是「先记账后返回」:`Account.apply` 守 I1/I2,一笔违反不变式
             的成交必须在 `Fill` 流到 `trades[]` **之前**抛错 —— 否则 T5 会把
             一笔从未发生的交易写进 `trades.csv`。

        **ADR-020**:第 2–6 步没有任何「跳空太大就跳过/截断」的分支。T 日的
        意图在 T+1 开盘**无条件执行**。ADR-016 的「已知代价」正是这条:引擎
        会在任何跳空下适应新价格并忠实执行意图。

        Args:
            bar: 成交日(T+1)的 K 线。只读 `Open` 与 `Date`(见 `BarLike`)。

        Returns:
            成交则为 `Fill`;队列空或按 ADR-008 跳过则为 `None`。

        Raises:
            ValueError: 卖出超过持仓(I2 / ADR-009)或现金不足(I1 / ADR-010),
                由 `Account.apply` 抛出 —— 它拥有持仓与现金的真相。
        """
        # ── 步骤 1:出队 ────────────────────────────────────────────────
        #
        # `pop()` 而不是 `[0]` + 后续清空:出队必须**无条件**发生,包括第 4 步
        # 的 ADR-008 跳过。否则一个零差额的订单会留在队列里,被明天再解析一次
        # —— 那就成了 GTC(OQ-06 ⑤ 明确留给 v1),且 I4 在明天入队后会失效。
        if not self.queue:
            return None
        order = self.queue.pop()

        open_price = float(bar.Open)
        if not math.isfinite(open_price) or open_price <= 0.0:
            # ADR-019 保证载入后的价格全部 > 0,故这里只会是算错的中间值。
            # 它若静默通过,第 2 步的分母会是 0 或 NaN,产出一个无意义的股数。
            raise ValueError(
                f"成交价(bar.Open)必须 > 0(ADR-019 已保证):实得 {bar.Open!r}"
            )

        shares_held = self._account.shares

        # ── 步骤 2–3:算差额 ───────────────────────────────────────────
        if isinstance(order, TargetOrder):
            # ADR-014 的「可投资产」,逐字:cash + shares * Open_{T+1}。
            investable = self._account.cash + shares_held * open_price
            # ── ADR-014 钉死的公式,逐字。除数是 `Open`,**不是**
            #    `Open * (1 + rate)` ───────────────────────────────────────
            #
            # ADR-012:floor。`math.floor` 而不是 `int()`:两者对正数相同,
            # 但 `int()` 是朝零截断,负数上与 floor 分叉 —— 目标股数恒 >= 0
            # (weight >= 0 由 ADR-015 保证),故此刻等价,但写 floor 才是
            # ADR-012 说的那件事,也不会在 v1 引入 weight 的负值时静默改语义。
            target_shares = math.floor(order.weight * investable / open_price)

            # ── ADR-014a:可负担性约束 —— 只在**真正买不起**时递减 ─────────
            #
            # ADR-014a(2026-10-03 增补)逐字:
            #
            #     target = floor(w * investable / Open)        # 钉死的公式不变
            #     while target > 0 and target * Open * (1 + rate) > investable:
            #         target -= 1                               # 只在真正买不起时生效
            #
            # 缺陷本身是真的:`cash=100000`、`rate=0.0013`、`Open=27.07`、
            # `weight=1.0` → `floor(100000/27.07) = 3694` 股、`gross+fee =
            # 100126.58`,**超支 126.58**,`Account.apply` 抛 I1 而整轮中止 ——
            # 而 ADR-010/016 明文「I1 是不变式,不该靠运气成立」「本方案下
            # 永不超买」。
            #
            # **为什么不改除数**(ADR-014a 逐字,也是本轮的关键约束):把除数
            # 改成 `Open * (1 + rate)` 能一步解决,但它会改变**每一笔**
            # `rate > 0` 的目标成交 —— `weight = 0.5` 从 1847 变 1844,而那里
            # 从无超买风险。那是修改仓位语义,不是修 bug。本约束只在超支时
            # 生效,影响面最小。
            #   (`contracts.yaml` 的 `fixtures.adr014a_affordability` 把两者
            #    钉死为可分辨的两个值:`expect_shares_half == 1847` 与
            #    `reject_divisor_change_half == 1844`。)
            #
            # 循环的两个守卫各有理由:`target > 0` 使它必然终止且不会递减到
            # 负数(卖出侧的差额由 `target_shares - shares_held` 产生,不是
            # 这里);`> investable` 用严格大于,故「恰好花光」(ADR-012 的
            # `floor` 用尽余额是**设计预期**,见 `fixtures.adr012_floor_vs_ceil`)
            # 不被误判成买不起。
            #
            # 向后兼容:`rate == 0.0`(ADR-007 的默认值)时
            # `target * Open <= investable` 由 floor 本身保证,循环一次都不转
            # —— T4 全部钉死 fixture(adr016_* / adr014_* / adr012_* /
            # adr041_*)的费率均为 0,故它们的期望值一个未动。
            # 约束作用于**差额**,不是整个仓位:已持有的股份不需要再买一次,
            # 故只有 `target_shares - shares_held` 这部分要用现金负担。
            #
            # 本条初版对**整个仓位**计费(`target * unit_cost > investable`)。
            # 但 `investable` 已把持股按 `Open` 计入,循环却对全部股份收费,
            # 于是 `shares_held > 0` 时过度递减 —— 实测后果:买入持有在第二根
            # K 线上**卖出 1 股**(差额 −1)然后来回倒腾。三个钉死 fixture 都
            # 从空仓起,故全部照常通过,缺陷只在持仓后出现。
            # (ADR-014a 的 2026-10-04 修正,由 T8 的 `--fee` 端到端门抓到。)
            unit_cost = open_price * (1.0 + self.fee_rate)
            while (
                target_shares > shares_held
                and (target_shares - shares_held) * unit_cost > self._account.cash
            ):
                target_shares -= 1

            delta = target_shares - shares_held
        else:
            # ADR-014:`order(shares=n)` 的差额就是 `n` 本身。
            delta = order.shares

        # ── 步骤 4:ADR-008 —— 差额为 0 则无声跳过 ─────────────────────
        #
        # 「不生成订单、不报警、静默跳过」。有意**不**发 warning:ADR-008 的
        # 偏离理由写明,参考实现 60 根 K 线产生 57 个警告,使真问题无法从
        # 警告流里辨别。§7 第 1 条也禁止给 ADR 锁定的行为加旁路。
        if delta == 0:
            return None

        # ── 步骤 5:ADR-007 —— fee = shares * price * rate ─────────────
        #
        # `abs(delta)`:ADR-037 规定 `Fill.shares` 恒为正,方向由 `side` 承载。
        # 费率乘在**绝对**股数上,故卖出同样收费(ADR-043 的「双向均收取」)。
        fill_shares = abs(delta)
        side = BUY if delta > 0 else SELL
        amount = fill_shares * open_price
        fee = amount * self.fee_rate

        fill = Fill(
            date=bar_date_str(bar.Date),
            side=side,
            shares=fill_shares,
            # I7:成交价 == 成交日的 Open。本方法只有这一个价格来源。
            price=open_price,
            # ADR-046:成交额,**不含手续费**。前端直接显示,不做乘法。
            amount=amount,
            fee=fee,
        )

        # ── 步骤 6:记账(I1/I2 由 T3 守),再返回 ──────────────────────
        self._account.apply(fill)
        return fill

    # ──────────────── 账本的只读转发面(§1 / ADR-044) ────────────────
    #
    # §1 的实体表:`Account` 是**聚合内部实体**,「不可被聚合外引用」;
    # ADR-044 重申 T6/T7 不得触及它。故 `_account` 是私有的,聚合外需要的
    # 三个量全部经下面的**只读**转发面取得。没有 setter:写入必须经
    # `resolve_queue` → `Account.apply`(ADR-043 的唯一记账入口),否则
    # I1/I2 就有了一条绕过校验的旁路。
    #
    # 加性说明(§7 第 1 条 / 第 8 条):ADR-035 把本模块钉死的名字是
    # `queue` / `enqueue` / `resolve_queue`(以及 T5 的 `run` / `compute_gaps`
    # 与各值对象)。它规定哪些名字**必须存在**,未禁止加性地增加,故这三个
    # 转发成员不破坏那个名字契约。
    #
    # ADR-005 没有对应的方法:它说「最后一根 K 线没有 T+1,其上产生的订单被
    # 静默丢弃」,而那是主循环**跑完不再 resolve** 的自然结果 —— 队列里剩下
    # 的订单无人出队。给它加一个 `discard_queue()` 会把「缺失行为」实现成一个
    # 需要被调用的动作,反而让「忘了调」变成一种新的失败模式。

    @property
    def cash(self) -> float:
        """账本现金(ADR-035 把只读属性钉在 `Account` 上,这里只是转发)。"""
        return self._account.cash

    @property
    def shares(self) -> int:
        """账本持仓(同上)。"""
        return self._account.shares

    def equity_at(self, close: float) -> float:
        """按给定 `Close` 估值:``cash + shares * close``(ADR-003)。

        **纯转发**到 `Account.equity_at`(ADR-035 钉死那个名字),存在的理由
        只有一个:聚合外不得引用 `Account`(§1 / ADR-044),而 ADR-036 要求
        引擎在每次回调前把 `equity`(收盘基准)绑给策略 —— 那个值必须有一条
        合法的取法。

        口径提醒(ADR-014 澄清的那处语义冲突):这是 `equity`,**收盘基准**,
        每个交易日一个(ADR-003 / I3)。ADR-014 的「可投资产」是
        ``cash + shares * Open_{T+1}`` —— 同一形状、不同价位、不同用途。
        `resolve_queue` 算目标股数时**不调用本方法**(它在成交那一刻连当日
        Close 都还不存在)。
        """
        return self._account.equity_at(close)

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (
            f"Backtest(cash={self._account.cash:.2f}, "
            f"shares={self._account.shares}, queue={len(self.queue)})"
        )
