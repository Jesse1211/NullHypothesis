"""T21 的验收门:因子法复权 + 两路仲裁 + 跳空体检。

契约来源:**ADR-050**(复权划入框架内,全列同乘,后复权,「严格晚于 t」)、
**ADR-051**(两路缺席时的跳空体检,运算符 `>=`)、**ADR-052**(两条因子路径,
行为表优先,不一致则报错,`tol = 1e-4`)、**ADR-053**(`close_before` 取
除息日前一交易日的**原始**收盘价;`ratio` 取前向股数比)、
**ADR-054**(`amount` 与 `close_before` 同股数基准 —— 方向门 / 量级门 /
`amount >= close_before`)、**ADR-055**(诚实声明,文案由 T24 归集)。

**真实数据状态**(与 `tests/test_data.py` 同一条硬要求):每个测试都把 CSV
**真实写到磁盘**,再用路径调 `load_csv` —— **不构造内存 DataFrame**。
`tests/test_data.py` 开头已记明理由:内存 DataFrame 会绕过 `pd.read_csv`
的类型推断与空文件/仅表头行为。T21 另有一条自己的理由:`Adj Close` 的
「先读取、后移除」顺序只在真实读文件的路径上可观测。

**期望值一律引用 `contracts.yaml` 的 key,不复述字面量** ——
该规则写在 `contracts.yaml` 开头,由 `tests/test_design_binding.py` 强制。

blindSpots(T21 的门要求声明):
  * ① `amount` 与 `close_before` 的**同基准前提无法从数据独立证实**
    (ADR-054)。方向门只看 split 的价格比,量级门只看股息率落在哪个带 ——
    **二者都只抓部分违反**:一笔基准错但股息率仍在 25% 以下的分红
    (绝大多数真实情形)对这两条门**完全不可见**。
    这与 ADR-001 的结构弱点同类,区别只在于它**可被部分验证**。
  * ② 体检阈值对真实暴跌会**误报**(2020-03 多次 −20% 的交易日),
    对小比例拆股会**漏报**(6:5 = −16.7%、10:9 = −10% 都在阈值下)。
    ADR-051 明文接受这个张力:宁可误报。
  * ③ 本任务**不验证**复权后的价格是否等于任何**外部**数据源的复权价。
    两条路径互校只能证明「两个来源一致」,不能证明「与 Yahoo 一致」。
  * ④ `yfinance` 的 `auto_adjust=True`(0.2.x 起的库默认值)返回**已调整**的
    `Close` 且**没有** `Adj Close` 列 → 两路同时缺席,体检在**已调整**的
    数据上运行、什么也查不到。这是正确行为,但框架**无法分辨**
    「已复权」与「无事件」(ADR-052 的在范围声明,须进 ADR-055)。
  * ⑤ 继承 `tests/test_data.py` 声明的那组 CSV 变体(编码 / BOM / 千分位 /
    CRLF / 分隔符变体 / 超大文件)—— 同一个 `pd.read_csv`,同样未被测试。
"""

from __future__ import annotations

import warnings
from datetime import date
from pathlib import Path

import pytest
import yaml

from nullhypothesis.data import (
    DIVIDEND_YIELD_SUSPICION,
    FACTOR_AGREEMENT_TOL,
    GAP_SUSPICION_PCT,
    PRICE_COLUMNS,
    CorporateEvent,
    UnadjustedPriceWarning,
    UnsortedDateWarning,
    cumfactors_from_events,
    load_csv,
    load_events,
)
from nullhypothesis.errors import DataValidationError

ROOT = Path(__file__).resolve().parent.parent
C = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))

THRESHOLDS = C["thresholds"]
BASIS = C["fixtures"]["adr053_close_before_basis"]
CUMF = C["fixtures"]["adr050_cumfactor"]
SPLITF = C["fixtures"]["adr050_split_factor"]
DIRECTION = C["fixtures"]["adr054_direction_gate"]
MAGNITUDE = C["fixtures"]["adr054_dividend_magnitude"]
ARBITRATION = C["fixtures"]["adr052_arbitration"]
HEALTH = C["fixtures"]["adr051_gap_health_check"]

PRICE_HEADER = "Date,Open,High,Low,Close,Volume"


# ───────────────────────────── 真实磁盘 fixture ─────────────────────────────


def write_prices(
    tmp_path: Path,
    bars: list[tuple[str, float]],
    name: str = "prices.csv",
    adj: list[float] | None = None,
) -> Path:
    """把价格 CSV 真实写到磁盘。`bars` 是 `(日期, 收盘)`。

    OHLC 四列由收盘派生出一个**真实的 bar 形状**(而非四列同值):
    `Open = c`、`High = c*1.02`、`Low = c*0.98`、`Close = c`。
    四列**互不相同**是门 2(全列同乘)可执行的前提 —— 四列同值时
    「只调了 Close」与「四列都调了」在序关系上无从分辨。
    """
    header = PRICE_HEADER if adj is None else (
        "Date,Open,High,Low,Close,Adj Close,Volume"
    )
    rows = []
    for i, (day, c) in enumerate(bars):
        cells = [day, f"{c!r}", f"{c * 1.02!r}", f"{c * 0.98!r}", f"{c!r}"]
        if adj is not None:
            cells.append(f"{adj[i]!r}")
        cells.append("1000")
        rows.append(",".join(cells))
    path = tmp_path / name
    path.write_text(header + "\n" + "".join(r + "\n" for r in rows), "utf-8")
    assert path.is_file(), "fixture 必须真实落盘"
    return path


def write_events_file(tmp_path: Path, rows: list[str], name: str = "ev.csv") -> Path:
    header = ",".join(C["fixtures"]["adr053_events_schema"]["columns"])
    path = tmp_path / name
    path.write_text(header + "\n" + "".join(r + "\n" for r in rows), "utf-8")
    return path


def split_row(day: str, ratio: float) -> str:
    return f"{day},split,{ratio!r},"


def dividend_row(day: str, amount: float) -> str:
    return f"{day},dividend,,{amount!r}"


def no_warnings():
    """`pytest.warns(None)` 在 pytest 8 里被移除,故用 catch_warnings。"""
    return warnings.catch_warnings(record=True)


# ═══════════ 门 2:全列同乘 + 复权后六条序关系 + 价格正性 ═══════════


