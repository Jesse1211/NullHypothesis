"""账本原语(T3)—— `Account`:现金 / 持股 / 逐笔记账 / 收盘估值。

契约来源:
  * ADR-035 —— 本模块的公开符号被**钉死**,构建期不得变更:
        ``Account.cash``       只读属性 -> float
        ``Account.shares``     只读属性 -> int
        ``Account.apply(fill)`` -> None
        ``Account.equity_at(close)`` -> float   (== cash + shares * close)
    跨任务布局门(`tests/test_layout.py`)逐个断言这四个名字,T5 的 I3 门也
    依赖同一口径。改名会让 T3 的门自产自销、而 T5 读不到。
  * ADR-043 —— **手续费双向收取**,这是本模块唯一的记账公式:
        买入:``cash -= shares * price + fee``
        卖出:``cash += shares * price - fee``
    手续费本身由 ADR-007 定义(``fee = shares * price * rate``,默认
    ``rate = 0.0``),但**它在 `Fill` 里已经算好** —— 本模块只按方向加减,
    **不自己乘费率**(费率属 T4 的成交层)。
  * ADR-003 —— 估值 ``equity = cash + shares * Close``。本模块只提供
    「给一个 Close 算一次」的纯函数 `equity_at`;逐日的 `equity[]` 序列是
    聚合根(T5)的产出物,不在这里(§4.5 归属矩阵)。
  * ADR-009 —— **仅多头**:``shares ∈ [0, +∞)``。`sell` 只能平仓;超额卖出
    或无持仓卖出 **报错**。
  * ADR-010 —— **I1 `cash >= 0`**:v0 无杠杆、全额付款,现金不得透支。
    ADR-016 的「T+1 开盘才算股数」在构造上保证永不超买,但 I1 仍必须在
    账本这一层**守住**:构造上的保证不等于运行时的断言,而 `Fill` 是由
    调用方构造的(见下),账本无从假设调用方算对了。

本任务的边界(§4 变更说明 + T3 门的前言,逐字):
    「T3 只做 `Account.apply(Fill)` 等原语,`Fill` 由**调用方构造**。」
故本模块**不含任何「成交价从哪来」的逻辑** —— 没有 Open/Close 的选取、
没有目标股数、没有 floor 取整、没有费率。那些全是 T4(ADR-012/014/016)。

`Fill` 的类型归属(为什么这里用 Protocol 而不 import 它):
    ADR-035 把 `Fill` 等值对象钉在 `nullhypothesis/engine.py`(T4/T5 产出),
    §4.5 归属矩阵又规定每个产出物**恰好一个产出者**。T3 若自己定义一个
    `Fill`,与 T4 的那个就是两个同名不同源的类(T1 的 `errors.py` 注释里
    记着同一个坑:两个 agent 各写一半,后写者静默覆盖)。T3 若 import
    `engine`,则 T3 → T4 的依赖方向与 DAG(T4 依赖 T3)相反,且 T3 的门
    在 `engine.py` 尚不存在时直接 ImportError。
    唯一两者都不犯的写法:**结构化类型**。本模块只要求 `fill` 具备
    ADR-037 钉死的那五个属性中它用得到的三个(`side`/`shares`/`price`/`fee`)。
    运行期不做 isinstance 检查,故 T4 的 `engine.Fill` 与 T3 门里调用方
    手工构造的 `Fill` 都能直接传进来。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["Account", "FillLike", "BUY", "SELL", "EPSILON"]

#: ADR-037 钉死的 `side` 取值(`contracts.yaml` 的 `trade_sides`,顺序固定)。
#: 大写字面量;本模块**不**接受小写或别名 —— 静默接受别名会让 T4 的一个
#: 拼错的 side 变成「既不买也不卖」的无声 no-op。
BUY = "BUY"
SELL = "SELL"

#: 浮点容差,单位「分」。I1(`cash >= 0`)必须允许 `cash` 落在 `-1e-9` 这类
#: 纯浮点残差上 —— 否则一笔「正好花光现金」的合法成交(ADR-012 的
#: `floor` 恰好用尽余额是**设计预期**,见 `fixtures.adr012_floor_vs_ceil`)
#: 会因 `cash == -2.9e-12` 而被报成透支。容差与 I1/I3 的门同一口径(0.01)。
EPSILON = 0.01


@runtime_checkable
class FillLike(Protocol):
    """`Fill` 值对象的**结构化**契约(属性名见 ADR-037,构建期不得变更)。

    ADR-037 的 `Fill` 恰好五个字段:``date / side / shares / price / fee``。
    账本记账只需要后四个中的三个加 `side`;`date` 由聚合根用于 `trades.csv`
    与 `EquityPoint`,账本不读它(账本**没有「交易日」概念** —— T3 门的
    括注逐字如此)。

    `shares` **恒为正**(ADR-037),方向由 `side` 承载。因此「卖出用负
    shares」不是本模块的合法输入:那会让同一个方向被编码两次,两处矛盾时
    无人知道哪个是真的。
    """

    @property
    def side(self) -> str: ...

    @property
    def shares(self) -> int: ...

    @property
    def price(self) -> float: ...

    @property
    def fee(self) -> float: ...


class Account:
    """聚合内部实体:一个 `Backtest` 恰有一个账本,随聚合消亡(§1)。

    §1 声明 `Account` **不可被聚合外引用** —— ADR-044 重申 T6/T7 不得触及它。
    故本类不提供任何序列化、不持有 bar 列表、不知道 `run_id`。它只是
    「现金 + 持股」这两个标量,加上让它们按 ADR-043 变化的唯一入口。

    不变式(聚合根职责,但在本层**逐笔**守住):
        I1  ``cash >= 0``    (ADR-010)
        I2  ``shares >= 0``  (ADR-009,仅多头)

    两条都在 `apply` 内**先算后验、验不过不改**(见 `apply` 的文档)。
    """

    #: 实例属性放 `__slots__`:账本只有这两个状态量,任何第三个字段都意味着
    #: 有人把「成交价从哪来」或逐日序列塞进了这一层(T3 的边界)。
    __slots__ = ("_cash", "_shares")

    def __init__(self, cash: float, shares: int = 0) -> None:
        """用初始资金建账。

        Args:
            cash: 初始现金。必须 ``>= 0``(I1 在**开账时**就成立,否则
                第一笔成交前账本已违反不变式)。ADR-039 的 CLI 层另有
                ``--cash > 0`` 的更强约束,那是参数校验,不是账本契约 ——
                ``cash == 0`` 的账本是合法的(它只是什么都买不起)。
            shares: 初始持股,默认 0。必须 ``>= 0``(I2)。保留该参数是为了
                让测试能从任意合法状态出发构造影子场景;生产路径(T5)永远
                从 ``shares=0`` 开始。

        Raises:
            ValueError: 初始 `cash` 为负(违反 I1)或 `shares` 为负(违反 I2)。
                用 `ValueError` 而非 `errors.py` 的两个类:ADR-040 只定义了
                `DataValidationError`(载入期)与 `StrategyError`(策略层),
                账本不变式属于**第三类**,而 `errors.py` 是 T1 的产出物
                (§4.5:T2/T8 只 import,不新建)—— T3 同理不得往里加类。
                `ValueError` 是本设计对「非法值」的统一类型(ADR-019 的行为表、
                ADR-036 的 `init()` 内下单、ADR-037 的 stem 重复都用它),
                且 `DataValidationError` 本身就是它的子类,故 catch `ValueError`
                的上层代码对三者的处理一致。
        """
        cash = float(cash)
        shares = _as_int_shares(shares, label="初始 shares")
        if cash < -EPSILON:
            raise ValueError(
                f"初始现金不得为负(I1,ADR-010):cash={cash:.2f}"
            )
        if shares < 0:
            raise ValueError(
                f"初始持股不得为负(I2,仅多头,ADR-009):shares={shares}"
            )
        self._cash: float = max(cash, 0.0)
        self._shares: int = shares

    # ───────────────────────── 只读属性(ADR-035) ─────────────────────────
    #
    # 用 `@property` 且**不提供 setter**:ADR-035 把它们钉为「只读属性」,
    # 而 ADR-036 的 T2 门对策略侧的同名三件套明确要求「断言写入三者报错」。
    # 账本是那三者的数据源,若这里可写,ADR-043 的记账公式就有了一条绕过
    # I1/I2 校验的旁路 —— 那正是不变式最常见的失守方式。

    @property
    def cash(self) -> float:
        """当前现金(ADR-035 钉死;恒 ``>= 0``,见 I1)。"""
        return self._cash

    @property
    def shares(self) -> int:
        """当前持股(ADR-035 钉死;恒 ``>= 0``,见 I2 / ADR-009)。"""
        return self._shares

    # ───────────────────────────── 记账 ─────────────────────────────

    def apply(self, fill: FillLike) -> None:
        """把一笔**调用方构造的**成交记入账本(ADR-043,ADR-035 钉死的签名)。

        ADR-043 的公式,逐字:

            买入:``cash -= shares * price + fee``
            卖出:``cash += shares * price - fee``

        两个方向的 `fee` 符号**相同**(都是账本的流出),但成交额的符号相反
        —— 这正是 ADR-043 存在的理由:ADR-007 只给了公式没说方向,导致原先
        T3 的门「现金扣减 == 成交额 + 手续费」对卖出字面错误。

        **先算后验、验不过不改**(本方法的关键结构):两个新值都算完、两条
        不变式都通过,才一次性写回 `self`。若边算边写,一笔「现金够、但股数
        不够」的卖出会在抛错前已经把 `cash` 改了 —— 账本停在一个违反不变式
        的中间态,而调用方接住异常后看到的是脏数据。记账要么整笔发生,要么
        完全不发生。

        Args:
            fill: 任何具备 ADR-037 的 `side`/`shares`/`price`/`fee` 的对象
                (见 `FillLike`)。`shares` 恒为正,方向由 `side` 承载。
                **成交价由调用方(T4)决定** —— 本方法不校验 `price` 来自
                哪根 K 线的哪个价位,那是 I7 的事,属 T4 的门。

        Raises:
            ValueError:
                * `side` 不是 ``BUY`` / ``SELL``;
                * `shares <= 0`、`price < 0` 或 `fee < 0`(非法成交);
                * 买入会使 ``cash`` 变负(违反 I1,ADR-010);
                * 卖出股数超过持仓,即会使 ``shares`` 变负
                  (违反 I2 / ADR-009 的「超额卖出或无持仓卖出报错退出」)。
        """
        side = fill.side
        shares = _as_int_shares(fill.shares, label="fill.shares")
        price = float(fill.price)
        fee = float(fill.fee)

        # ── 输入合法性:非法 Fill 不该被静默吞掉 ─────────────────────────
        if side not in (BUY, SELL):
            raise ValueError(
                f"fill.side 必须是 {BUY!r} 或 {SELL!r}(ADR-037),实得 {side!r}"
            )
        if shares <= 0:
            # ADR-037:`shares` 恒为正。0 股的「成交」按 ADR-008/012 根本
            # 不该被生成(差额为 0 则不生成订单、静默跳过)—— 它若到了账本,
            # 说明 T4 的 floor 分支漏了那个 early return,静默接受会把一条
            # 空记录写进 trades.csv 并让交易次数(ADR-042)偏大。
            raise ValueError(
                f"fill.shares 必须为正(ADR-037;0 股成交应按 ADR-008/012 "
                f"在成交层就跳过),实得 {shares}"
            )
        if price < 0.0:
            raise ValueError(f"fill.price 不得为负:实得 {price}")
        if fee < 0.0:
            # ADR-039/§1:`FeeRate` 的值对象约束是 `rate >= 0`,故 fee >= 0。
            raise ValueError(f"fill.fee 不得为负(ADR-039 的 rate >= 0):实得 {fee}")

        gross = shares * price

        # ── ADR-043:按方向算出新状态(此处仍不写回) ────────────────────
        if side == BUY:
            new_cash = self._cash - (gross + fee)
            new_shares = self._shares + shares
        else:  # SELL
            new_cash = self._cash + (gross - fee)
            new_shares = self._shares - shares

        # ── I2(ADR-009,仅多头)先验:它的消息比 I1 更能指出真正的病因 ──
        #
        # 顺序有意如此:一笔无持仓的卖出同时可能让 cash 变负(卖出扣 fee),
        # 两条都失败时报 I1「现金不足」会把作者引向资金规模,而真正的错是
        # 「你没有那么多股」。
        if new_shares < 0:
            raise ValueError(
                f"卖出股数超过持仓,shares 将变为 {new_shares}"
                f"(违反 I2;ADR-009 仅多头:超额卖出或无持仓卖出报错)。"
                f"持仓={self._shares},卖出={shares}"
            )

        # ── I1(ADR-010,cash >= 0) ──────────────────────────────────────
        #
        # 容差只放在**负侧**且只有 EPSILON 分:ADR-016 的设计使买入永不超买,
        # 故真实的负现金只会是浮点残差。真超支(差一分以上)必须响亮失败,
        # 而不是被 clamp 成 0 —— clamp 会凭空创造资金,让净值曲线对不上账。
        if new_cash < -EPSILON:
            raise ValueError(
                f"现金不足,cash 将变为 {new_cash:.2f}"
                f"(违反 I1;ADR-010:v0 无杠杆、全额付款,现金不得透支)。"
                f"现金={self._cash:.2f},成交额={gross:.2f},手续费={fee:.2f}"
            )

        # ── 两条不变式都过,整笔写回 ────────────────────────────────────
        #
        # 负侧残差夹到 0:若留着 `-3e-13`,它会顺着 ADR-003 的
        # `cash + shares*Close` 渗进 equity 序列,并让 T5 的 I3 影子对账在
        # 「现金恰好花光」的那一天看到一个负现金。夹的只是容差内的残差,
        # 真超支在上面已经抛了。
        self._cash = new_cash if new_cash > 0.0 else max(new_cash, 0.0)
        self._shares = new_shares

    # ───────────────────────────── 估值 ─────────────────────────────

    def equity_at(self, close: float) -> float:
        """按给定 `Close` 估值:``cash + shares * close``(ADR-003,ADR-035 钉死)。

        **这是纯函数** —— 不改状态、不缓存、不记录。T3 没有 bar 列表、没有
        「交易日」概念,故本方法只回答「给一个 Close,此刻值多少」;逐日的
        `equity[]` 序列(I3/I6)由聚合根 T5 调用它逐 bar 产出(§4.5)。

        口径提醒(ADR-014 的那处语义冲突):本方法算的是 `equity`,**收盘
        基准**。ADR-014 的「可投资产」是 ``cash + shares * Open_{T+1}`` ——
        同一形状、不同价位、不同用途。T4 要算目标股数时**不该**调用本方法,
        否则会在成交时刻用上一日的 Close 当分母。

        Args:
            close: 估值用的收盘价。必须 ``>= 0``:ADR-019 已保证载入后的
                价格全部 ``> 0``,一个负 Close 到这里只能是算错的中间值,
                而它会静默产出一条负净值曲线。

        Raises:
            ValueError: `close` 为负。
        """
        close = float(close)
        if close < 0.0:
            raise ValueError(
                f"估值用的 Close 不得为负(ADR-019 保证价格 > 0):实得 {close}"
            )
        return self._cash + self._shares * close

    # ───────────────────────────── 杂项 ─────────────────────────────

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return f"Account(cash={self._cash:.2f}, shares={self._shares})"


def _as_int_shares(value: object, *, label: str) -> int:
    """把股数转成 `int`,**拒绝小数股**(ADR-012:「小数股不支持」)。

    `int(3.7)` 会静默截成 3 —— 那是在账本层偷偷做 ADR-012 的 floor 取整,
    而取整属 T4(§4 变更说明把 ADR-012 移到了 T4)。账本收到 3.7 股只能
    说明上游漏了取整,必须报错而不是替它决定。

    `bool` 也被拒:`True` 是 `int` 的子类,`shares=True` 会被当成 1 股。
    """
    if isinstance(value, bool):
        raise ValueError(f"{label} 不得为 bool(ADR-037 要求整数股数):实得 {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(
            f"{label} 必须是整数股(ADR-012:小数股不支持;取整属成交层),"
            f"实得 {value!r}"
        )
    raise ValueError(f"{label} 必须是整数,实得 {value!r}({type(value).__name__})")
