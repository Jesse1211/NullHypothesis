"""T2 验收门 · 策略加载与协议。

对应 DESIGN.md §5 的 T2 门,逐条。ADR:006 / 014 / 015 / 022 / 035 / 036 / 041。

**为什么这里有一个 `_Driver` 而不是 import `engine`**:T2 的门有三条需要「时间在
推进」才能断言 —— 非法值的消息要含**交易日序号 + 日期**(ADR-022)、`init()` 恰调
一次且在首次 `next()` 之前(ADR-036)、I5 的切片长度 `== T+1`。而 §4.5 的归属矩阵
把主循环归 **T5 的 `engine.run`**,ADR-035 的 DAG 里 T2 的依赖是 `—`(无)。
所以门不能 import `engine.py`(T2 开工时它不存在,import 会把 T2 的门变成
「等 T5 完成才能跑」—— 正是 DESIGN.md 反复点名的跨任务顺序错误)。

`_Driver` 是测试内的**最小驱动**:只做 ADR-002 的两行顺序中属于策略侧的那半
(逐根 bar 调 `next()`、喂 `Bars[0..T]` 的副本、把异常包成 `StrategyError`),
**不含订单队列、不含成交、不算股数** —— 那些是 T4 的门。

真实状态要求:每个加载测试都把策略**真写到磁盘的 .py 文件**,经
`load_strategy` 加载(ADR-036 的加载方式只能在真实文件上验证)。
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
import tempfile
import textwrap
import traceback
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nullhypothesis.errors import StrategyError  # noqa: E402
from nullhypothesis.strategy import (  # noqa: E402
    ShareIntent,
    Strategy,
    TargetIntent,
    load_strategy,
)

CONTRACTS = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))
FROZEN = CONTRACTS["frozen_text"]
DATE_FMT = CONTRACTS["formats"]["date"]


# ═══════════════════════ 测试用最小驱动 ═══════════════════════


def make_bars(n: int = 5, *, start_close: float = 100.0) -> pd.DataFrame:
    """n 根 K 线。**四价互异且逐根不同**,使任何错位都必然失败(I7 的同一手法)。"""
    rows = []
    dates = pd.date_range("2020-01-02", periods=n, freq="D")
    for i in range(n):
        base = start_close + i * 10.0
        rows.append(
            {
                "Date": dates[i],
                "Open": base + 1.0,
                "High": base + 3.0,
                "Low": base - 3.0,
                "Close": base,
                "Volume": 1000 + i,
            }
        )
    return pd.DataFrame(rows)


EMPTY_BARS = pd.DataFrame(
    columns=["Date", "Open", "High", "Low", "Close", "Volume"]
).astype(
    {"Open": float, "High": float, "Low": float, "Close": float, "Volume": int}
)


class _Driver:
    """最小驱动。只负责策略侧的回调协议(ADR-036),不做成交(那是 T4)。"""

    def __init__(
        self,
        cls: type,
        df: pd.DataFrame,
        *,
        cash: float = 10_000.0,
        shares: int = 0,
    ) -> None:
        self.cls = cls
        self.df = df.reset_index(drop=True)
        self.cash = float(cash)
        self.shares = int(shares)
        self.intents: list[object] = []
        self.strategy: Strategy | None = None

    def _date_of(self, i: int) -> str:
        return pd.Timestamp(self.df.loc[i, "Date"]).strftime(DATE_FMT)

    def _equity(self, df_slice: pd.DataFrame) -> float:
        # ADR-036 的 equity 口径:cash + shares * data['Close'].iloc[-1]。
        # data 为空(init 期)时退化为 cash。
        if len(df_slice) == 0:
            return self.cash
        return self.cash + self.shares * float(df_slice["Close"].iloc[-1])

    def run(self) -> Strategy:
        strat = self.cls()
        self.strategy = strat

        # ── init():第 0 根 K 线【之前】,data 为零行 DataFrame ──
        empty = EMPTY_BARS.copy()
        strat._enter_init(data=empty, cash=self.cash)
        try:
            strat.init()
        except ValueError:
            raise
        except Exception as exc:
            raise self._wrap(exc, -1, "<init>", strat) from exc
        strat._exit_init()

        # ── 逐根 next() ──
        for i in range(len(self.df)):
            # I5:Bars[0..i] 的独立副本
            sl = self.df.iloc[: i + 1].copy(deep=True)
            strat._bind_data(sl)
            strat._bind_account(
                cash=self.cash, shares=self.shares, equity=self._equity(sl)
            )
            strat._enter_next(bar_index=i, date=self._date_of(i))
            try:
                strat.next()
            except StrategyError:
                raise
            except Exception as exc:
                raise self._wrap(exc, i, self._date_of(i), strat) from exc
            strat._exit_next()
            # ADR-041:末次胜出 —— 本根 K 线最多留下一个意图(I4 的来源)
            self.intents.append(strat.pending_intent)
        return strat

    @staticmethod
    def _wrap(exc: Exception, bar_index: int, date: str, strat: Strategy) -> StrategyError:
        """ADR-022:消息含交易日序号 + 日期 + 原始 traceback(含源文件行号)。"""
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return StrategyError(
            f"策略 {strat._strategy_name} 在第 {bar_index} 个交易日 ({date}) 抛出异常\n"
            f"   └─ {type(exc).__name__}: {exc}\n{tb}",
            strategy=strat._strategy_name,
            bar_index=bar_index,
            date=date,
        )


HEAD = "from nullhypothesis.strategy import Strategy\n"


def write_strategy(tmp_path: Path, name: str, body: str, *, head: bool = True) -> Path:
    """把策略源码**真写到磁盘**,返回路径。

    `dedent` 必须作用在【缩进的那一段】上:若先拼接 HEAD(顶格)再 dedent,
    公共前缀为空、整段不被 dedent,写出去的文件是 IndentationError。
    """
    text = textwrap.dedent(body).lstrip("\n")
    if head and "import Strategy" not in text:
        text = HEAD + text
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ═══════════ 门:target() 与 order() 各有测试(ADR-014) ═══════════


def test_target_records_weight_intent(tmp_path):
    """ADR-014:`target(weight=w)` 记录目标仓位意图,只存 weight(ADR-016)。"""
    p = write_strategy(tmp_path, "t.py", """
        class S(Strategy):
            def next(self):
                self.target(weight=0.5)
    """)
    d = _Driver(load_strategy(p), make_bars(3))
    d.run()
    assert d.intents == [TargetIntent(0.5)] * 3
    # ADR-016:入队时【只存意图】,不存股数 —— 意图对象上没有 shares 字段
    assert not hasattr(d.intents[0], "shares")


def test_order_records_share_intent(tmp_path):
    """ADR-014:`order(shares=n)` 的差额就是 n 本身。两方法语义正交,无重载。"""
    p = write_strategy(tmp_path, "t.py", """
        class S(Strategy):
            def next(self):
                self.order(shares=7)
    """)
    d = _Driver(load_strategy(p), make_bars(2))
    d.run()
    assert d.intents == [ShareIntent(7)] * 2
    assert not hasattr(d.intents[0], "weight")


def test_target_and_order_are_distinct_types(tmp_path):
    """ADR-014「两者语义正交,无重载」—— 单参数重载是 ADR-011,已被取代。"""
    p = write_strategy(tmp_path, "t.py", """
        class S(Strategy):
            def next(self):
                if self.data.index[-1] == 0:
                    self.target(weight=1.0)
                else:
                    self.order(shares=-3)
    """)
    d = _Driver(load_strategy(p), make_bars(2))
    d.run()
    assert isinstance(d.intents[0], TargetIntent)
    assert isinstance(d.intents[1], ShareIntent)
    assert type(d.intents[0]) is not type(d.intents[1])


# ═══════ 门:非法值逐个成立,消息含交易日序号与日期(ADR-022) ═══════

ILLEGAL_WEIGHTS = {
    "nan": "float('nan')",
    "none": "None",
    "negative": "-0.1",
    "leverage": "1.5",
    "string": "'0.5'",
}


@pytest.mark.parametrize("label,literal", sorted(ILLEGAL_WEIGHTS.items()))
def test_illegal_weight_raises_with_bar_index_and_date(tmp_path, label, literal):
    """ADR-022:NaN / None / 负 / >1 / 字符串 **各自**报错,消息含序号 + 日期。

    刻意让策略在**第 2 根**(index 2)才犯错 —— 若实现把序号写死成 0 或用
    `len(data)` 当序号,这条就红。
    """
    p = write_strategy(tmp_path, f"bad_{label}.py", f"""
        class S(Strategy):
            def next(self):
                if self.data.index[-1] == 2:
                    self.target(weight={literal})
    """)
    df = make_bars(5)
    expected_date = pd.Timestamp(df.loc[2, "Date"]).strftime(DATE_FMT)

    with pytest.raises(StrategyError) as ei:
        _Driver(load_strategy(p), df).run()

    err = ei.value
    msg = str(err)
    # 结构化字段(ADR-040)
    assert err.bar_index == 2, f"bar_index 错:{err.bar_index}"
    assert err.date == expected_date
    assert err.strategy == f"bad_{label}"
    # 消息文本(ADR-022:交易日序号 + 日期)
    assert "2" in msg and expected_date in msg
    assert re.search(r"第\s*2\s*个交易日", msg), msg
    # ADR-022 的第三件事:原始 traceback —— 必须指向【策略源文件】的行号。
    # ADR-036 禁 exec 的全部理由就在这里:exec 会显示 `<string>`。
    assert str(p.resolve()) in msg, f"消息未指向策略源文件:{msg}"
    assert "<string>" not in msg, "traceback 指向 <string> —— 说明用了 exec(ADR-036 禁止)"
    line_match = re.search(rf"{re.escape(str(p.resolve()))}:(\d+)", msg)
    assert line_match, f"消息里没有 <文件>:<行号>:{msg}"
    # 行号必须是真正发出调用的那一行(第 5 行:`self.target(...)`)
    bad_line = next(
        i for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "self.target(" in ln
    )
    assert int(line_match.group(1)) == bad_line, (
        f"traceback 行号 {line_match.group(1)} != 实际犯错行 {bad_line}"
    )


def test_leverage_weight_message_contains_frozen_text(tmp_path):
    """ADR-015:`weight=1.5` 的消息须含 `frozen_text.leverage_not_implemented`。

    文案从 contracts.yaml 读取,不硬编码(§4.5 的构建期纪律)。
    """
    p = write_strategy(tmp_path, "lev.py", """
        class S(Strategy):
            def next(self):
                self.target(weight=1.5)
    """)
    with pytest.raises(StrategyError) as ei:
        _Driver(load_strategy(p), make_bars(2)).run()
    assert FROZEN["leverage_not_implemented"] in str(ei.value)


@pytest.mark.parametrize("weight", [1.0, 0.0])
def test_closed_interval_boundaries_do_not_raise(tmp_path, weight):
    """ADR-015 的闭区间:`weight=1.0` 与 `weight=0.0` **必须不报错**。

    `if weight >= 1: raise` 的 off-by-one 能通过所有其他 T2 条款,却会让 T4 的
    `fixtures.adr016_fill_at_next_open`(weight=1.0)失败 —— 而那个失败会被
    归因到错误的 ADR。这条门就是为了让它在 T2 就红。
    """
    p = write_strategy(tmp_path, "edge.py", f"""
        class S(Strategy):
            def next(self):
                self.target(weight={weight})
    """)
    d = _Driver(load_strategy(p), make_bars(3))
    d.run()  # 不得抛
    assert d.intents == [TargetIntent(weight)] * 3


def test_adr016_fixture_weight_is_accepted():
    """直接用 T4 的 fixture 值喂进来 —— 它必须被 T2 接受,否则 T4 无法开工。"""
    fx = CONTRACTS["fixtures"]["adr016_fill_at_next_open"]

    class S(Strategy):
        def next(self) -> None:
            self.target(weight=fx["weight"])

    d = _Driver(S, make_bars(2), cash=fx["cash"])
    d.run()
    assert d.intents[-1] == TargetIntent(float(fx["weight"]))


@pytest.mark.parametrize("weight", [1.0000001, 2, 100.0, float("inf")])
def test_above_upper_bound_raises(tmp_path, weight):
    """闭区间的上界之外 —— 含刚刚越界的 1.0000001(拦住 `round` 后再比较)。"""
    class S(Strategy):
        def next(self) -> None:
            self.target(weight=weight)

    with pytest.raises(StrategyError):
        _Driver(S, make_bars(2)).run()


@pytest.mark.parametrize("weight", [True, False])
def test_bool_weight_is_rejected(weight):
    """`bool` 是 `int` 的子类,`float(True) == 1.0` 会被静默当成满仓。

    ADR-022 把「非法值」与「立即终止」绑定;作者写 `target(weight=True)` 是笔误,
    静默接受会产出一条看似可信的曲线 —— 正是 ADR-022 要避开的失真。
    """
    class S(Strategy):
        def next(self) -> None:
            self.target(weight=weight)

    with pytest.raises(StrategyError):
        _Driver(S, make_bars(2)).run()


@pytest.mark.parametrize("bad", [None, "3", float("nan"), 1.5])
def test_illegal_order_shares_raise(bad):
    """`order(shares=n)` 的非法 n 同样按 ADR-022 立即终止。

    1.5 股:ADR-012 不支持小数股。
    """
    class S(Strategy):
        def next(self) -> None:
            self.order(shares=bad)

    with pytest.raises(StrategyError) as ei:
        _Driver(S, make_bars(2)).run()
    assert ei.value.bar_index == 0


def test_negative_order_shares_is_allowed_at_strategy_layer():
    """分层:`order(shares=-999)` 的 I2 守卫归 **T4**(需当前持仓才能判断)。

    T2 只校验 n 本身是整数。T4 的门「I2 经 order()」断言持仓 100 股时
    `order(-999)` 报错 —— 若 T2 在这里就拒绝所有负数,T4 那条门测不到真正的
    不变式(它会在到达账本前就被挡掉),且 `order()` 的 ADR-014 语义
    (「n<0 卖」)会消失。
    """
    class S(Strategy):
        def next(self) -> None:
            self.order(shares=-999)

    d = _Driver(S, make_bars(1))
    d.run()
    assert d.intents == [ShareIntent(-999)]


# ═══════════ 门:ADR-036 的属性契约(可读 + 数值正确 + 只读) ═══════════


def test_readonly_attributes_have_correct_values_in_next():
    """`fixtures.t2_strategy_equity`:cash / shares / close → expect_equity。"""
    fx = CONTRACTS["fixtures"]["t2_strategy_equity"]
    seen: dict[str, object] = {}

    class S(Strategy):
        def next(self) -> None:
            seen["cash"] = self.cash
            seen["shares"] = self.shares
            seen["equity"] = self.equity
            seen["close"] = float(self.data["Close"].iloc[-1])

    df = pd.DataFrame(
        [{
            "Date": pd.Timestamp("2020-01-02"),
            "Open": 111.0, "High": 130.0, "Low": 90.0,
            "Close": fx["close"], "Volume": 1,
        }]
    )
    _Driver(S, df, cash=fx["cash"], shares=fx["shares"]).run()

    assert seen["cash"] == pytest.approx(fx["cash"])
    assert seen["shares"] == fx["shares"]
    assert seen["close"] == pytest.approx(fx["close"])
    assert seen["equity"] == pytest.approx(fx["expect_equity"]), (
        "equity 口径错。ADR-036:== cash + shares * data['Close'].iloc[-1]"
    )
    # 显式反断言:equity 不得用 Open 估值(那是 ADR-014 的「可投资产」,
    # 不同的量 —— ADR-036 明文区分二者)
    assert seen["equity"] != pytest.approx(fx["cash"] + fx["shares"] * 111.0)


@pytest.mark.parametrize("attr", ["cash", "shares", "equity", "data"])
def test_readonly_attributes_reject_assignment(attr):
    """ADR-036:断言**写入**三者报错(只读)。`data` 同理(I5 的副本语义)。"""
    captured: dict[str, BaseException] = {}

    class S(Strategy):
        def next(self) -> None:
            try:
                setattr(self, attr, 0 if attr != "data" else pd.DataFrame())
            except BaseException as exc:  # noqa: BLE001
                captured["exc"] = exc

    _Driver(S, make_bars(1)).run()
    assert isinstance(captured.get("exc"), AttributeError), (
        f"self.{attr} = ... 没有报错 —— ADR-036 规定它只读"
    )
    assert "ADR-036" in str(captured["exc"])


def test_readonly_attributes_track_account_changes():
    """只读不等于常量:账本推进时三者必须跟着变(否则「只读」退化为写死)。"""
    log: list[tuple[float, int, float]] = []

    class S(Strategy):
        def next(self) -> None:
            log.append((self.cash, self.shares, self.equity))

    df = make_bars(3)
    # 在第 1 根之后改账本,模拟一次成交(真正产生它是 T4 的事)
    d2 = _Driver(S, df, cash=10_000.0, shares=0)
    strat = S()
    d2.strategy = strat
    strat._enter_init(data=EMPTY_BARS.copy(), cash=10_000.0)
    strat._exit_init()
    for i in range(len(df)):
        if i == 1:
            d2.cash, d2.shares = 5_000.0, 50
        sl = df.iloc[: i + 1].copy(deep=True)
        strat._bind_data(sl)
        strat._bind_account(cash=d2.cash, shares=d2.shares, equity=d2._equity(sl))
        strat._enter_next(bar_index=i, date=d2._date_of(i))
        strat.next()
        strat._exit_next()
    assert log[0] == (10_000.0, 0, 10_000.0)
    assert log[1][0] == 5_000.0 and log[1][1] == 50
    assert log[1][2] == pytest.approx(5_000.0 + 50 * float(df.loc[1, "Close"]))
    assert log[2][2] != log[1][2], "Close 变了,equity 必须跟着变"


# ═══════════ 门:ADR-036 的调用契约 ═══════════


def test_init_called_exactly_once_before_first_next():
    """ADR-036:`init()` 恰调用 **1** 次,且在首次 `next()` 之前。"""
    calls: list[str] = []

    class S(Strategy):
        def init(self) -> None:
            calls.append("init")

        def next(self) -> None:
            calls.append("next")

    _Driver(S, make_bars(4)).run()
    assert calls.count("init") == 1, f"init 调用 {calls.count('init')} 次"
    assert calls[0] == "init", f"init 不在首次 next 之前:{calls[:2]}"
    assert calls == ["init"] + ["next"] * 4


def test_init_sees_zero_row_dataframe_and_equity_equals_cash():
    """ADR-036:`init()` 内 `data` 为**零行** DataFrame(列齐全)、`equity == cash`。"""
    seen: dict[str, object] = {}

    class S(Strategy):
        def init(self) -> None:
            seen["len"] = len(self.data)
            seen["cols"] = list(self.data.columns)
            seen["cash"] = self.cash
            seen["shares"] = self.shares
            seen["equity"] = self.equity
            seen["is_df"] = isinstance(self.data, pd.DataFrame)

        def next(self) -> None:
            pass

    _Driver(S, make_bars(3), cash=12_345.0).run()
    assert seen["is_df"] is True, "init() 内 data 必须是 DataFrame,不是 None"
    assert seen["len"] == 0, f"init() 内 data 必须【零行】,实际 {seen['len']}"
    # 「列齐全」—— 否则 init() 里写 self.data['Close'] 会 KeyError 而非空 Series
    for col in ("Open", "High", "Low", "Close"):
        assert col in seen["cols"], f"init() 的空 DataFrame 缺列 {col}"
    assert seen["shares"] == 0
    assert seen["equity"] == pytest.approx(seen["cash"])
    assert seen["equity"] == pytest.approx(12_345.0)


def test_init_does_not_leak_future_bars():
    """反断言:`init()` 的 data 不是全量(那会违反 I5 —— ADR-036 点名的硬币抛掷)。"""
    seen: list[int] = []

    class S(Strategy):
        def init(self) -> None:
            seen.append(len(self.data))

        def next(self) -> None:
            pass

    _Driver(S, make_bars(7)).run()
    assert seen == [0], f"init() 看到了 {seen[0]} 行 —— 违反 I5"


@pytest.mark.parametrize("call", ["self.target(weight=0.5)", "self.order(shares=1)"])
def test_calling_target_or_order_in_init_raises_value_error(tmp_path, call):
    """ADR-036:在 `init()` 内调 `target()`/`order()` → **`ValueError`**。

    类型钉死为 ValueError(非 StrategyError)—— ADR-036 的字面规定。
    """
    p = write_strategy(tmp_path, "in_init.py", f"""
        class S(Strategy):
            def init(self):
                {call}
            def next(self):
                pass
    """)
    with pytest.raises(ValueError) as ei:
        _Driver(load_strategy(p), make_bars(2)).run()
    assert not isinstance(ei.value, StrategyError), (
        "ADR-036 规定这里是 ValueError;StrategyError 是 RuntimeError 的子类,"
        "不是 ValueError —— 退出码会从 invalid 变成 strategy_error"
    )
    assert "init()" in str(ei.value) and "ADR-036" in str(ei.value)


def test_next_with_extra_required_parameter_is_rejected(tmp_path):
    """ADR-036:`next()` 以**零参数**调用。`def next(self, bar)` → 报错而非合法。"""
    p = write_strategy(tmp_path, "arg_next.py", """
        class S(Strategy):
            def next(self, bar):
                pass
    """)
    with pytest.raises(ValueError) as ei:
        load_strategy(p)
    msg = str(ei.value)
    assert str(p.resolve()) in msg or p.name in msg, f"消息未指明文件:{msg}"
    assert "ADR-036" in msg
    assert "bar" in msg, "消息应指出多出来的参数名"


def test_init_with_extra_required_parameter_is_rejected(tmp_path):
    """同理 `init()` —— ADR-036 的签名是 `def init(self) -> None`。"""
    p = write_strategy(tmp_path, "arg_init.py", """
        class S(Strategy):
            def init(self, ctx):
                pass
            def next(self):
                pass
    """)
    with pytest.raises(ValueError) as ei:
        load_strategy(p)
    assert "ADR-036" in str(ei.value)


def test_next_with_defaulted_extra_parameter_is_accepted(tmp_path):
    """反向守卫:带**默认值**的额外参数仍可零参数调用,不得红掉正确实现。"""
    p = write_strategy(tmp_path, "default_next.py", """
        class S(Strategy):
            def next(self, _scratch=None):
                self.target(weight=0.25)
    """)
    d = _Driver(load_strategy(p), make_bars(2))
    d.run()
    assert d.intents == [TargetIntent(0.25)] * 2


def test_missing_next_raises_not_implemented(tmp_path):
    """ADR-036:`next()` **必需**。只定义 init() 的策略在被调用时必须响亮失败。"""
    p = write_strategy(tmp_path, "no_next.py", """
        class S(Strategy):
            def init(self):
                pass
    """)
    cls = load_strategy(p)  # 加载期不拒绝(签名合法),运行期响亮失败
    with pytest.raises(StrategyError) as ei:
        _Driver(cls, make_bars(2)).run()
    assert "NotImplementedError" in str(ei.value)
    assert "ADR-036" in str(ei.value)


# ═══════════ 门:一文件一子类(ADR-036,关闭 OQ-02) ═══════════


def test_zero_strategy_subclasses_raises_value_error_with_filename(tmp_path):
    p = write_strategy(tmp_path, "empty_strat.py", """
        X = 1
        class NotAStrategy:
            pass
    """)
    with pytest.raises(ValueError) as ei:
        load_strategy(p)
    msg = str(ei.value)
    assert not isinstance(ei.value, StrategyError)
    assert p.name in msg, f"消息必须指明文件名(ADR-036):{msg}"
    assert "ADR-036" in msg


def test_two_strategy_subclasses_raises_value_error_with_filename(tmp_path):
    """静默取第一个的 loader 能通过其余全部条款,并会污染 `GET /api/strategies`。"""
    p = write_strategy(tmp_path, "two_strats.py", """
        class A(Strategy):
            def next(self):
                pass
        class B(Strategy):
            def next(self):
                pass
    """)
    with pytest.raises(ValueError) as ei:
        load_strategy(p)
    msg = str(ei.value)
    assert p.name in msg, f"消息必须指明文件名(ADR-036):{msg}"
    assert "A" in msg and "B" in msg, f"消息应列出两个类名:{msg}"


def test_three_strategy_subclasses_also_rejected(tmp_path):
    p = write_strategy(tmp_path, "three.py", """
        class A(Strategy):
            def next(self): pass
        class B(Strategy):
            def next(self): pass
        class C(Strategy):
            def next(self): pass
    """)
    with pytest.raises(ValueError) as ei:
        load_strategy(p)
    assert p.name in str(ei.value)


def test_class_may_be_named_anything(tmp_path):
    """ADR-036 / OQ-02:类可**任意命名**。固定名 `Strategy` 会与基类同名冲突。"""
    for name in ("BuyAndHold", "MaCross", "低买高卖", "_x"):
        p = write_strategy(tmp_path, f"n_{abs(hash(name))}.py", f"""
            class {name}(Strategy):
                def next(self):
                    self.target(weight=1.0)
        """)
        cls = load_strategy(p)
        assert cls.__name__ == name
        assert issubclass(cls, Strategy)


def test_imported_base_class_does_not_count_as_a_subclass(tmp_path):
    """`from ... import Strategy` 是**必需**写法,基类自身不得被计为子类。"""
    p = write_strategy(tmp_path, "one.py", """
        class Only(Strategy):
            def next(self):
                pass
    """)
    assert load_strategy(p).__name__ == "Only"


def test_subclass_imported_from_another_file_does_not_count(tmp_path):
    """`from other import Foo` 把别处的子类带进命名空间 —— 不得计为本文件的。

    否则一个 import 了共享基类的策略文件会被误判为「2+ 个子类」而无法加载。
    """
    write_strategy(tmp_path, "lib_base.py", """
        class SharedBase(Strategy):
            def next(self):
                self.target(weight=0.5)
    """)
    p = write_strategy(tmp_path, "uses_lib.py", """
        import sys
        sys.path.insert(0, r"%s")
        from nullhypothesis.strategy import Strategy
        from lib_base import SharedBase

        class Mine(SharedBase):
            def next(self):
                self.target(weight=0.75)
    """ % str(tmp_path))
    cls = load_strategy(p)
    assert cls.__name__ == "Mine"
    d = _Driver(cls, make_bars(1))
    d.run()
    assert d.intents == [TargetIntent(0.75)]


def test_subclass_of_subclass_single_definition_is_fine(tmp_path):
    """同文件内两级继承 = 两个子类 → 按 ADR-036 报错(并指明文件名)。"""
    p = write_strategy(tmp_path, "two_level.py", """
        class Mid(Strategy):
            def next(self):
                pass
        class Leaf(Mid):
            def next(self):
                pass
    """)
    with pytest.raises(ValueError) as ei:
        load_strategy(p)
    assert p.name in str(ei.value)


# ═══════════ 门:加载方式(ADR-036,关闭 OQ-01) ═══════════


def test_loader_uses_importlib_and_never_exec_eval_compile():
    """ADR-036:禁止 `exec`/`eval`/`compile`;T9 的 grep 门必须在构造上可满足。

    这里复刻 T9 的 tripwire(`grep -E '\\bexec\\(|\\beval\\(|\\bcompile\\('`),
    但**只对 T2 拥有的源文件**跑 —— 允许的 `spec.loader.exec_module` 不含 `exec(`。
    """
    src = (ROOT / "nullhypothesis" / "strategy.py").read_text(encoding="utf-8")
    for banned in (r"\bexec\(", r"\beval\(", r"\bcompile\("):
        hits = [
            ln for ln in src.splitlines()
            if re.search(banned, ln) and not ln.strip().startswith("#")
        ]
        assert not hits, f"strategy.py 含被 ADR-036 禁止的 {banned}:{hits}"
    # 正向:钉死 ADR-036 规定的三件套都真的被用到
    for required in (
        "importlib.util.spec_from_file_location",
        "importlib.util.module_from_spec",
        "spec.loader.exec_module",
    ):
        assert required in src, f"ADR-036 规定的加载步骤缺失:{required}"


def test_grep_tripwire_over_whole_backend_is_satisfiable():
    """T9 的门会对**整个后端** grep。T2 现在就证明自己不会让它失败。"""
    # 前置 `[^.\w]` 排除**属性访问**:ADR-036 禁的是 builtins 的 exec/eval/
    # compile,不是 `re.compile(` 或 `spec.loader.exec_module`。不排除的话
    # `\bcompile\(` 会命中 `re.compile(` —— `.` 本身就是词边界,这正是
    # DESIGN.md 说 tripwire「需用词边界才不误报」指的那个误报。
    out = subprocess.run(
        ["grep", "-rnE", r"(^|[^.\w])(exec|eval|compile)\(", "--include=*.py", "."],
        cwd=ROOT, capture_output=True, text=True,
    )
    offenders = [
        ln for ln in out.stdout.splitlines()
        if "/tests/" not in ln and not ln.split(":", 2)[-1].strip().startswith("#")
    ]
    assert not offenders, f"后端出现 exec/eval/compile(ADR-036 禁止):{offenders}"


def test_traceback_points_to_real_file_not_string(tmp_path):
    """OQ-01 的关闭理由:`exec` 会让 traceback 显示 `<string>` 而非文件行号。

    这是**禁 exec 的全部收益**,必须被直接断言 —— 而不是只断言源码里没有
    `exec(`(那可以被 `getattr(builtins, 'exec')` 绕过)。
    """
    p = write_strategy(tmp_path, "boom.py", """
        class S(Strategy):
            def next(self):
                x = 1 / 0
    """)
    with pytest.raises(StrategyError) as ei:
        _Driver(load_strategy(p), make_bars(3)).run()
    msg = str(ei.value)
    assert "<string>" not in msg
    assert str(p.resolve()) in msg
    bad_line = next(
        i for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "1 / 0" in ln
    )
    assert re.search(rf"{re.escape(str(p.resolve()))}\", line {bad_line}", msg) or (
        f"{p.resolve()}:{bad_line}" in msg
    ), f"traceback 未指向 {p.name}:{bad_line}\n{msg}"


def test_load_strategy_returns_class_not_instance(tmp_path):
    """返回**类** —— T5 的多策略隔离门要求每次运行自己实例化。"""
    p = write_strategy(tmp_path, "cls.py", """
        class S(Strategy):
            def next(self):
                pass
    """)
    cls = load_strategy(p)
    assert isinstance(cls, type) and issubclass(cls, Strategy)
    assert cls() is not cls()


def test_two_files_with_identical_bytes_load_independently(tmp_path):
    """T5 的「多策略隔离」门的 T2 侧前提:同字节、不同文件名 → 两个独立模块。

    若模块名只取 stem,`sys.modules` 缓存会让第二次加载拿到第一个模块对象,
    模块级可变状态串台 —— 那条门会红在 T5,但根因在 T2 的 loader。
    """
    body = """
        COUNTER = []
        class S(Strategy):
            def next(self):
                COUNTER.append(1)
                self.target(weight=0.5)
    """
    a = write_strategy(tmp_path, "a.py", body)
    b = write_strategy(tmp_path, "a_copy.py", body)
    ca, cb = load_strategy(a), load_strategy(b)
    assert ca is not cb
    assert ca.__module__ != cb.__module__
    _Driver(ca, make_bars(3)).run()
    _Driver(cb, make_bars(3)).run()
    mod_a, mod_b = sys.modules[ca.__module__], sys.modules[cb.__module__]
    assert len(mod_a.COUNTER) == 3 and len(mod_b.COUNTER) == 3, (
        "模块级状态串台了 —— sys.modules 缓存按 stem 碰撞"
    )


def test_same_file_loaded_twice_yields_independent_modules(tmp_path):
    """同一文件两次加载也必须独立(T5 的 `run([A])` vs `run([A,B])` 等价门)。"""
    p = write_strategy(tmp_path, "twice.py", """
        SEEN = []
        class S(Strategy):
            def next(self):
                SEEN.append(1)
    """)
    c1, c2 = load_strategy(p), load_strategy(p)
    assert c1 is not c2
    _Driver(c1, make_bars(2)).run()
    assert len(sys.modules[c2.__module__].SEEN) == 0


@pytest.mark.parametrize("bad_name", ["nope.py", "nope.txt"])
def test_missing_or_non_python_path_raises_value_error(tmp_path, bad_name):
    p = tmp_path / bad_name
    if bad_name.endswith(".txt"):
        p.write_text("not python", encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        load_strategy(p)
    assert p.name in str(ei.value)


def test_directory_path_raises_value_error(tmp_path):
    d = tmp_path / "adir.py"
    d.mkdir()
    with pytest.raises(ValueError) as ei:
        load_strategy(d)
    assert d.name in str(ei.value)


def test_import_time_exception_is_wrapped_as_strategy_error(tmp_path):
    """策略文件在 import 期炸 → ADR-022:带文件名 + 原始异常。"""
    p = write_strategy(tmp_path, "boom_import.py", """
        raise RuntimeError("boom at import")
        class S(Strategy):
            def next(self):
                pass
    """)
    with pytest.raises(StrategyError) as ei:
        load_strategy(p)
    msg = str(ei.value)
    assert str(p.resolve()) in msg
    assert "boom at import" in msg
    assert isinstance(ei.value.__cause__, RuntimeError), (
        "ADR-040:__cause__ 必须携带原始异常"
    )
    # 失败的加载不得在 sys.modules 里留下半初始化的模块
    assert not [k for k in sys.modules if k.endswith("boom_import")
                or "boom_import" in k], "半初始化的模块泄漏进 sys.modules"


def test_syntax_error_in_strategy_file_is_wrapped(tmp_path):
    p = write_strategy(tmp_path, "bad_syntax.py", """
        class S(Strategy)
            def next(self)
    """)
    with pytest.raises(StrategyError) as ei:
        load_strategy(p)
    assert str(p.resolve()) in str(ei.value)
    assert isinstance(ei.value.__cause__, SyntaxError)


# ═══════════ 门:策略抛异常的消息内容(ADR-022) ═══════════


def test_strategy_exception_message_has_all_four_parts(tmp_path):
    """ADR-022:交易日序号 + 日期 + **原始异常类型** + 源文件行号,四者齐全。"""
    p = write_strategy(tmp_path, "my_strategy.py", """
        class S(Strategy):
            def next(self):
                if self.data.index[-1] == 3:
                    raise ZeroDivisionError("division by zero")
    """)
    df = make_bars(6)
    expected_date = pd.Timestamp(df.loc[3, "Date"]).strftime(DATE_FMT)
    with pytest.raises(StrategyError) as ei:
        _Driver(load_strategy(p), df).run()
    err, msg = ei.value, str(ei.value)

    assert err.bar_index == 3 and err.date == expected_date
    assert re.search(r"第\s*3\s*个交易日", msg), msg          # ① 序号
    assert expected_date in msg                               # ② 日期
    assert "ZeroDivisionError" in msg                         # ③ 原始异常类型
    raise_line = next(
        i for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "ZeroDivisionError(" in ln
    )
    assert f'line {raise_line}' in msg and str(p.resolve()) in msg  # ④ 行号
    assert isinstance(err.__cause__, ZeroDivisionError)


def test_strategy_exception_in_init_is_also_reported(tmp_path):
    """`init()` 内抛异常同样按 ADR-022 终止(此时还没有交易日)。"""
    p = write_strategy(tmp_path, "boom_init.py", """
        class S(Strategy):
            def init(self):
                raise KeyError("missing")
            def next(self):
                pass
    """)
    with pytest.raises(StrategyError) as ei:
        _Driver(load_strategy(p), make_bars(3)).run()
    assert "KeyError" in str(ei.value)
    assert ei.value.bar_index == -1


def test_strategy_error_is_runtime_error_not_value_error():
    """ADR-040:`StrategyError(RuntimeError)`。退出码映射靠这个类型分叉。"""
    assert issubclass(StrategyError, RuntimeError)
    assert not issubclass(StrategyError, ValueError)
    err = StrategyError("x", strategy="s", bar_index=1, date="2020-01-02")
    assert (err.strategy, err.bar_index, err.date) == ("s", 1, "2020-01-02")


# ═══════════ 门:I5 —— 数据长度 == T+1,不含第 T+1 根 ═══════════


def test_i5_slice_length_equals_bar_index_plus_one():
    """断言 I5:策略收到的数据长度 `== T+1`,且**不含**第 T+1 根 K 线。"""
    lengths: list[int] = []
    last_closes: list[float] = []

    class S(Strategy):
        def next(self) -> None:
            lengths.append(len(self.data))
            last_closes.append(float(self.data["Close"].iloc[-1]))

    df = make_bars(6)
    _Driver(S, df).run()

    assert lengths == list(range(1, 7)), f"切片长度不是 T+1:{lengths}"
    # 「不含第 T+1 根」—— 用【互异】的 Close 钉死:最后一行必须是第 T 根
    assert last_closes == [float(c) for c in df["Close"]], (
        "最后一行不是第 T 根 —— 策略看到了未来的 bar"
    )
    assert len(set(last_closes)) == len(last_closes), "Close 必须互异,否则错位测不出"


def test_i5_slice_is_an_independent_copy():
    """I5:切片是**独立副本**。写入它不得影响后续调用看到的数据。

    I5 的诚实边界(§1):框架只保证自己传出去的是副本,不实现沙箱。
    """
    observed: list[list[float]] = []

    class S(Strategy):
        def next(self) -> None:
            observed.append([float(x) for x in self.data["Close"]])
            self.data.iloc[0, self.data.columns.get_loc("Close")] = -1.0
            self.data["Close"].values[:] = 0.0

    df = make_bars(4)
    before = [float(c) for c in df["Close"]]
    _Driver(S, df).run()
    assert [float(c) for c in df["Close"]] == before, "策略改到了源 DataFrame"
    for i, row in enumerate(observed):
        assert row == before[: i + 1], f"第 {i} 根看到了被上一根污染的数据:{row}"


def test_i5_retained_reference_keeps_its_own_length():
    """每次调用是**新**副本,不是一份被反复 re-slice 的同一对象。"""
    kept: list[pd.DataFrame] = []

    class S(Strategy):
        def next(self) -> None:
            kept.append(self.data)

    _Driver(S, make_bars(5)).run()
    assert [len(d) for d in kept] == [1, 2, 3, 4, 5]
    assert len({id(d) for d in kept}) == 5, "同一个 DataFrame 对象被反复 re-slice"


def test_strategy_can_compute_moving_average_from_self_data():
    """ADR-036 的用法示例必须真的可用:`self.data['Close'].rolling(n).mean()`。"""
    seen: list[float] = []

    class S(Strategy):
        def next(self) -> None:
            ma = self.data["Close"].rolling(3).mean().iloc[-1]
            seen.append(float(ma))

    df = make_bars(5)
    _Driver(S, df).run()
    assert math.isnan(seen[0]) and math.isnan(seen[1])
    closes = [float(c) for c in df["Close"]]
    assert seen[2] == pytest.approx(sum(closes[0:3]) / 3)
    assert seen[4] == pytest.approx(sum(closes[2:5]) / 3)


# ═══════════ 门:ADR-041 末次胜出(T2 侧 = 策略层的意图) ═══════════


def test_adr041_last_target_wins():
    """同一 `next()` 内 `target(0.3)` 再 `target(0.8)` → 只留 0.8。"""
    class S(Strategy):
        def next(self) -> None:
            self.target(weight=0.3)
            self.target(weight=0.8)

    d = _Driver(S, make_bars(2))
    d.run()
    assert d.intents == [TargetIntent(0.8)] * 2
    assert TargetIntent(0.3) not in d.intents, "先前调用必须被丢弃(ADR-041)"


def test_adr041_order_after_target_wins():
    """混用也按调用顺序末次胜出:`target(0.5)` 再 `order(100)` → 按 order。"""
    class S(Strategy):
        def next(self) -> None:
            self.target(weight=0.5)
            self.order(shares=100)

    d = _Driver(S, make_bars(1))
    d.run()
    assert d.intents == [ShareIntent(100)]


def test_adr041_target_after_order_wins():
    """反向混用:`order(100)` 再 `target(0.5)` → 按 target。

    这是 ADR-041 与 I4 唯一会对「哪个订单存活」产生分歧的地方(T4 的关键 case
    「末次差额为 0」的 T2 侧前提):先前的 `order(100)` 必须被**丢弃**,
    而不是「留着等差额非零再执行」。
    """
    class S(Strategy):
        def next(self) -> None:
            self.order(shares=100)
            self.target(weight=0.5)

    d = _Driver(S, make_bars(1))
    d.run()
    assert d.intents == [TargetIntent(0.5)]
    assert not isinstance(d.intents[0], ShareIntent)


def test_adr041_ten_calls_leave_exactly_one_intent():
    """I4 的策略侧来源:十次调用后恰好一个意图,不是十个排队。"""
    class S(Strategy):
        def next(self) -> None:
            for i in range(10):
                self.target(weight=i / 10.0)

    d = _Driver(S, make_bars(1))
    strat = d.run()
    assert strat.pending_intent == TargetIntent(0.9)
    assert d.intents == [TargetIntent(0.9)]


def test_intent_does_not_leak_across_bars():
    """ADR-041 的跨 bar 边界:只在第 0 根下单 → 其余各根的意图为 None。

    拦住「意图残留」的实现:它会让 ADR-005(最后一根丢弃)与 ADR-008
    (零差额跳过)在 T4 变得无从观测。
    """
    class S(Strategy):
        def next(self) -> None:
            if self.data.index[-1] == 0:
                self.order(shares=1)

    d = _Driver(S, make_bars(4))
    d.run()
    assert d.intents == [ShareIntent(1), None, None, None]


def test_no_call_leaves_no_intent():
    """不下单的 bar 没有意图 —— 不是「weight=0 的意图」(那会在 T4 产生卖单)。"""
    class S(Strategy):
        def next(self) -> None:
            pass

    d = _Driver(S, make_bars(3))
    d.run()
    assert d.intents == [None, None, None]


# ═══════════ 门:ADR-035/036 的名字契约 ═══════════


def test_adr035_pinned_symbols_exist():
    """ADR-035:`strategy.py` 暴露 `Strategy` 与 `load_strategy`。名字契约。"""
    import nullhypothesis.strategy as mod

    assert hasattr(mod, "Strategy") and isinstance(mod.Strategy, type)
    assert callable(mod.load_strategy)


def test_adr036_signatures_are_implemented_verbatim():
    """ADR-036 的签名逐字:`init()`/`next()` 零参数、`target(weight)`、`order(shares)`。"""
    import inspect as _i

    assert list(_i.signature(Strategy.init).parameters) == ["self"]
    assert list(_i.signature(Strategy.next).parameters) == ["self"]
    assert list(_i.signature(Strategy.target).parameters) == ["self", "weight"]
    assert list(_i.signature(Strategy.order).parameters) == ["self", "shares"]
    # 返回标注为 None(ADR-036 的 `-> None`)
    for name in ("init", "next", "target", "order"):
        ann = _i.signature(getattr(Strategy, name)).return_annotation
        assert ann in (None, "None"), f"{name} 的返回标注应为 None,实际 {ann!r}"


def test_adr036_attribute_names_are_exactly_the_four():
    """四个属性名钉死 —— 下游(T4/T5)按名读取。"""
    for name in ("data", "cash", "shares", "equity"):
        attr = getattr(Strategy, name, None)
        assert attr is not None, f"ADR-036 的属性 {name} 缺失"
        assert hasattr(type(attr), "__get__"), f"{name} 必须是描述符(只读)"
        assert hasattr(type(attr), "__set__"), f"{name} 必须拒绝赋值"


# ═══════════ 门:本轮返工新增接缝(审查 J1/J2/J4) ═══════════


def test_frozen_leverage_text_constant_matches_contracts_yaml():
    """审查 J1:生产常量与 `contracts.yaml` 的 key 必须一致。

    生产代码改为**钉死常量**(不在运行时读 YAML —— contracts.yaml:12 的纪律
    约束的是测试,且 `except Exception` 回退会让漂移静默)。漂移的拦截点就是
    本条门:常量与 YAML 一旦分叉,这里立刻红。
    """
    from nullhypothesis.strategy import LEVERAGE_NOT_IMPLEMENTED

    assert LEVERAGE_NOT_IMPLEMENTED == FROZEN["leverage_not_implemented"]


def test_strategy_module_imports_without_yaml_or_contracts_file():
    """审查 J1:`nullhypothesis` 不得依赖 PyYAML 或 contracts.yaml 的存在。

    §6 只把 PyYAML 列为 `CONTRACT_CMD` 的依赖,不是本包的依赖。断言**行为**而非
    源码文本:在子进程里屏蔽 `yaml` 并把 cwd 换到空目录(contracts.yaml 不可见),
    import 仍须成功且常量仍须是正确值。
    """
    probe = textwrap.dedent(
        """
        import sys, pathlib
        sys.modules["yaml"] = None          # 任何 `import yaml` 都会 ImportError
        sys.path.insert(0, %r)
        from nullhypothesis.strategy import LEVERAGE_NOT_IMPLEMENTED as L
        assert L == %r, L
        print("ok")
        """
    ) % (str(ROOT), FROZEN["leverage_not_implemented"])
    empty = tempfile.mkdtemp()
    out = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=empty,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, f"stdout={out.stdout!r} stderr={out.stderr!r}"
    assert "ok" in out.stdout


def test_bind_identity_binds_to_instance_not_class(tmp_path):
    """审查 J2:`_bind_identity` 把身份绑在实例上,不污染共享类状态。"""
    p = write_strategy(tmp_path, "ident.py", """
        class S(Strategy):
            def next(self):
                pass
    """)
    cls = load_strategy(p)
    a, b = cls(), cls()
    a._bind_identity(source_file="/x/a.py", strategy_name="aaa")

    assert a._strategy_name == "aaa" and a._source_file == "/x/a.py"
    # b 未绑定 → 仍读到 load_strategy 写在类上的向后兼容默认值
    assert b._strategy_name == "ident", "实例绑定不得泄漏到同类的其他实例"
    assert cls._strategy_name == "ident", "实例绑定不得改写类属性"


def test_phase_values_are_exactly_the_documented_set(tmp_path):
    """审查 J4:`_phase` 的实际取值集合 == `_PHASES`(注释曾漏掉 `ready`)。"""
    from nullhypothesis.strategy import _PHASES

    seen = set()
    p = write_strategy(tmp_path, "ph.py", """
        class S(Strategy):
            def init(self):
                pass
            def next(self):
                pass
    """)
    cls = load_strategy(p)
    s = cls()
    seen.add(s._phase)                       # created
    s._enter_init(data=make_bars(0), cash=100.0)
    seen.add(s._phase)                       # init
    s._exit_init()
    seen.add(s._phase)                       # ready
    s._enter_next(bar_index=0, date="2020-01-02")
    seen.add(s._phase)                       # next
    s._exit_next()
    seen.add(s._phase)                       # ready

    assert seen == set(_PHASES), f"实际阶段 {seen} != 文档化的 {set(_PHASES)}"