def test_split_adjusts_all_four_price_columns_by_the_same_factor(tmp_path):
    """门 2:一次 4:1 拆股 → `Open/High/Low/Close` **四列**都被**同一个**因子调整。

    **绝不半复权**(ADR-050):直接把 `Close` 换成 `Adj Close` 会让两列落在
    不同的价格体系里,而 ADR-002 让决策读 `Close_t`、成交读 `Open_{t+1}`,
    故半复权序列会在**每一根** bar 上注入一个**假的隔夜跳空** ——
    那比完全不复权更糟。
    """
    ratio = SPLITF["forward_4_for_1"]
    expected_factor = SPLITF["expect_factor_4_for_1"]
    # 价格在拆股日确实跌到 1/4,使方向门成立
    bars = [("2020-01-02", 400.0), ("2020-01-03", 400.0), ("2020-01-06", 100.0)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(tmp_path, [split_row("2020-01-06", ratio)])

    raw = load_csv(prices)
    adjusted = load_csv(prices, events=events)

    # 拆股**之前**的两根 bar:四列都 × 同一个因子
    for i in (0, 1):
        for column in PRICE_COLUMNS:
            assert adjusted[column].iloc[i] == pytest.approx(
                raw[column].iloc[i] * expected_factor
            ), f"row={i} column={column} 未被同一个因子调整"

    # 「同一个因子」的**独立**断言:四列各自的 调整后/原始 比值彼此相等。
    # 这条比上面那组更强 —— 它不依赖 `expected_factor` 的取值,
    # 故一个「四列各乘自己的 AdjClose/Close」的半复权实现会在此必红。
    for i in (0, 1):
        quotients = [
            adjusted[c].iloc[i] / raw[c].iloc[i] for c in PRICE_COLUMNS
        ]
        assert quotients == pytest.approx([quotients[0]] * 4)


def test_order_relations_and_positivity_survive_adjustment(tmp_path):
    """门 2 续:复权后**全部六条**序关系仍成立(**穷举,不得用「等」收尾**),
    且四列**全部 > 0**(ADR-019 的价格正性在复权后不得被破坏)。
    """
    ratio = SPLITF["forward_4_for_1"]
    bars = [("2020-01-02", 400.0), ("2020-01-03", 400.0), ("2020-01-06", 100.0)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(tmp_path, [split_row("2020-01-06", ratio)])

    df = load_csv(prices, events=events)

    for i in range(len(df)):
        o = df["Open"].iloc[i]
        h = df["High"].iloc[i]
        low = df["Low"].iloc[i]
        c = df["Close"].iloc[i]
        # 六条,逐条写出 —— 不用「等」收尾
        assert h >= low, f"row={i} High >= Low"
        assert h >= o, f"row={i} High >= Open"
        assert h >= c, f"row={i} High >= Close"
        assert low <= o, f"row={i} Low <= Open"
        assert low <= c, f"row={i} Low <= Close"
        # 第六条:四列全部 > 0(ADR-019 的价格正性)
        for column in PRICE_COLUMNS:
            assert df[column].iloc[i] > 0, f"row={i} column={column} 价格正性"


# ═══════════════════ 门 3:后复权 —— 最新一行 cumfactor == 1.0 ═══════════════════


def test_last_row_is_untouched_because_cumfactor_is_one(tmp_path):
    """门 3:**后复权** —— 最新一行的 `cumfactor == 1.0`,
    即最后一行 OHLC **逐值等于** CSV 原值。

    与 `Adj Close` 列自身的语义一致,使两条因子路径产出**同一组绝对价格**
    (ADR-050)。
    """
    bars = [("2020-01-02", 400.0), ("2020-01-03", 400.0), ("2020-01-06", 100.0)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(
        tmp_path, [split_row("2020-01-06", SPLITF["forward_4_for_1"])]
    )

    raw = load_csv(prices)
    adjusted = load_csv(prices, events=events)

    for column in PRICE_COLUMNS:
        assert adjusted[column].iloc[-1] == raw[column].iloc[-1], (
            f"最后一行 column={column} 必须逐值等于 CSV 原值"
        )

    # 因子序列自身的断言 —— 末项恒为 1.0
    cumfactors = cumfactors_from_events(
        raw, load_events(events), str(events)
    )
    assert cumfactors[-1] == CUMF["last_row_cumfactor"]


# ═══════════ 门 4:累积规则「严格晚于 t」—— 除权日当根 bar 不被自己调整 ═══════════


def test_cumfactor_uses_strictly_later_events_only(tmp_path):
    """门 4:构造**两个**事件,断言中间区段的因子是**后一个事件**的因子
    (而非两者之积)—— 除权日**当根** bar 不被自己的事件调整。

    本规则经三份独立实现确认为学术标准:CRSP 的 `CUMFACPR`(`EXDT > d`)、
    一份 Tiingo 参考实现("never divide ex-date")、Sharadar 的
    `cumprod(ratios[::-1])[::-1]` + `searchsorted(side='right')`。
    ⚠️ **zipline 反其道而行**(`Float64Multiply` 含 `effective_date` 当行)
    —— 同名列,差一天,故 zipline 格式的调整表不能逐日照搬。
    """
    ra, rb = CUMF["split_a_ratio"], CUMF["split_b_ratio"]
    # 价格让两次拆股的方向门都成立:1000 → 500(1/2)→ 100(1/5)
    bars = [
        ("2020-01-02", 1000.0),   # A、B 都晚于它 → (1/2)×(1/5)
        ("2020-01-03", 500.0),    # ex-date(A)当根 —— 只有 B 晚于它 → 1/5
        ("2020-01-06", 500.0),    # 中间区段 —— 只有 B 晚于它 → 1/5
        ("2020-01-07", 100.0),    # ex-date(B)当根 —— 无事件晚于它 → 1.0
    ]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(tmp_path, [
        split_row("2020-01-03", ra),
        split_row("2020-01-07", rb),
    ])

    df = load_csv(prices)
    cumfactors = cumfactors_from_events(df, load_events(events), str(events))

    assert cumfactors[0] == pytest.approx(CUMF["expect_segment_before_a"])
    # **中间区段是后一个事件的因子,不是两者之积** —— 这一条就是「严格晚于」
    assert cumfactors[1] == pytest.approx(CUMF["expect_segment_between"])
    assert cumfactors[2] == pytest.approx(CUMF["expect_segment_between"])
    assert cumfactors[3] == pytest.approx(CUMF["last_row_cumfactor"])

    # 显式拒绝「把除权日当根 bar 也算进去」的实现(zipline 的 inclusive 口径)
    assert cumfactors[1] != pytest.approx(CUMF["reject_segment_between"])


# ═══════════════════ 门 5:split 因子 = 1/ratio,合股须放大 ═══════════════════


def test_forward_split_factor_is_the_reciprocal_of_ratio(tmp_path):
    """门 5 前半:4:1 → 历史价 × 0.25。"""
    ratio = SPLITF["forward_4_for_1"]
    bars = [("2020-01-02", 400.0), ("2020-01-03", 100.0)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(tmp_path, [split_row("2020-01-03", ratio)])

    df = load_csv(prices, events=events)
    assert df["Close"].iloc[0] == pytest.approx(
        400.0 * SPLITF["expect_factor_4_for_1"]
    )


def test_reverse_split_factor_scales_history_up(tmp_path):
    """门 5 后半:**合股** `ratio = 0.125` → 历史价 × 8.0(**放大**)。

    `ratio < 1` 自然即合股(ADR-053 取约定 A 的三条理由之一)。
    这条门与上一条成对:只测拆股无法区分 `1/ratio` 与 `ratio` ——
    4:1 时前者给 0.25、后者给 4.0,而**合股**时方向正好反过来。
    """
    ratio = SPLITF["reverse_1_for_8"]
    # 1:8 合股 → 价格**涨** 8 倍:12.5 → 100.0
    bars = [("2020-01-02", 12.5), ("2020-01-03", 100.0)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(tmp_path, [split_row("2020-01-03", ratio)])

    df = load_csv(prices, events=events)
    assert df["Close"].iloc[0] == pytest.approx(
        12.5 * SPLITF["expect_factor_1_for_8"]
    )
    # **放大**:复权后的历史价高于原始价
    assert df["Close"].iloc[0] > 12.5


# ═══════════ 门 6:dividend 因子,`close_before` 取前一交易日的【原始】收盘 ═══════════
#
# 三个子门。**缺第三个则 H2 类缺陷不可观测**(T21 门 6 原文)。


def _basis_fixture(tmp_path: Path):
    """ADR-053 实测段的 fixture:**分红在前、拆股在后**(最常见的次序)。

    原始收盘 `100.00 / 98.50 / 100.00 / 20.00`,除息 1.50、5:1 拆股。
    """
    bars = list(zip(BASIS["dates"], BASIS["raw_closes"]))
    prices = write_prices(tmp_path, bars)
    events = write_events_file(tmp_path, [
        dividend_row(BASIS["dividend_date"], BASIS["dividend_amount"]),
        split_row(BASIS["split_date"], BASIS["split_ratio"]),
    ])
    return prices, events


def test_close_before_is_the_previous_bar_not_the_ex_date_bar(tmp_path):
    """门 6(a):构造用例使「用当日收盘」与「用前一日收盘」结果**可区分**,
    断言取**后者**。

    `close_before` 也**不得**用除息日**当天**的收盘价:当天开盘已扣掉分红,
    用它当分母等于**用「已经调整过的价格」再算一次调整** —— 二次扣减
    (ADR-053)。
    """
    prices, events = _basis_fixture(tmp_path)
    df = load_csv(prices)
    cumfactors = cumfactors_from_events(df, load_events(events), str(events))

    # 前一交易日原始收盘 = 100.00 → f = 1 − 1.50/100.00 = 0.985
    f_div = BASIS["expect_f_dividend"]
    f_split = BASIS["expect_f_split"]
    assert cumfactors[0] == pytest.approx(f_div * f_split)

    # 「用除息日当天收盘(98.50)」会给出一个**可区分**的不同值 → 必须不等
    wrong = (1.0 - BASIS["dividend_amount"] / BASIS["raw_closes"][1]) * f_split
    assert cumfactors[0] != pytest.approx(wrong)


def test_close_before_is_the_raw_close_not_the_split_adjusted_one(tmp_path):
    """门 6(b):**分红在前、拆股在后** —— 断言 05-12 复权后收盘
    **== 19.700**,**拒绝 18.500**(后者即用**已拆股调整**的收盘做分母)。

    ADR-053 实测两种读法**相对误差 −6.09%**,作用于分红日之前的**每一根**
    bar,且多笔分红会**复利放大**。`close_before` 取原始值是**因子法的
    定义性前提**,不是实现细节。
    """
    prices, events = _basis_fixture(tmp_path)
    df = load_csv(prices, events=events)

    assert df["Close"].iloc[0] == pytest.approx(
        BASIS["expect_adjusted_close_first"]
    )
    # 拒绝已拆股调整的分母:1 − 1.50/20.00 = 0.925 → 18.500
    assert df["Close"].iloc[0] != pytest.approx(
        BASIS["reject_adjusted_close_first"]
    )


def test_factor_method_equals_dividend_reinvestment(tmp_path):
    """门 6(c):同一 fixture 断言总收益与「除息日收盘再投资」的**地面真值**
    逐位吻合 —— 故该门同时验证 **ADR-055 的「因子法 ≡ 自动再投资」声明为真**。

    地面真值(ADR-053):持 1 股、05-12 价 100,除息日按**当日收盘**(98.50)
    再投资 1.50,再经 5:1 拆股,06-18 终值 `101.5228426395939`。
    而因子法用**原始** `close_before` 推出的总收益
    `0.015228426395939021` —— **末位数字吻合**。

    **这是本组三条门里最重要的一条**:它是唯一能把「`close_before` 读成
    已调整值」(H2 类缺陷)照出来的断言 —— 前两条门只比一个价格,
    而这一条比的是**整段区间的收益**,与一个完全独立的现金流模型对账。
    """
    prices, events = _basis_fixture(tmp_path)
    df = load_csv(prices, events=events)

    first, last = df["Close"].iloc[0], df["Close"].iloc[-1]
    total_return = last / first - 1.0
    assert total_return == pytest.approx(
        BASIS["expect_total_return"], rel=0, abs=1e-15
    )

    # 与地面真值的独立对账:持 1 股 + 除息日收盘再投资 + 5:1 拆股
    shares = 1.0 + BASIS["dividend_amount"] / BASIS["raw_closes"][1]
    shares *= BASIS["split_ratio"]
    terminal = shares * BASIS["raw_closes"][-1]
    assert terminal == pytest.approx(BASIS["ground_truth_terminal"])
    # 两条路算出的总收益是**同一个**数
    assert terminal / BASIS["raw_closes"][0] - 1.0 == pytest.approx(total_return)


# ═══════════ 门 7:ex-date 落在第一根 bar → 报错(close_before 不存在) ═══════════


def test_dividend_on_the_first_bar_is_an_error(tmp_path):
    """门 7:**ex-date 落在第一根 bar**(无前一交易日,故 `close_before`
    不存在)→ **报错**,消息须指明该日期并说明原因。

    *(**不得退化为用 bar 0 自己的收盘** —— 那正是二次扣减。)*
    """
    bars = [("2020-01-02", 100.0), ("2020-01-03", 101.0)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(tmp_path, [dividend_row("2020-01-02", 1.5)])

    with pytest.raises(DataValidationError) as excinfo:
        load_csv(prices, events=events)

    message = str(excinfo.value)
    assert "2020-01-02" in message, "消息须指明该日期"
    # 说明原因:close_before 不存在 / 第一根 bar / 二次扣减
    assert "close_before" in message
    assert "第一根 bar" in message or "前一交易日" in message


# ═══════════════════ 门 8:`Adj Close` 反解(顺序是前提) ═══════════════════


def test_adj_close_is_removed_from_the_returned_frame(tmp_path):
    """门 8①:该列**不出现**在返回的 DataFrame 里
    (ADR-001 主张 A 的残留部分仍有效)。"""
    bars = [("2020-01-02", 100.0), ("2020-01-03", 101.0)]
    prices = write_prices(tmp_path, bars, adj=[50.0, 101.0])
    df = load_csv(prices)
    assert "Adj Close" not in df.columns
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]


def test_factor_really_comes_from_adj_close(tmp_path):
    """门 8②:**且**因子确实由它算出 —— 构造一份 `Adj Close` 与 `Close` 比值
    **非 1** 的 CSV,断言返回的价格**已被调整**。

    **若实现保留了 drop-before-read,此断言必红。** `data.py` 原先的
    `EXCLUDED_COLUMNS` 是**读取前就 drop**(注释自述「先丢弃再校验…
    结构上不可能影响结果」)—— 那个顺序会让本条的前提**不可达**。
    顺序必须是「先读取用于算因子,再从返回帧中移除」。
    """
    # 一次 2:1 拆股的签名:Adj Close 把历史折半
    bars = [("2020-01-02", 200.0), ("2020-01-03", 100.0)]
    prices = write_prices(tmp_path, bars, adj=[100.0, 100.0])

    df = load_csv(prices)

    # factor_0 = (100/200)/(100/100) = 0.5 → 首行价格被折半
    assert df["Close"].iloc[0] == pytest.approx(100.0)
    assert df["Open"].iloc[0] == pytest.approx(100.0)
    # 后复权:末行逐值等于原值
    assert df["Close"].iloc[-1] == pytest.approx(100.0)
    # 四列同乘:比值彼此相等
    raw_high, raw_low = 200.0 * 1.02, 200.0 * 0.98
    assert df["High"].iloc[0] == pytest.approx(raw_high * 0.5)
    assert df["Low"].iloc[0] == pytest.approx(raw_low * 0.5)


def _write_prices_in_row_order(
    tmp_path: Path,
    bars: list[tuple[str, float]],
    adj: list[float],
    row_order: list[int],
    name: str,
) -> Path:
    """与 `write_prices` 同形,但按 `row_order` 给出的**原始行号**写行。

    `write_prices` 总是按 `bars` 的给定顺序落盘;本条门要的是**同一批行**
    以乱序落盘,故单独写一个 writer,不去改动共用 helper(门 17 的加性保证:
    既有内联 CSV 的断言一行都不许动)。
    """
    rows = [
        f"{day},{c!r},{c * 1.02!r},{c * 0.98!r},{c!r},{a!r},1000"
        for (day, c), a in zip(bars, adj)
    ]
    path = tmp_path / name
    path.write_text(
        "Date,Open,High,Low,Close,Adj Close,Volume\n"
        + "".join(rows[i] + "\n" for i in row_order),
        "utf-8",
    )
    assert path.is_file(), "fixture 必须真实落盘"
    return path


def test_adj_close_follows_the_same_sort_permutation(tmp_path):
    """门 8 × ADR-019:**乱序 CSV 与 `Adj Close` 同时在场**时,输出必须与
    手工排序版本**逐值相同**(DESIGN.md:1051「ADR-019 排序后等价」在因子
    路径上的延伸;乱序是 ADR-019 明确**允许**的输入形态,见 DESIGN.md:373)。

    为什么这条门是必要的:`Adj Close` 在排序**前**按原始行序读下来
    (门 8① 要求它不出现在返回帧里,故它被单独拎成一个 Series),之后
    `df` 被 `sort_values` 重排。两者必须跟着**同一个置换**走,否则因子会
    配错 bar。而 `data.py` 里做这件事的那一行**本身不改变任何已有门的
    结果** —— 把它删掉,既有全套测试仍全绿(实测 550 passed),因为既有的
    乱序测试都没有 `Adj Close`,而既有的 `Adj Close` 测试都是有序的。
    这条门把两者**交叉**起来。

    fixture 的三条必要条件(缺一则测不出错配):
      1. cumfactor 必须**逐根互不相同**(0.125 / 0.4 / 0.8 / 1.0)。
         对称 fixture(如 0.5 / 0.5 / 1.0)实测两侧都是 [100, 100, 100],
         置换后仍相等 —— **测不出**。
      2. 断言必须落在**中间**各行上:后复权归一化让末行恒为 1.0
         (ADR-050),错配在末行上**不可见**(实测错序列末位仍是 100.0)。
      3. 首行不得是最早的日期,否则排序是恒等置换,无事可错。
    """
    F = C["fixtures"]["adr019_adj_close_follows_the_sort"]
    days = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
    bars = list(zip(days, F["raw_closes"]))
    adj = F["adj_closes"]
    order = F["shuffled_row_order"]

    # 前提自检:置换非恒等,且 cumfactor 逐根互不相同
    assert order != sorted(order), "乱序 fixture 必须真的乱序"
    assert len(set(F["cumfactors"])) == len(F["cumfactors"]), (
        "cumfactor 必须逐根互不相同,否则排序错配不可见"
    )
    assert F["cumfactors"][-1] == 1.0, "后复权:末行 cumfactor 恒为 1.0"

    sorted_csv = _write_prices_in_row_order(
        tmp_path, bars, adj, list(range(len(bars))), "sorted.csv"
    )
    shuffled_csv = _write_prices_in_row_order(
        tmp_path, bars, adj, order, "shuffled.csv"
    )

    with no_warnings():
        warnings.simplefilter("ignore", UnsortedDateWarning)
        hand_sorted = load_csv(sorted_csv)
    with pytest.warns(UnsortedDateWarning):      # ADR-019:排序 + 提示,非错误
        shuffled = load_csv(shuffled_csv)

    # ① 乱序版 == 手工排序版,四列逐值相同(「排序后等价」的本体)
    for column in PRICE_COLUMNS:
        assert shuffled[column].tolist() == hand_sorted[column].tolist(), (
            f"column={column}:乱序 CSV 必须与手工排序版逐值相同"
        )
    assert shuffled["Date"].tolist() == hand_sorted["Date"].tolist()
    assert list(shuffled.index) == list(range(len(bars))), "index 必须是 0..n-1"
    assert "Adj Close" not in shuffled.columns      # 门 8① 在乱序下同样成立

    # ② 钉死**绝对值**,而非只钉两侧相等 —— 否则「两侧都错成同一个值」
    #    的实现也能过。复权后收盘恒等于 Adj Close(这正是反解的定义)。
    assert shuffled["Close"].tolist() == F["expect_adjusted_closes"]

    # ③ 明确拒绝「漏掉置换重对齐」的实现读出的那条错序列(实测值)。
    #    注意它的**末位仍是 100.0** —— 这就是为什么 ② / ③ 必须看中间各行。
    reject = F["reject_adjusted_closes"]
    assert shuffled["Close"].tolist() != reject
    assert reject[-1] == F["expect_adjusted_closes"][-1], (
        "错序列末位与正确值相同 —— 归一化会藏住末行的错配"
    )
    for i in range(len(bars) - 1):
        assert shuffled["Close"].iloc[i] != pytest.approx(reject[i]), (
            f"row={i}:读到了错配的因子"
        )

    # ④ 四列同乘同一个因子,在乱序下也成立(门 2 的不变量)
    for i, cumfactor in enumerate(F["cumfactors"]):
        raw_close = F["raw_closes"][i]
        assert shuffled["Close"].iloc[i] == pytest.approx(raw_close * cumfactor)
        assert shuffled["Open"].iloc[i] == pytest.approx(raw_close * cumfactor)
        assert shuffled["High"].iloc[i] == pytest.approx(
            raw_close * 1.02 * cumfactor
        )
        assert shuffled["Low"].iloc[i] == pytest.approx(
            raw_close * 0.98 * cumfactor
        )


# ═══════════════════ 门 9 / 10:两路仲裁 ═══════════════════


def test_both_paths_agree_uses_the_events_table(tmp_path):
    """门 9 前半:两路一致 → **用行为表**(ADR-052:信息更多,
    带事件类型与金额,可审计)。"""
    bars = [("2020-01-02", 400.0), ("2020-01-03", 100.0)]
    # Adj Close 表达同一次 4:1 拆股
    prices = write_prices(tmp_path, bars, adj=[100.0, 100.0])
    events = write_events_file(
        tmp_path, [split_row("2020-01-03", SPLITF["forward_4_for_1"])]
    )

    df = load_csv(prices, events=events)
    # 两路给出同一组绝对价格 → 结果与单用任一路相同
    assert df["Close"].iloc[0] == pytest.approx(100.0)
    assert df["Close"].iloc[-1] == pytest.approx(100.0)


def test_paths_disagreeing_beyond_tol_is_an_error(tmp_path):
    """门 9 后半:**不一致超 `tol` → 报错**,消息指明**哪一天、差多少**。

    「两个独立来源对不上,**本身就是**『假的赚钱』的 signal」(ADR-052)
    —— 静默选一个等于把它藏起来。
    """
    # 价格里确实是一次 4:1 拆股(400 → 100),行为表也如实写了 4.0
    # → **方向门成立**(否则会先在那条门上失败,本门就测了别的东西)。
    # 但 `Adj Close` 额外折进了**一笔行为表漏掉的分红**(×0.97):
    # 路径一的首行累积因子 = 0.25×0.97,路径二 = 0.25 → 两路相差 3%。
    # 「行为表漏事件」正是 ADR-052 列举的、两路互校要抓的头号情形。
    bars = [("2020-01-02", 400.0), ("2020-01-03", 100.0)]
    prices = write_prices(tmp_path, bars, adj=[400.0 * 0.25 * 0.97, 100.0])
    events = write_events_file(
        tmp_path, [split_row("2020-01-03", SPLITF["forward_4_for_1"])]
    )

    with pytest.raises(DataValidationError) as excinfo:
        load_csv(prices, events=events)

    message = str(excinfo.value)
    assert "2020-01-02" in message, "消息须指明哪一天"
    # 「差多少」:相对偏差与容差都要出现在消息里
    assert str(FACTOR_AGREEMENT_TOL) in message or "1e-04" in message
    assert "不一致" in message


def test_arbitration_tol_tolerates_vendor_float_noise(tmp_path):
    """门 10:**仲裁 tol 不可取 `1e-6`** —— 构造一个带浮点噪声的 `ratio`
    (`7.000007000007001`,Tiingo 存 7:1 的实际值)的**一致**用例,
    断言它**通过**而非误报。

    `1e-4` 够宽以容纳厂商噪声,够紧以抓住真实缺口:漏一笔拆股是
    **数量级**偏差,漏一笔分红约 0.5%~2%(ADR-052)。
    """
    noisy, clean = ARBITRATION["noisy_ratio"], ARBITRATION["clean_ratio"]
    bars = [("2020-01-02", 700.0), ("2020-01-03", 100.0)]
    # Adj Close 用**干净**的 7.0 折平历史,行为表用**带噪声**的 ratio
    prices = write_prices(tmp_path, bars, adj=[100.0, 100.0])
    events = write_events_file(tmp_path, [split_row("2020-01-03", noisy)])

    # 不报错 —— 这就是本门的断言
    df = load_csv(prices, events=events)
    assert df["Close"].iloc[0] == pytest.approx(700.0 / noisy)

    # 且实测相对偏差确实落在两个容差之间(钉死「为什么 1e-6 不行」)
    dev = abs(1.0 / noisy - 1.0 / clean) / (1.0 / clean)
    assert dev == pytest.approx(ARBITRATION["measured_rel_dev"], rel=1e-9)
    assert dev < FACTOR_AGREEMENT_TOL, "1e-4 必须容得下"
    assert dev >= 1e-6, "若取 1e-6 则本用例会被误报 —— 这正是不取它的理由"


# ═══════════ 门 11:方向门(ADR-054,本特性价值最高的一条) ═══════════


def test_direction_gate_accepts_a_correctly_written_ratio(tmp_path):
    """门 11 肯定侧:每笔 split 断言 `close[ex]/close[ex-1] ≈ 1/ratio`。"""
    ratio = DIRECTION["true_ratio"]
    bars = [("2020-01-02", 400.0), ("2020-01-03", 100.0)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(tmp_path, [split_row("2020-01-03", ratio)])

    df = load_csv(prices, events=events)      # 不报错
    assert df["Close"].iloc[0] == pytest.approx(100.0)


def test_direction_gate_catches_a_reversed_ratio_convention(tmp_path):
    """门 11:构造一个 `ratio` 写成**倒数**(`0.25` 而非 `4.0`)的文件,
    断言**报错**。

    **该门是唯一能抓到约定写反(16 倍误差)的机制。** 约定 A(前向股数比)
    与 B(价格乘数)**互为倒数**:4:1 读成 B 得 `f = 4` 而非 `0.25`,
    即对拆股前全部历史造成 **16 倍**误差,**且不抛任何异常** ——
    **没有任何格式级校验能发现它**(ADR-053)。
    """
    reversed_ratio = DIRECTION["reversed_ratio"]
    # 价格确实是 4:1 拆股(400 → 100),但 ratio 按约定 B 写成 0.25
    bars = [("2020-01-02", 400.0), ("2020-01-03", 100.0)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(tmp_path, [split_row("2020-01-03", reversed_ratio)])

    with pytest.raises(DataValidationError) as excinfo:
        load_csv(prices, events=events)

    message = str(excinfo.value)
    assert "2020-01-03" in message
    assert "方向门" in message
    # 消息须把「约定 A / 前向股数比」说出来,否则用户不知道怎么改
    assert "前向股数比" in message


# ═══════════════════ 门 12 / 13:量级门与因子正性 ═══════════════════


def test_dividend_magnitude_gate_rejects_an_implausible_yield(tmp_path):
    """门 12 肯定侧:`amount / close_before` 超过
    `thresholds.dividend_yield_suspicion` → **报错**。

    抓 Yahoo/Sharadar 那类 5 倍膨胀(膨胀后股息率会荒谬,ADR-054 门二)。
    """
    close_before = MAGNITUDE["close_before"]
    bars = [("2020-01-02", close_before), ("2020-01-03", close_before)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(
        tmp_path, [dividend_row("2020-01-03", MAGNITUDE["reject_amount"])]
    )

    with pytest.raises(DataValidationError) as excinfo:
        load_csv(prices, events=events)
    assert "2020-01-03" in str(excinfo.value)
    assert str(DIVIDEND_YIELD_SUSPICION) in str(excinfo.value)


def test_dividend_magnitude_gate_accepts_a_yield_below_the_threshold(tmp_path):
    """门 12 **否定用例(必须配)**:比值在阈值**以下**(0.24)→ **通过**。

    **否则一个「对每笔分红都报错」的实现也能通过本门。**
    """
    close_before = MAGNITUDE["close_before"]
    amount = MAGNITUDE["accept_amount"]
    bars = [("2020-01-02", close_before), ("2020-01-03", close_before - amount)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(tmp_path, [dividend_row("2020-01-03", amount)])

    df = load_csv(prices, events=events)       # 不报错 —— 本门的断言
    assert df["Close"].iloc[0] == pytest.approx(
        close_before * (1.0 - amount / close_before)
    )
    # 前提自检:这个比值确实在阈值之下(否则本门测的是别的东西)
    assert amount / close_before < DIVIDEND_YIELD_SUSPICION


def test_amount_equal_to_close_before_is_an_error(tmp_path):
    """门 13:`amount >= close_before` → **报错**。

    因子 `<= 0` 会把历史价**归零或变号**。(zipline 对此只是 log-and-drop;
    按 ADR-019 的响亮失败姿态,我们报错。)
    """
    close_before = MAGNITUDE["close_before"]
    bars = [("2020-01-02", close_before), ("2020-01-03", close_before)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(
        tmp_path, [dividend_row("2020-01-03", MAGNITUDE["zero_factor_amount"])]
    )

    with pytest.raises(DataValidationError) as excinfo:
        load_csv(prices, events=events)
    assert "amount >= close_before" in str(excinfo.value)


# ═══════════════════ 门 14 / 15:跳空体检(两路缺席) ═══════════════════


def test_gap_health_check_warns_and_does_not_adjust(tmp_path):
    """门 14:两路缺席 + 一个 −75% 隔夜跳空 → **发出提示**(`warnings`,
    非异常);且断言**价格未被调整**(体检不调价)。

    4:1 拆股的签名**就是**一个 −75% 的隔夜跳空,而 ADR-019 的六条校验
    **没有一条**能看见拆股(非 NaN、非负、非零、日期不重复、单调递增,
    六条门全过)—— ADR-001 主张 D 的「该前提不可验证」是**事实错误**。
    """
    bars = [("2020-01-02", 400.0), ("2020-01-03", 100.0)]
    prices = write_prices(tmp_path, bars)      # 无 Adj Close、不传 events

    with pytest.warns(UnadjustedPriceWarning) as record:
        df = load_csv(prices)

    assert "嫌疑未复权" in str(record[0].message)
    # **价格未被调整** —— 体检是提示,不是诊断
    assert df["Close"].tolist() == [400.0, 100.0]
    assert df["Open"].tolist() == [400.0, 100.0]


def test_gap_health_check_fires_exactly_at_the_boundary(tmp_path):
    """门 15(a):**5:4 拆股**(跳空 `−19.999999999999996%`,`round` 后恰为
    `−20.0`)→ **必须发出提示**。

    **这条钉死运算符为 `>=`;写成 `>` 则本门必红。**
    5:4 是真正的边界:它算出 `-0.19999999999999996`,严格 `> 0.20` 为**假**;
    而 `round(x, 2)` 得**恰好** `-20.0`,`> 20.0` 也为**假** ——
    **两条路径都漏掉 5:4**,是因为浮点表示而非因为决策(ADR-051)。
    """
    ratio = HEALTH["boundary_ratio"]
    price_before = 100.0
    bars = [("2020-01-02", price_before), ("2020-01-03", price_before / ratio)]
    prices = write_prices(tmp_path, bars)

    # 前提自检:这个跳空 round 后确实**恰为** 边界值
    gap_pct = round((price_before / ratio / price_before - 1.0) * 100.0, 2)
    assert gap_pct == HEALTH["boundary_gap_pct"]
    assert abs(gap_pct) >= GAP_SUSPICION_PCT, "`>=` 取到边界"
    assert not abs(gap_pct) > GAP_SUSPICION_PCT, "`>` 会漏掉它 —— 这就是本门的理由"

    with pytest.warns(UnadjustedPriceWarning):
        load_csv(prices)


def test_gap_below_the_threshold_is_quiet(tmp_path):
    """门 15(b):−15% 跳空 → 断言**无**提示。

    与上一条成对:只测「必须发出」无法区分 `>=` 与「无条件发出」。
    """
    bars = [("2020-01-02", 100.0), ("2020-01-03", 85.0)]
    prices = write_prices(tmp_path, bars)

    gap_pct = round((85.0 / 100.0 - 1.0) * 100.0, 2)
    assert gap_pct == HEALTH["quiet_gap_pct"]

    with no_warnings() as record:
        warnings.simplefilter("always")
        load_csv(prices)
    assert not [w for w in record if issubclass(w.category, UnadjustedPriceWarning)]


def test_no_health_check_when_a_factor_path_is_present(tmp_path):
    """体检只在**两路缺席**时跑(ADR-051 的前置条件)。

    传了行为表 → 价格已被复权,那个 −75% 跳空是**被解释过**的,
    再发提示就是噪声。没有这条门,一个「无条件体检」的实现也能通过门 14。
    """
    bars = [("2020-01-02", 400.0), ("2020-01-03", 100.0)]
    prices = write_prices(tmp_path, bars)
    events = write_events_file(
        tmp_path, [split_row("2020-01-03", SPLITF["forward_4_for_1"])]
    )

    with no_warnings() as record:
        warnings.simplefilter("always")
        load_csv(prices, events=events)
    assert not [w for w in record if issubclass(w.category, UnadjustedPriceWarning)]


# ═══════════════════ 门 17:加性保证(最高优先) ═══════════════════


def test_no_events_and_no_adj_close_is_byte_identical(tmp_path):
    """门 17:`--events` 与 `Adj Close` **均缺席**时,`load_csv` 的输出与
    本任务**之前逐值相同**。

    **这是本任务最高优先的一条门**:`data/synthetic.csv` 的表头是
    `Date,Open,High,Low,Close,Volume`(无 `Adj Close`),且 `tests/` 下大量
    内联 CSV 同形 —— 缺席即改行为会是破坏性变更,违反 §7 第 1 条。
    """
    bars = [("2020-01-02", 100.0), ("2020-01-03", 104.0), ("2020-01-06", 102.0)]
    prices = write_prices(tmp_path, bars)

    df = load_csv(prices)

    # 逐值等于 CSV 原值 —— 一个因子都没乘
    assert df["Close"].tolist() == [100.0, 104.0, 102.0]
    assert df["Open"].tolist() == [100.0, 104.0, 102.0]
    assert df["High"].tolist() == [c * 1.02 for _, c in bars]
    assert df["Low"].tolist() == [c * 0.98 for _, c in bars]
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert list(df.index) == [0, 1, 2]


def test_real_synthetic_csv_is_untouched_and_quiet():
    """门 17 续 + REAL_STACK_GATE ①:用**真实的** `data/synthetic.csv`
    (2516 行,无 `Adj Close`)断言 ① **无提示**、② 价格**逐点**等于原值。

    **不可只在内存 DataFrame 上验**(T21 门原文)—— 须用路径调 `load_csv`。
    """
    path = ROOT / "data" / "synthetic.csv"
    if not path.is_file():                      # pragma: no cover
        pytest.skip("data/synthetic.csv 不在工作树内(它由 .gitignore 排除)")

    import csv

    with no_warnings() as record:
        warnings.simplefilter("always")
        df = load_csv(path)
    assert not [
        w for w in record if issubclass(w.category, UnadjustedPriceWarning)
    ], "真实数据不得触发体检提示(它没有拆股签名)"

    with path.open(encoding="utf-8") as handle:
        raw_rows = list(csv.DictReader(handle))
    assert len(df) == len(raw_rows)
    for column in PRICE_COLUMNS:
        assert df[column].tolist() == [float(r[column]) for r in raw_rows], (
            f"column={column} 必须逐点等于 CSV 原值"
        )


# ═══════════════════ 门 18 / 19:签名与下游零影响 ═══════════════════


def test_load_csv_signature_is_unchanged(tmp_path):
    """门 18:ADR-035 钉死的 `load_csv` 签名**未变**。

    `path` 仍是**第一个位置参数**,且 `load_csv(path)` 单参调用仍合法;
    `events` 是**仅关键字**的可选加性参数(与 T20 给 `load_events` 加
    `price_dates=` 同构),故 ADR-035 的名字契约未被触碰。
    """
    import inspect

    params = inspect.signature(load_csv).parameters
    assert list(params)[0] == "path"
    assert params["path"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params["path"].default is inspect.Parameter.empty
    # 加性:新参数必须是仅关键字**且**有默认值
    assert params["events"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["events"].default is None

    # 单参调用仍合法(ADR-035 的调用形态)
    prices = write_prices(tmp_path, [("2020-01-02", 100.0)])
    assert len(load_csv(prices)) == 1


def test_read_csv_still_appears_only_in_data_py():
    """门 19:`pd.read_csv` 全库仍**仅 `data.py` 一处**。

    这是 ADR-050「下游零影响是**结构性**保证而非约定」的可执行投影:
    只要数据入口唯一,下游就**没有那个接缝**去分辨价格是否被调整过。
    """
    offenders = []
    for path in ROOT.rglob("*.py"):
        if any(part in {"out", "frontend", ".git"} for part in path.parts):
            continue
        if path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8")
        if "read_csv" in text and path.name != "data.py":
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"`read_csv` 出现在 data.py 之外:{offenders}"


def test_bar_like_still_declares_only_open_and_date():
    """门 19 续:`BarLike` 仍**只声明** `Open` 与 `Date`。

    引擎只读这两个 → 它**无从分辨**价格是否被复权过(ADR-050)。
    若将来有人往 `BarLike` 里加 `Adj Close`,那个结构性保证就破了。
    """
    import nullhypothesis.engine as engine

    # `BarLike` 是 `Protocol`,成员以 `@property` 声明 → 读 `__annotations__`
    # 会是空的;协议的成员名在 `__protocol_attrs__`(3.12)里。
    declared = getattr(engine.BarLike, "__protocol_attrs__", None)
    if declared is None:                        # pragma: no cover
        declared = {
            name for name in vars(engine.BarLike)
            if not name.startswith("_")
        }
    assert set(declared) == {"Open", "Date"}, (
        f"BarLike 的字段集已偏离 ADR-050 的前提:{sorted(declared)}"
    )


# ═══════════════════ 阈值必须来自 contracts.yaml,不得是裸字面量 ═══════════════════


def test_thresholds_match_contracts_yaml():
    """T21 门 12 的原话:「阈值**不可写成裸字面量**…参照 OQ-11 对跳空阈值
    的处置」。故 `data.py` 的三个常量必须与 `contracts.yaml` 逐值一致。

    没有这条门,改 `contracts.yaml` 而不改 `data.py`(或反之)会让两处
    悄悄漂移 —— 那正是本文件开头记载的「同一个值多处复述」缺陷。
    """
    assert GAP_SUSPICION_PCT == THRESHOLDS["gap_suspicion_pct"]
    assert FACTOR_AGREEMENT_TOL == THRESHOLDS["factor_agreement_tol"]
    assert DIVIDEND_YIELD_SUSPICION == THRESHOLDS["dividend_yield_suspicion"]


def test_gap_threshold_is_the_oq11_value():
    """OQ-11:阈值默认 **20%**,**构建期不得改**。

    「若将来要调,须重新讨论本 OQ 而非直接改常数」—— 故这条门是那句话的
    可执行形式。它同时钉住 ADR-051 的拆股对照表:2:1 与 3:2 抓得到,
    6:5 与 10:9 抓不到。
    """
    assert GAP_SUSPICION_PCT == 20.0

    def caught(ratio: float) -> bool:
        return abs(round((1.0 / ratio - 1.0) * 100.0, 2)) >= GAP_SUSPICION_PCT

    assert caught(2.0), "2:1 = −50% 必须抓到"
    assert caught(1.5), "3:2 = −33.3% 必须抓到"
    assert caught(HEALTH["boundary_ratio"]), "5:4 是边界,`>=` 必须抓到"
    assert caught(0.25), "1:4 合股 = +300% 必须抓到"
    assert not caught(1.2), "6:5 = −16.7% 抓不到(已声明的漏报)"
    assert not caught(10 / 9), "10:9 = −10% 抓不到(已声明的漏报)"


# ═══════════ REAL_STACK_GATE ②:带真实 `Adj Close` 的多年 CSV 跑完整 CLI ═══════════


def test_cli_with_adj_close_matches_hand_computed_total_return(tmp_path):
    """REAL_STACK_GATE ②:构造一份**带真实 `Adj Close` 列**的多年 CSV
    (含**至少一次拆股与两笔分红**),跑**完整 CLI**,断言复权后
    `Summary.total_return_pct` 与**手算值**一致。

    **真起子进程** —— 不在内存 DataFrame 上验(T21 门原文的硬要求:
    `tests/test_data.py` 开头已记明理由,须用路径调 `load_csv`)。
    """
    import json
    import subprocess
    import sys

    # ── 构造多年原始价格 + 与之自洽的 Adj Close ──────────────────────────
    # 事件:2021-03-01 除息 1.00、2022-06-01 4:1 拆股、2023-09-01 除息 0.50
    bars = [
        ("2020-01-02", 100.0),
        ("2021-02-26", 100.0),   # 除息前一交易日 → close_before = 100.0
        ("2021-03-01", 99.0),    # 除息 1.00 → f = 0.99
        ("2022-05-31", 400.0),   # 拆股前一交易日
        ("2022-06-01", 100.0),   # 4:1 拆股 → f = 0.25
        ("2023-08-31", 100.0),   # 除息前一交易日 → close_before = 100.0
        ("2023-09-01", 99.5),    # 除息 0.50 → f = 0.995
        ("2023-12-29", 120.0),
    ]
    f_div1, f_split, f_div2 = 0.99, 0.25, 0.995
    # cumfactor_t = ∏ 严格晚于 t 的事件因子(后复权,末行为 1.0)
    cumfactors = [
        f_div1 * f_split * f_div2,   # 2020-01-02:三个事件都晚于它
        f_div1 * f_split * f_div2,   # 2021-02-26:同上
        f_split * f_div2,            # 2021-03-01:除息日**当根**不被自己调整
        f_split * f_div2,            # 2022-05-31
        f_div2,                      # 2022-06-01:拆股日当根
        f_div2,                      # 2023-08-31
        1.0,                         # 2023-09-01:除息日当根,之后无事件
        1.0,                         # 2023-12-29
    ]
    adj = [c * f for (_, c), f in zip(bars, cumfactors)]

    data_dir = ROOT / "data"
    price_name = "t21_real_stack_prices.csv"
    events_name = "t21_real_stack_events.csv"
    price_path = data_dir / price_name
    events_path = data_dir / events_name
    write_prices(data_dir, bars, name=price_name, adj=adj)
    write_events_file(data_dir, [
        dividend_row("2021-03-01", 1.00),
        split_row("2022-06-01", 4.0),
        dividend_row("2023-09-01", 0.50),
    ], name=events_name)

    try:
        out_dir = tmp_path / "out"
        proc = subprocess.run(
            [sys.executable, "backtest.py",
             "--data", f"data/{price_name}",
             "--strategy", "strategies/buy_and_hold.py",
             "--events", f"data/{events_name}",
             "--out", str(out_dir)],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert proc.returncode == C["exit_codes"]["ok"], proc.stderr

        run_dirs = [d for d in out_dir.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1
        summary = json.loads(
            (run_dirs[0] / C["filenames"]["summary_json"]).read_text("utf-8")
        )
    finally:
        price_path.unlink(missing_ok=True)
        events_path.unlink(missing_ok=True)

    # ── 手算值 ───────────────────────────────────────────────────────────
    # 买入持有:T 日(首根)意图 → T+1 开盘成交(ADR-016)。
    # 复权后 Open_1 == Close_1 × cumfactor_1(write_prices 让 Open == Close)。
    adjusted_open_1 = bars[1][1] * cumfactors[1]
    adjusted_last_close = bars[-1][1] * cumfactors[-1]
    shares = int(100000.0 // adjusted_open_1)
    cash_left = 100000.0 - shares * adjusted_open_1
    final_equity = cash_left + shares * adjusted_last_close
    expected_pct = round((final_equity / 100000.0 - 1.0) * 100.0, 2)

    results = summary["results"] if "results" in summary else summary
    actual = (
        results[0]["summary"]["total_return_pct"]
        if isinstance(results, list)
        else results["total_return_pct"]
    )
    assert actual == pytest.approx(expected_pct, abs=0.01), (
        f"复权后的 total_return_pct 与手算值不符:{actual} vs {expected_pct}"
    )

    # 两路并存且一致 —— 若 Adj Close 与行为表对不上,CLI 早就以退出码 2 失败了
    assert proc.returncode == 0


# ═══════════════════ 门 16 的指向:三条 ADR-004 老门已反写 ═══════════════════


def test_the_three_adr004_gates_have_been_rewritten():
    """门 16:**三条 ADR-004 老门已反写**,且反写后的新断言与 ADR-050 一致。

    ADR-050 的原话:「`data.py` 的 `EXCLUDED_COLUMNS` 机制与
    `tests/test_data.py` 的三条门**必须反写** —— 它们会把新行为判为失败。
    **这是本步真正的工作量所在,不是复权算术(算术是四行乘法)。**」

    本门断言那三个**老名字**已不存在、三个**新名字**在位 —— 否则「反写」
    可能被实现成「加三条新门、把老门留在那里 xfail/skip」,
    而那是 §7 与根因纪律都禁止的自欺。
    """
    text = (ROOT / "tests" / "test_data.py").read_text(encoding="utf-8")

    for retired in (
        "def test_adj_close_column_is_not_read(",
        "def test_adj_close_does_not_change_result(",
        "def test_adj_close_garbage_is_ignored(",
    ):
        assert retired not in text, f"ADR-004 的老门仍在:{retired}"

    for rewritten in (
        "def test_adj_close_is_absent_from_the_returned_frame(",
        "def test_adj_close_does_change_the_result(",
        "def test_adj_close_garbage_falls_back_to_no_adjustment(",
    ):
        assert rewritten in text, f"反写后的门缺失:{rewritten}"

    # 且没有被 skip/xfail 掩盖
    assert "xfail" not in text and "skipif" not in text, (
        "反写不得用 xfail/skip 掩盖 —— 那是根因纪律禁止的自欺"
    )


def test_corporate_event_factors_are_computable_without_a_file(tmp_path):
    """`cumfactors_from_events` 是 ADR-035 自检规则二要求的**公开**接缝:
    T21 的门 4/9/10 都要直接采样**累积因子序列**本身,而不是调整后的价格。

    这里顺带断言它接受一个手工构造的 `CorporateEvent` 序列 ——
    使那几条门不必经由文件往返。
    """
    prices = write_prices(tmp_path, [
        ("2020-01-02", 400.0), ("2020-01-03", 100.0),
    ])
    df = load_csv(prices)
    events = [CorporateEvent(
        date=date(2020, 1, 3), event="split", ratio=4.0, amount=None,
    )]
    assert cumfactors_from_events(df, events) == pytest.approx([0.25, 1.0])
