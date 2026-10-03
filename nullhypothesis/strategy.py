"""策略基类与策略文件加载(T2)。

ADR 索引(本模块的每个决定都挂在一条 ADR 上):

* **ADR-036** —— `Strategy` 基类接口,构建期不得变更。本模块逐字实现其签名:
  只读属性 `data` / `cash` / `shares` / `equity`;`init()` / `next()`(零参数)/
  `target(weight)` / `order(shares)`。加载用
  `importlib.util.spec_from_file_location` + `module_from_spec` +
  `spec.loader.exec_module`,**禁止 `exec`/`eval`/`compile`**(关闭 OQ-01)。
  每文件恰好一个 `Strategy` 子类,零个或 2+ 个 → `ValueError` 并指明文件名
  (关闭 OQ-02)。
* **ADR-014** —— 两个正交的下单方法:`target(weight=w)`(目标仓位)与
  `order(shares=n)`(绝对股数)。无重载。
* **ADR-015** —— `weight ∈ [0, 1]` 闭区间;`weight > 1.0` 报错,文案取
  `contracts.yaml` 的 `frozen_text.leverage_not_implemented`。
* **ADR-022** —— 策略抛异常或请求非法值 → 立即终止,错误信息含
  交易日序号 + 日期 + 原始 traceback。
* **ADR-041** —— 同一个 `next()` 内多次调用 `target()`/`order()` → 末次胜出,
  先前调用被丢弃(I4:队列长度 <= 1 的真正来源)。
* **ADR-006** —— 意图在 T 日产生、T+1 开盘成交。本模块只**记录意图**
  (`Strategy.pending_intent`),不解析成股数 —— 解析归 T4 的 `engine.Backtest`
  (ADR-016:`TargetOrder` 入队时只存 `weight`,不存股数)。
* **ADR-035** —— 本模块**被钉死**的公开符号是 `Strategy` 与 `load_strategy`;
  二者均按 ADR-035 实现。`__all__` 另外导出 `TargetIntent` / `ShareIntent` /
  `LEVERAGE_NOT_IMPLEMENTED` —— ADR-035 规定哪些名字**必须存在**,未禁止加性
  地增加,§7 第 1 条允许加性变更。
  *(待上报的 DESIGN.md 缺口,T2 不得单方面修补 —— §7 第 5 条:
  `TargetIntent`/`ShareIntent` 与 `Strategy.pending_intent` 是 T2→T4 的交接
  接缝,T4 的门要读它,而 §4.5 自检规则二要求「下游门读的接缝必须在 ADR-035
  中被钉死为模块级公开名」。它们目前不在 ADR-035 里。)*

**分层边界(ADR-035 / §4.5 的归属矩阵)**:订单队列、成交、`floor` 取整、
「可投资产」分母都归 T4 的 `engine.py`。本模块不 import `engine`,也不计算股数。
"""

from __future__ import annotations

import importlib.util
import inspect
import math
import sys
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import StrategyError

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    import pandas as pd

__all__ = [
    "Strategy",
    "TargetIntent",
    "ShareIntent",
    "load_strategy",
    "LEVERAGE_NOT_IMPLEMENTED",
]


# ───────────────────────── 冻结文案 ─────────────────────────
#
# `contracts.yaml` 的 `frozen_text.leverage_not_implemented` 是这段文案的单一
# 真相来源(ADR-015 / DESIGN.md:284 引用该 key)。这里把它作为**钉死常量**复制
# 一份,生产代码不在运行时读 YAML:
#
#   * contracts.yaml:12 的纪律原文是「**测试**引用本文件的 key,不硬编码」——
#     它约束测试,不约束产品代码;§6 也只把 PyYAML 列为 `CONTRACT_CMD` 的依赖,
#     不是 `nullhypothesis` 包的依赖。让产品代码 import yaml 会凭空加一条依赖。
#   * 漂移由门拦住,不是靠运行时读取:`tests/test_strategy.py` 直接断言消息含
#     `FROZEN["leverage_not_implemented"]`(从 YAML 现读),故本常量与 YAML 一旦
#     分叉,T2 的门立刻红。
#
# (上一版在 import 期读 YAML 并以 `except Exception` 回退到字面量 —— 那恰好
#  反转了目标:contracts.yaml 缺失/改名时会**静默**换用回退值而无门变红。)
LEVERAGE_NOT_IMPLEMENTED: str = "杠杆未实现"


# ───────────────────────── 意图值对象 ─────────────────────────
#
# §1 的实体/值对象表:`TargetOrder` 只持 `weight`,T+1 的解析【不改写它】而是
# 产出一个新的 `Fill`(ADR-016)。故两者 frozen。
#
# 命名:ADR-035 没有把订单值对象的类名钉死在 `strategy.py`(表里只列了
# `Strategy` + `load_strategy`),而 §1 把 `TargetOrder`/`ShareOrder` 归为聚合
# 内部的值对象 —— 聚合根是 T4 的 `engine.Backtest`。为不预占 T4 的名字,本模块
# 用 `TargetIntent`/`ShareIntent`:它们是【策略侧的意图记录】,T4 把它们翻译成
# 自己的队列元素。这不改 ADR-035 的任何钉死名字。


@dataclass(frozen=True)
class TargetIntent:
    """`target(weight=w)` 的意图。只存 `weight`,不存股数(ADR-016)。"""

    weight: float


@dataclass(frozen=True)
class ShareIntent:
    """`order(shares=n)` 的意图。差额就是 `n` 本身(ADR-014)。"""

    shares: int


# ───────────────────────── 阶段取值 ─────────────────────────
#
# `Strategy._phase` 的全部合法取值。用 frozenset 而非 Enum:`_phase` 只在本模块
# 内部比较,Enum 会让 T4/T5 为了调 `_enter_next` 多 import 一个名字,而 ADR-035
# 没把它钉死为公开符号(§4.5 自检规则二 —— 不预占未钉死的跨任务名字)。
_PHASES = frozenset({"created", "init", "ready", "next"})


# ───────────────────────── 只读属性 ─────────────────────────


class _ReadOnly:
    """只读属性描述符(ADR-036:`cash`/`shares`/`equity` 只读)。

    用描述符而非 `@property`,理由:`@property` 定义在基类上时,子类里一句
    `self.cash = 0`(策略作者的常见笔误)抛的是 `AttributeError: can't set
    attribute`,消息里没有「为什么」。本描述符给出指向 ADR-036 的消息。
    """

    def __init__(self, name: str, doc: str) -> None:
        self._name = name
        self.__doc__ = doc

    def __get__(self, obj: Any, objtype: Any = None) -> Any:
        if obj is None:
            return self
        return getattr(obj, f"_{self._name}_value")

    def __set__(self, obj: Any, value: Any) -> None:
        raise AttributeError(
            f"Strategy.{self._name} 是只读属性(ADR-036),不能赋值。"
            f"账本由聚合根 Backtest 持有(§1:Account 不可被聚合外引用);"
            f"要改变仓位请调用 self.target(weight=...) 或 self.order(shares=...)"
            f"(ADR-014)。"
        )

    def __delete__(self, obj: Any) -> None:
        raise AttributeError(
            f"Strategy.{self._name} 是只读属性(ADR-036),不能删除。"
        )


class Strategy:
    """策略基类。签名由 ADR-036 钉死,构建期不得变更。

    用户子类覆写 `init()`(可选)与 `next()`(必需,**零参数**),在 `next()` 内
    读 `self.data` / `self.cash` / `self.shares` / `self.equity`,并调用
    `self.target(weight=...)` 或 `self.order(shares=...)` 表达意图。

    `self.data` 是 `Bars[0..T]` 的**独立副本**(I5)。I5 的诚实边界见 §1:
    框架只保证自己传出去的是副本,**不实现沙箱**。
    """

    # ADR-036 的四个只读属性。`data` 也只读 —— 允许策略重绑 `self.data` 会让
    # I5 的「独立副本」在下一次调用时无从验证。
    data: "pd.DataFrame" = _ReadOnly(  # type: ignore[assignment]
        "data", "bars[0..T] 的独立副本,index 为 Date(ADR-036 / I5)"
    )
    cash: float = _ReadOnly(  # type: ignore[assignment]
        "cash", "现金,只读(ADR-036)"
    )
    shares: int = _ReadOnly(  # type: ignore[assignment]
        "shares", "持股数,只读,>= 0(ADR-036 / I2)"
    )
    equity: float = _ReadOnly(  # type: ignore[assignment]
        "equity",
        "cash + shares * data['Close'].iloc[-1](ADR-003 的收盘基准)。"
        "注意:**不是** ADR-014 的「可投资产」(开盘基准)。"
        "init() 时 data 为空,故 equity == cash(ADR-036)",
    )

    # ── 生命周期状态(引擎侧写入;下划线前缀 = 非 ADR-036 的公开契约面)──
    #
    # 为什么是类属性而不是只在 `_bind_bar` 里赋值:策略作者的 `__init__` 若忘了
    # 调 `super().__init__()`,实例上就没有这些名字,而 `next()` 里读 `self.cash`
    # 会抛 `AttributeError` —— 那个 traceback 指向框架内部,不指向真正的原因。
    _data_value: Any = None
    _cash_value: float = 0.0
    _shares_value: int = 0
    _equity_value: float = 0.0
    # 阶段机:取值恰为 `_PHASES`,迁移为
    #     created --_enter_init--> init --_exit_init--> ready
    #     ready   --_enter_next--> next --_exit_next--> ready   (每根 K 线一轮)
    # `ready` 是「已 init、不在任何 next() 内」的静止态,也是 `_require_trading_phase`
    # 在 bar 之外拒绝下单时实际看到的值。
    # 注:这**不是** §1 的聚合生命周期(`created -> running -> finished`)——
    # 那是 `engine.Backtest` 的状态;本机描述的是单个 `Strategy` 实例的回调阶段,
    # 故没有 `finished`(策略实例不自己终止,引擎跑完就不再调它)。
    _phase: str = "created"
    _bar_index: int = -1
    _bar_date: str = ""
    _source_file: str = "<unknown>"
    _strategy_name: str = "<unknown>"
    _pending_intent: TargetIntent | ShareIntent | None = None

    # ─────────────────── 用户覆写面(ADR-036) ───────────────────

    def init(self) -> None:
        """可选。第 0 根 K 线之前调用**恰好一次**(ADR-036)。

        此时 `self.data` 为**零行** DataFrame(列齐全)、`shares == 0`、
        `equity == cash`。在此调用 `target()`/`order()` → `ValueError`。
        """

    def next(self) -> None:
        """必需,**零参数**。每根 K 线收盘后调用一次(ADR-036)。

        通过 `self.data` 读取截至今日的历史;移动均线即
        `self.data['Close'].rolling(n).mean()`。
        """
        raise NotImplementedError(
            f"策略 {self._strategy_name} 未实现 next()。ADR-036 规定 next() 必需,"
            f"且签名为零参数 def next(self) -> None。"
        )

    # ─────────────────── 下单接口(ADR-014) ───────────────────

    def target(self, weight: float) -> None:
        """目标仓位。分母是**可投资产**(ADR-014 的定义,不是 `equity`)。

        `weight ∈ [0, 1]` 闭区间(ADR-015);上界外报错,文案取
        `contracts.yaml` 的 `frozen_text.leverage_not_implemented`。

        引擎在 **T+1 开盘**时算
        `目标股数 = floor(w * 可投资产 / Open_{T+1})`,减当前股数得差额
        (ADR-016,归 T4)。本方法只记录意图。
        """
        self._require_trading_phase("target")
        self._set_intent(TargetIntent(weight=self._validate_weight(weight)))

    def order(self, shares: int) -> None:
        """绝对股数。`n > 0` 买、`n < 0` 卖;差额就是 `n` 本身(ADR-014)。

        `n < 0` 使 `shares` 变负时由引擎按 ADR-009 报错(守护 I2)—— 那需要
        当前持仓,属 T4 的 `engine` 层。本方法只校验 `n` 本身是合法整数。
        """
        self._require_trading_phase("order")
        self._set_intent(ShareIntent(shares=self._validate_shares(shares)))

    # ─────────────────── 校验(ADR-015 / ADR-022) ───────────────────

    # 关于 `_validate_weight` / `_validate_shares` 的重复(审查 J3):两者的
    # None/bool 前缀形状相同,但数值尾部受**不同** ADR 约束 —— weight 是
    # ADR-015 的闭区间 [0,1](接受小数),shares 是 ADR-012 的整数股。抽取
    # 公共前缀需要把参数名与提示文案参数化,结果是每条消息都要在 f-string 里
    # 拼装,反而让「哪条 ADR 拒绝了这个值」更难追溯。故**有意保留**两段平行
    # 的显式阶梯:每个 raise 点字面写出自己的 ADR 编号(§7 第 5 条要求实现
    # 时引用 ADR 编号)。这是经权衡的选择,不是疏漏。

    def _validate_weight(self, weight: Any) -> float:
        """ADR-015 的闭区间 + ADR-022 的「非法值立即终止」。

        非法:`None` / 非数值(字符串等)/ `bool` / `NaN` / `inf` / `< 0` /
        `> 1`。合法边界:`0.0` 与 `1.0` **必须通过**(闭区间)。
        """
        if weight is None:
            raise self._illegal_value(
                "target(weight=None):weight 不能为 None(ADR-022)"
            )
        # bool 是 int 的子类,`target(weight=True)` 会被 float() 吞成 1.0 ——
        # 那是作者笔误,不是 100% 仓位的意图。
        if isinstance(weight, bool):
            raise self._illegal_value(
                f"target(weight={weight!r}):weight 不能为 bool。"
                f"若要满仓请写 weight=1.0(ADR-015)"
            )
        if not isinstance(weight, (int, float)):
            raise self._illegal_value(
                f"target(weight={weight!r}):weight 必须是数值,"
                f"实际为 {type(weight).__name__}(ADR-022)"
            )
        value = float(weight)
        if math.isnan(value):
            raise self._illegal_value(
                "target(weight=NaN):weight 不能为 NaN(ADR-022)"
            )
        if math.isinf(value):
            raise self._illegal_value(
                f"target(weight={value}):weight 不能为无穷(ADR-022)"
            )
        if value < 0.0:
            raise self._illegal_value(
                f"target(weight={value}):weight 不能为负 —— "
                f"v0 仅多头,做空不支持(ADR-009/ADR-015)"
            )
        if value > 1.0:
            # ADR-015 的上界。闭区间:weight == 1.0 必须【不】走到这里。
            raise self._illegal_value(
                f"target(weight={value}):{LEVERAGE_NOT_IMPLEMENTED} —— "
                f"weight 的取值范围是闭区间 [0, 1](ADR-015)"
            )
        return value

    def _validate_shares(self, shares: Any) -> int:
        """`order(shares=n)` 的 `n` 必须是整数(ADR-012:不支持小数股)。"""
        if shares is None:
            raise self._illegal_value(
                "order(shares=None):shares 不能为 None(ADR-022)"
            )
        if isinstance(shares, bool):
            raise self._illegal_value(
                f"order(shares={shares!r}):shares 不能为 bool(ADR-022)"
            )
        if isinstance(shares, int):
            return int(shares)
        if isinstance(shares, float):
            if math.isnan(shares):
                raise self._illegal_value(
                    "order(shares=NaN):shares 不能为 NaN(ADR-022)"
                )
            if math.isinf(shares):
                raise self._illegal_value(
                    f"order(shares={shares}):shares 不能为无穷(ADR-022)"
                )
            if not float(shares).is_integer():
                raise self._illegal_value(
                    f"order(shares={shares}):不支持小数股(ADR-012)"
                )
            return int(shares)
        raise self._illegal_value(
            f"order(shares={shares!r}):shares 必须是整数,"
            f"实际为 {type(shares).__name__}(ADR-022)"
        )

    def _illegal_value(self, detail: str) -> StrategyError:
        """把非法值包成 `StrategyError`,带上 ADR-022 要求的三件事。

        三件事 = 交易日序号 + 日期 + **原始 traceback**。非法值的「原始
        traceback」是**策略源文件里发出这次调用的那一行** —— 我们从当前调用栈
        里摘出属于策略文件的帧,格式与 ADR-022 的样例一致(`at <file>:<line>`)。
        """
        return StrategyError(
            self._format_error_message(detail, self._strategy_call_site()),
            strategy=self._strategy_name,
            bar_index=self._bar_index,
            date=self._bar_date,
        )

    def _strategy_call_site(self) -> str:
        """返回策略源文件中触发本次调用的 traceback 行(ADR-022)。

        为什么必须是策略文件的帧:ADR-036 禁止 `exec`/`eval`/`compile`
        正是为了让行号指向真实文件而非 `<string>`(OQ-01 的关闭理由)。若
        指向框架内部,那条理由就白费了。
        """
        frames = traceback.extract_stack()
        source = self._source_file
        for frame in reversed(frames):
            if source != "<unknown>" and frame.filename == source:
                return f"at {frame.filename}:{frame.lineno}"
        # 策略直接调用(非从文件加载,例如测试里内联定义的子类)时无匹配帧;
        # 退回到紧邻框架之外的那一帧,仍然指向真实的调用点。
        framework = str(Path(__file__).resolve())
        for frame in reversed(frames):
            if frame.filename != framework:
                return f"at {frame.filename}:{frame.lineno}"
        return "at <unknown>:0"

    def _format_error_message(self, detail: str, traceback_line: str) -> str:
        """ADR-022 的消息布局:交易日序号 + 日期 + 原始 traceback。"""
        return (
            f"策略 {self._strategy_name} 在第 {self._bar_index} 个交易日 "
            f"({self._bar_date}) 请求非法值\n"
            f"   └─ {detail}\n"
            f"      {traceback_line}"
        )

    # ─────────────────── 阶段守卫(ADR-036) ───────────────────

    def _require_trading_phase(self, method: str) -> None:
        """ADR-036:在 `init()` 内调用 `target()`/`order()` → `ValueError`。

        `ValueError` 而非 `StrategyError` 是 ADR-036 的字面规定 —— 这是**协议
        误用**(策略作者在没有 bar 的时刻表达了针对某根 bar 的意图),不是
        ADR-022 的「运行期非法值」。
        """
        if self._phase == "init":
            raise ValueError(
                f"不能在 init() 内调用 {method}()(ADR-036)。"
                f"init() 在第 0 根 K 线之前运行,此时 data 为零行、"
                f"没有可下单的交易日。请把下单逻辑放进 next()。"
            )
        if self._phase != "next":
            raise ValueError(
                f"只能在 next() 内调用 {method}()(ADR-036),"
                f"当前阶段为 {self._phase!r}。"
            )

    # ─────────────────── 引擎侧接缝(供 T4/T5 使用) ───────────────────
    #
    # 下划线前缀:ADR-035 把 `strategy.py` 的公开契约面钉死为 `Strategy` 与
    # `load_strategy`,ADR-036 把 `Strategy` 的公开成员钉死为四个属性 + 四个
    # 方法。下面是**加性**的引擎侧接缝,不改动任何被钉死的名字。
    #
    # 为什么由 T2 提供而不是 T4 自己往实例上塞属性:ADR-022 要求错误消息含
    # 「交易日序号 + 日期」,而发现非法值的地方是 `Strategy.target()`(T2)。
    # 序号与日期只能由推进时间的一侧(T4)告知 —— 这就是接缝存在的理由。

    def _bind_account(self, *, cash: float, shares: int, equity: float) -> None:
        """由引擎在每次回调前写入账本快照。只读属性读的就是这三个值。"""
        self._cash_value = float(cash)
        self._shares_value = int(shares)
        self._equity_value = float(equity)

    def _bind_data(self, data: Any) -> None:
        """由引擎写入 `Bars[0..T]` 的**独立副本**(I5)。副本由引擎负责。"""
        self._data_value = data

    def _bind_identity(self, *, source_file: str, strategy_name: str) -> None:
        """把「我来自哪个文件 / 叫什么」绑到**实例**上(审查 J2)。

        `load_strategy` 另外把同样两个值写在**类**上,作为向后兼容的默认值 ——
        T4/T5 直接实例化而不调本接缝时,ADR-022 的消息仍然有名字可打。但类属性
        是共享状态:策略文件 A 若 `import` 并复用了别处定义的子类,两次 load 的
        `_source_file` 可能互相覆盖。实例绑定优先级更高(属性查找先看实例),
        故调过本方法的实例永远读到自己那一次 load 的身份。
        """
        self._source_file = source_file
        self._strategy_name = strategy_name

    def _enter_init(self, *, data: Any, cash: float) -> None:
        """进入 `init()` 阶段(ADR-036)。

        `data` 为零行 DataFrame、`shares == 0`、`equity == cash`。
        """
        if self._phase != "created":
            raise ValueError(
                f"init() 只能调用一次(ADR-036),当前阶段为 {self._phase!r}。"
            )
        self._bind_data(data)
        self._bind_account(cash=cash, shares=0, equity=cash)
        self._phase = "init"
        self._bar_index = -1
        self._bar_date = "<init>"

    def _exit_init(self) -> None:
        self._phase = "ready"

    def _enter_next(self, *, bar_index: int, date: str) -> None:
        """进入第 `bar_index` 根 K 线的 `next()` 阶段。

        ADR-041:每根 K 线开始时清空意图 —— 末次胜出意味着上一根的意图已由
        引擎取走并入队,不得跨 bar 残留。
        """
        self._phase = "next"
        self._bar_index = bar_index
        self._bar_date = date
        self._pending_intent = None

    def _exit_next(self) -> None:
        self._phase = "ready"

    @property
    def pending_intent(self) -> TargetIntent | ShareIntent | None:
        """本次 `next()` 内**末次**调用留下的意图(ADR-041:末次胜出)。

        引擎在 `next()` 返回后读它:`None` 表示本根 K 线没有意图(不入队);
        否则恰好一个意图 —— 这是 I4(队列长度 <= 1)在策略侧的来源。
        先前的调用被**丢弃**(ADR-041),而非排队。
        """
        return self._pending_intent

    def _set_intent(self, intent: TargetIntent | ShareIntent) -> None:
        # ADR-041:直接覆盖。不 append —— 队列里永远只有一个订单(I4)。
        self._pending_intent = intent


# ───────────────────────── 加载 ─────────────────────────


def _assert_zero_arg_callbacks(cls: type, path: Path) -> None:
    """ADR-036:`init()` 与 `next()` 均以**零参数**调用。

    名字用复数 `callbacks` 而非 `next` —— 下面的循环同时检查两个回调,
    叫 `_assert_zero_arg_next` 会把实际范围说窄。

    `def next(self, bar)` 的策略文件必须**报错而非被当作合法** —— 否则引擎调用
    `next()` 会抛 `TypeError`,而那个 TypeError 发生在第 0 根 K 线、被 ADR-022
    包成 StrategyError,作者会去找「第 0 个交易日的非法值」而不是去改签名。
    在加载期检查,错误指向签名本身。
    """
    for name in ("next", "init"):
        func = cls.__dict__.get(name)
        if func is None:
            # 继承自基类的实现;基类签名本就正确。
            continue
        if not callable(func):
            raise ValueError(
                f"{path}:{cls.__name__}.{name} 不是可调用对象 —— "
                f"ADR-036 规定它是方法。"
            )
        try:
            sig = inspect.signature(func)
        except (TypeError, ValueError):  # pragma: no cover - 极少见的内建包装
            continue
        extra = [
            p
            for p in list(sig.parameters.values())[1:]  # 跳过 self
            if p.kind
            in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
            and p.default is p.empty
        ]
        if extra:
            names = ", ".join(p.name for p in extra)
            raise ValueError(
                f"{path}:{cls.__name__}.{name}() 的签名多了必需参数 "
                f"({names})。ADR-036 规定 {name}() **零参数**"
                f"(def {name}(self) -> None);策略通过 self.data 读取"
                f"截至今日的历史,而不是从参数接收 bar。"
            )


def _discover_subclass(module: Any, path: Path) -> type:
    """每文件恰好一个 `Strategy` 子类(ADR-036,关闭 OQ-02)。

    零个或 2+ 个 → `ValueError` 并**指明文件名**。静默取第一个会污染阶段二的
    `GET /api/strategies`(ADR-036 的条款理由)。
    """
    found: list[type] = []
    for name, obj in vars(module).items():
        # 不按名字过滤:ADR-036「策略类**可任意命名**」—— 跳过下划线前缀会让
        # `class _Impl(Strategy)` 被判成「零个子类」,那是在 ADR-036 之外
        # 发明一条命名约定。只按 `__module__` 过滤(见下)。
        if name.startswith("__"):
            continue  # __builtins__ 等 import 机制注入的名字
        if not isinstance(obj, type):
            continue
        if obj is Strategy:
            # 基类自身被 import 进策略文件是正常写法,不计数。
            continue
        if not issubclass(obj, Strategy):
            continue
        # `from other_strategy import Foo` 把别处的子类带进本模块的命名空间 ——
        # 只认在本文件里定义的那些(`__module__` 等于本模块名)。
        if getattr(obj, "__module__", None) != module.__name__:
            continue
        if obj not in found:
            found.append(obj)

    if not found:
        raise ValueError(
            f"{path}:文件中没有 Strategy 子类。ADR-036 规定每个策略文件"
            f"恰好一个 Strategy 子类(类名可任意)。请写"
            f"`from nullhypothesis.strategy import Strategy` 并定义"
            f"`class MyStrategy(Strategy):`。"
        )
    if len(found) > 1:
        names = ", ".join(c.__name__ for c in found)
        raise ValueError(
            f"{path}:文件中有 {len(found)} 个 Strategy 子类({names})。"
            f"ADR-036 规定每个策略文件**恰好一个** —— 阶段二的"
            f"`GET /api/strategies` 返回文件名,隐含「一文件一策略」。"
            f"请把它们拆到不同文件。"
        )
    return found[0]


def load_strategy(path: str | Path) -> type:
    """从 `.py` 文件加载唯一的 `Strategy` 子类(ADR-036)。

    返回**类**而非实例 —— 引擎每次运行自己实例化(T5 的多策略隔离门要求
    `run([A, A_copy])` 产生两条独立曲线)。

    加载方式由 ADR-036 钉死:`importlib.util.spec_from_file_location` +
    `module_from_spec` + `spec.loader.exec_module`。**不使用**
    `exec`/`eval`/`compile`(关闭 OQ-01)—— 理由是 `exec` 会让 traceback 显示
    `<string>` 而非文件行号,违反 ADR-022 对「原始 traceback」的要求。

    Raises:
        ValueError: 文件不存在 / 不是 `.py` / 零个或 2+ 个 `Strategy` 子类 /
            `next()` 签名带必需参数。消息均含文件名。
        StrategyError: 策略文件在 import 期抛出异常(ADR-022)。
    """
    path = Path(path)
    if not path.exists():
        raise ValueError(f"{path}:策略文件不存在(ADR-039:--strategy 的文件须存在)")
    if path.is_dir():
        raise ValueError(f"{path}:策略路径是目录,不是 .py 文件")
    if path.suffix != ".py":
        raise ValueError(f"{path}:策略文件必须是 .py(ADR-036 的加载方式)")

    resolved = path.resolve()
    stem = resolved.stem  # ADR-037:<stem> ≔ 策略文件名去 .py

    # 模块名加一段随机后缀:T5 的「多策略隔离」门用两个**内容相同、文件名不同**
    # 的策略文件断言产生两条独立曲线。若模块名只取 stem,`sys.modules` 的缓存会
    # 让第二次加载拿到第一个模块对象,模块级可变状态因此串台。
    module_name = f"nullhypothesis._strategies.{stem}_{uuid.uuid4().hex}"

    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise ValueError(
            f"{resolved}:无法为该文件创建 import spec"
            f"(ADR-036 的 spec_from_file_location 返回 None)"
        )
    module = importlib.util.module_from_spec(spec)
    # 注册进 sys.modules:dataclass / typing.get_type_hints / pickle 都会按
    # `__module__` 回查,策略文件里用 @dataclass 时缺这一步会在 import 期炸。
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:
        sys.modules.pop(module_name, None)
        # import 期异常也归 ADR-022:带文件名 + 原始 traceback。此刻还没有
        # 交易日,故 bar_index = -1、date = "<import>"。
        raise StrategyError(
            f"策略文件 {resolved} 在加载(import)期抛出异常\n"
            f"   └─ {type(exc).__name__}: {exc}\n"
            f"      {''.join(traceback.format_exception_only(type(exc), exc)).strip()}",
            strategy=stem,
            bar_index=-1,
            date="<import>",
        ) from exc

    try:
        cls = _discover_subclass(module, resolved)
        _assert_zero_arg_callbacks(cls, resolved)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise

    # 让 ADR-022 的错误消息能指向真实源文件与行号(ADR-036 禁 exec 的理由)。
    #
    # 写在**类**上:`load_strategy` 按 ADR-035 返回类而非实例(引擎每次运行自己
    # 实例化,T5 的 `run([A, A_copy])` 要两条独立曲线),此刻还没有实例可绑。
    # 每次 load 用 uuid 后缀的模块名,故同一文件两次 load 得到两个不同的类对象,
    # 这两行不会互相覆盖。引擎若想要实例级身份(更强的隔离),调 `_bind_identity`。
    cls._source_file = str(resolved)
    cls._strategy_name = stem
    return cls
