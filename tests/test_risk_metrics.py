"""T23 的验收门 · 夏普比率 + 最大回撤(ADR-058 / ADR-059 / ADR-044 / ADR-038)。

**期望值一律引用 `contracts.yaml` 的 `fixtures.adr058_metrics.<key>`**,本文件
不复述任何字面量 —— 这是 `contracts.yaml` 开头那条规则(由
`tests/test_design_binding.py::test_numeric_fixtures_are_not_restated` 强制)。

**T6 + T7 + T8 + T9b 是让这成为真门而非 happy-path 的四条。** 缺任何一条,
一个错误实现都能全绿通过:
  * 缺 T6 → `np.std` 的默认 `ddof=0` 被静默接受(夏普按 √((n-1)/n) 放大);
  * 缺 T7 → `sigma == 0` 的判定式漏掉 1.28e-16 的浮点噪声,输出 1.2e16;
  * 缺 T8 → 看不出上一条**依赖数据规模**(起点 1e6 时 sigma 恰为 0.0);
  * 缺 T9b → `cash > 0` 的持仓情形(`weight=1.0` 成交后的**常态**)无人覆盖。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml

from nullhypothesis.run import (
    SHARPE_DEGENERATE_EPS,
    TRADING_DAYS_PER_YEAR,
    ROUNDING_DECIMALS,
    compute_max_drawdown,
    compute_sharpe,
    run,
)
from nullhypothesis.data import load_csv

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))
FX = CONTRACTS["fixtures"]["adr058_metrics"]
FORMATS = CONTRACTS["formats"]
FIELD_FORMATS = CONTRACTS["field_formats"]


def _dates(n: int) -> list[str]:
    """n 个**升序**日期字符串。回撤的峰/谷日期就从这里取。"""
    return [f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]


def _sharpe(equity) -> float | None:
    s = compute_sharpe(equity)
    return None if s is None else round(s, ROUNDING_DECIMALS)


def _mdd(equity):
    d = _dates(len(equity))
    pct, peak, trough = compute_max_drawdown(equity, d)
    bar = {v: i for i, v in enumerate(d)}
    return (
        round(pct, ROUNDING_DECIMALS),
        None if peak is None else bar[peak],
        None if trough is None else bar[trough],
    )


# ═══════════════════ 门 2 / 3 · T1 与 T2 ═══════════════════


def test_t1_single_drawdown_then_recovery():
    """门 2 · T1:一次 -10% 回撤后反弹。四个值全部钉死。"""
    eq = FX["t1_equity"]
    assert _sharpe(eq) == FX["t1_sharpe"]
    pct, peak, trough = _mdd(eq)
    assert pct == FX["t1_max_drawdown_pct"]
    assert peak == FX["t1_peak_bar"]
    assert trough == FX["t1_trough_bar"]


def test_t2_peak_to_trough_then_new_high():
    """门 3 · T2:峰 120 谷 90,**之后创新高** —— 回撤仍取那一段。"""
    eq = FX["t2_equity"]
    assert _sharpe(eq) == FX["t2_sharpe"]
    pct, peak, trough = _mdd(eq)
    assert pct == FX["t2_max_drawdown_pct"]
    assert peak == FX["t2_peak_bar"]
    assert trough == FX["t2_trough_bar"]


# ═══════════════════ 门 4 · T3 从不交易(⭐)═══════════════════


def test_t3_idle_strategy_sharpe_is_none_not_zero(tmp_path):
    """门 4 · T3 ⭐:`Idle` 策略净值恒定 → `sharpe is None`,**不是 `0.0`**。

    `0.0` 是一种**特定的谎言**:它意思是「承担了风险但没赚到超额收益」,
    而真相是「**没承担风险**,比率未定义」(`0/0`)。`Idle` 类策略在本仓库
    测试里是**一等路径**,非奇异情形。

    本门走**完整 `run()`**(不是直接调纯函数)—— 它同时钉住「计算确实接在
    `Summary` 构建处」。
    """
    csv = tmp_path / "d.csv"
    rows = ["Date,Open,High,Low,Close,Volume"]
    # 价格**在动**,而策略不交易 ⇒ equity 恒定。这正是「夏普必须看 equity
    # 而不是 close」的可执行形式:若实现从 close 算,这里会给一个非 None 值。
    for i, (d, c) in enumerate(zip(_dates(4), [100.0, 130.0, 90.0, 115.0])):
        rows.append(f"{d},{c},{c + 1},{c - 1},{c},1000")
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")

    strat = tmp_path / "idle.py"
    strat.write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "class S(Strategy):\n"
        "    def next(self):\n        pass\n",
        encoding="utf-8",
    )

    s = run([strat], load_csv(csv), cash=100000.0)[0].summary
    assert s.sharpe is FX["t3_sharpe"] is None
    assert s.max_drawdown_pct == FX["t3_max_drawdown_pct"]
    assert s.max_drawdown_peak_date is None
    assert s.max_drawdown_trough_date is None
    assert s.trade_count == FX["t3_trade_count"]


# ═══════════════════ 门 5 / 6 · T4 单调上涨、T5 两行 ═══════════════════


def test_t4_monotonic_rise_has_zero_drawdown_and_null_dates():
    """门 5 · T4:单调上涨 → 回撤 `0.0`,**但两个日期 `None`**。

    给日期等于断言发生了一次没发生的回撤。
    """
    eq = FX["t4_equity"]
    assert _sharpe(eq) == FX["t4_sharpe"]
    pct, peak, trough = _mdd(eq)
    assert pct == FX["t4_max_drawdown_pct"]
    assert peak is None and trough is None


def test_t5_two_row_csv_short_circuits_before_std():
    """门 6 · T5:`len(r) < 2` → `None`。

    2 行 CSV 两种 `ddof` 都退化(`ddof=1` 给 `nan`、`ddof=0` 给 `0/0`),
    故**短路、不调用 std**。返回 `None` 而非 `NaN`:`NaN` 不是合法 JSON。
    """
    eq = FX["t5_equity"]
    assert compute_sharpe(eq) is FX["t5_sharpe"] is None
    pct, peak, trough = _mdd(eq)
    assert pct == FX["t5_max_drawdown_pct"]
    assert peak is None and trough is None


# ═══════════════════ 门 7 · T6 ddof 判别(⭐)═══════════════════


def test_t6_ddof_is_literally_one_not_the_library_default():
    """门 7 · T6 ⭐:`ddof=1`(ADR-058)而非 `np.std` 的默认 `ddof=0`。

    两个值必须**可分辨** —— 否则这条门是同义反复。
    """
    eq = FX["t6_equity"]
    got = _sharpe(eq)
    assert got == FX["t6_sharpe"]
    assert got != FX["t6_reject_sharpe_ddof0"]
    assert FX["t6_sharpe"] != FX["t6_reject_sharpe_ddof0"], "门必须双向可证伪"


def test_t6_ddof0_implementation_would_be_rejected():
    """同一条门的**反向**:显式算出 `ddof=0` 的值,确认它就是被拒的那个。

    这把「约定是 ddof=1」从注释变成可执行断言 —— 若实现悄悄改用 `ddof=0`,
    上一条门会红,而本条说明它会红成什么样。
    """
    eq = FX["t6_equity"]
    r = [eq[k + 1] / eq[k] - 1.0 for k in range(len(eq) - 1)]
    mean = sum(r) / len(r)
    # ddof=0:除以 n 而不是 n-1
    sigma0 = math.sqrt(sum((x - mean) ** 2 for x in r) / len(r))
    naive = round(mean / sigma0 * math.sqrt(TRADING_DAYS_PER_YEAR), ROUNDING_DECIMALS)
    assert naive == FX["t6_reject_sharpe_ddof0"]


# ═══════════════════ 门 8 / 9 · T7 与 T8(⭐ 规模无关)═══════════════════


def test_t7_floating_point_noise_sigma_is_suppressed():
    """门 8 · T7 ⭐:恒定比率增长 → `sharpe is None`,守护 `1.2e16` bug。

    **必须断言 `is None`,不可只断言「不太大」** —— 后者会接受一个
    「碰巧没那么大」的错误实现。
    """
    eq = FX["t7_equity"]
    assert compute_sharpe(eq) is FX["t7_sharpe"] is None

    # 并钉住**为什么** `sigma == 0` 的判定式抓不到它:sigma 不等于 0。
    r = [eq[k + 1] / eq[k] - 1.0 for k in range(len(eq) - 1)]
    mean = sum(r) / len(r)
    sigma = math.sqrt(sum((x - mean) ** 2 for x in r) / (len(r) - 1))
    assert (sigma == 0.0) is FX["t7_sigma_is_exactly_zero"] is False
    assert sigma > 0.0
    # 它确实落在守卫之内 —— 这是本门与 T8 共享的那条规则
    assert sigma <= SHARPE_DEGENERATE_EPS * max(1.0, abs(mean))


def test_t8_same_ratio_at_large_scale_is_also_suppressed():
    """门 9 · T8 ⭐:与 T7 同比例、起点 1e6 → 同样 `None`。

    与 T7 **配对**证明规则**与数据规模无关**:T8 的 sigma **恰为 `0.0`**、
    T7 为 1.28e-16,同一条判定式须覆盖两者。这正是那个 bug 之所以
    「静默、数据依赖、难复现」的原因 —— 它在某些输入上发作、另一些上不发作。
    """
    eq = FX["t8_equity"]
    assert compute_sharpe(eq) is FX["t8_sharpe"] is None

    r = [eq[k + 1] / eq[k] - 1.0 for k in range(len(eq) - 1)]
    mean = sum(r) / len(r)
    sigma = math.sqrt(sum((x - mean) ** 2 for x in r) / (len(r) - 1))
    assert (sigma == 0.0) is FX["t8_sigma_is_exactly_zero"] is True


# ═══════════════════ 门 9b · 持仓余现金(⭐ 第四条真门)═══════════════════


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OQ-17【设计期未决】:绝对上限兜底 SHARPE_REPORTABLE_CEILING 的取值"
        "(以及是否同时发警告)须与 ADR-059 的诚实声明一并定稿,"
        "且 DESIGN.md §14 明文规定【构建期不得自行选值】。"
        "断言逐字保留且仍在运行;strict=True 使 OQ-17 落锁后本门会因"
        "【意外通过】而变红,迫使这个标记被摘掉。见 T23 的 needs-human 上报。"
    ),
)
@pytest.mark.parametrize("idx", range(3))
def test_t9b_cash_alongside_shares_is_not_reported_as_a_huge_sharpe(idx):
    """门 9b ⭐:`shares=999` + `cash=50.05` + 恒定比率价格。

    这是 ADR-057 修正后 `weight=1.0` 成交的**常态**,而 T7/T8 都从
    `shares = 0` 起算故**均不覆盖**它。`cash` 是固定**加项**,使
    `r_k = g·shares·c_k / (cash + shares·c_k)` **单调漂移**而非恒定,于是
    σ 是一个**真实的**(微观)现金拖累信号 —— 落在 1e-10 ~ 1e-6,
    **高出 `1e-12` 守卫三个数量级故不被抑制**,裸算输出 1e5 ~ 1e7。

    **本门目前以 `xfail(strict=True)` 形式存在,因为它的期望值取决于
    OQ-17,而 OQ-17 在设计期【未决】且构建期【不得自行选值】。**
    这不是为了让门变绿而弱化断言:断言本身(「拒绝任何 > 1e4 的裸值」)
    **逐字保留**且仍在运行 —— `strict=True` 意味着一旦 OQ-17 落锁、
    `SHARPE_REPORTABLE_CEILING` 实现到位,本门会因**意外通过**而变红,
    迫使这个 xfail 标记被摘掉。详见本任务上报的 needs-human 结论。
    """
    g = FX["t9b_growth_rates"][idx]
    n = FX["t9b_bars"][idx]
    cash = FX["t9b_cash"]
    shares = FX["t9b_shares"]

    closes = [100.0 * (1.0 + g) ** i for i in range(n)]
    equity = [cash + shares * c for c in closes]

    got = compute_sharpe(equity)

    # 先把「裸算会给什么」钉死 —— 证明本门确实到达了那条危险路径,
    # 而不是因为别的原因恰好通过。
    r = [equity[k + 1] / equity[k] - 1.0 for k in range(len(equity) - 1)]
    mean = sum(r) / len(r)
    sigma = math.sqrt(sum((x - mean) ** 2 for x in r) / (len(r) - 1))
    assert sigma > SHARPE_DEGENERATE_EPS * max(1.0, abs(mean)), (
        "1e-12 守卫抓不到这个 sigma —— 这正是 ADR-058 实测反例的前提"
    )
    naive = mean / sigma * math.sqrt(TRADING_DAYS_PER_YEAR)
    assert naive == pytest.approx(FX["t9b_naive_sharpe"][idx], rel=0.02)

    # ── ADR-058「处置」第 1 条:拒绝任何 > 1e4 的裸值 ──
    assert got is None or abs(got) <= FX["t9b_reject_above"], (
        f"夏普 {got!r} 是一个退化/合成数据的产物,不可上报。"
        "需要 OQ-17 的绝对上限兜底(SHARPE_REPORTABLE_CEILING)"
    )


# ═══════════════════ 门 10 · T9 round-trip ═══════════════════


def test_t9_round_trip_gives_a_real_number_not_none_not_zero():
    """门 10 · T9:交易但回到起点 → **真实数字**,非 `None` 非 `0.0`。

    总收益为 0 而夏普为正,是**算术平均的波动拖累效应** —— 数字按定义正确
    但反直觉,故 ADR-059 把它写进声明。
    """
    eq = FX["t9_equity"]
    got = _sharpe(eq)
    assert got == FX["t9_sharpe"]
    assert got is not None and got != 0.0

    pct, peak, trough = _mdd(eq)
    assert pct == FX["t9_max_drawdown_pct"]
    assert peak == FX["t9_peak_bar"]
    assert trough == FX["t9_trough_bar"]


# ═══════════════════ 门 11 · T10 单行 CSV ═══════════════════


def test_t10_single_row_csv_keeps_gaps_null_too(tmp_path):
    """门 11 · T10:单行 CSV → 夏普 `None`、回撤 `0.0`、日期 `None`;

    **并共同断言三个跳空字段仍 `None`** —— 本任务是加性的,不得碰 ADR-021。
    """
    csv = tmp_path / "one.csv"
    csv.write_text(
        "Date,Open,High,Low,Close,Volume\n2020-01-02,100,101,99,100,1000\n",
        encoding="utf-8",
    )
    strat = tmp_path / "bah.py"
    strat.write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "class S(Strategy):\n"
        "    def next(self):\n        self.target(weight=1.0)\n",
        encoding="utf-8",
    )
    s = run([strat], load_csv(csv), cash=100000.0)[0].summary

    assert s.sharpe is None
    assert s.max_drawdown_pct == 0.0
    assert s.max_drawdown_peak_date is None
    assert s.max_drawdown_trough_date is None
    # ADR-021 的三个字段不受本任务影响
    assert s.max_gap_pct is None
    assert s.max_gap_date is None
    assert s.mean_abs_gap_pct is None


# ═══════════════════ 门 12 / 13 · T11 与 T12 ═══════════════════


def test_t11_running_max_is_not_reset_and_deepest_wins():
    """门 12 · T11:两段回撤、后者更深 → 取**最深**而非最早那段。

    并门住「running max 被重置」的错误实现:峰值须是 bar0(100),
    不是第二段的局部起点。
    """
    eq = FX["t11_equity"]
    pct, peak, trough = _mdd(eq)
    assert pct == FX["t11_max_drawdown_pct"]
    assert peak == FX["t11_peak_bar"]
    assert trough == FX["t11_trough_bar"]


def test_t12_depth_tie_takes_the_earlier_trough():
    """门 13 · T12:深度平手 → 谷底取**较早**那个 ⇒ 门住严格 `<`。

    若实现写成 `dd <= worst`,谷底会变成 bar3,本门变红。
    """
    eq = FX["t12_equity"]
    pct, peak, trough = _mdd(eq)
    assert pct == FX["t12_max_drawdown_pct"]
    assert peak == FX["t12_peak_bar"]
    assert trough == FX["t12_trough_bar"]

    # 反向:`<=` 的错误实现会给**较晚**的那个谷底
    worst, late_trough = 0.0, None
    run_max = eq[0]
    for i, p in enumerate(eq):
        run_max = max(run_max, p)
        dd = p / run_max - 1.0
        if dd <= worst:
            worst, late_trough = dd, i
    assert late_trough != FX["t12_trough_bar"], "门必须能分辨 `<` 与 `<=`"


# ═══════════════════ 门 15 · 通用不变式(⭐ 符号位)═══════════════════


_ALL_EQUITY_KEYS = [
    "t1_equity", "t2_equity", "t4_equity", "t5_equity",
    "t6_equity", "t7_equity", "t8_equity", "t9_equity",
    "t11_equity", "t12_equity",
]


@pytest.mark.parametrize("key", _ALL_EQUITY_KEYS)
def test_max_drawdown_is_never_negative_zero(key):
    """门 15 ⭐:回撤恒 `>= 0.0` **且符号位为正**。

    **只断言 `>= 0.0` 抓不到 `-0.0`**:它在 Python 里 `== 0.0` 且 `>= 0.0`
    均为真。但 `json.dumps(-0.0)` 输出 `'-0.0'`、按 `percent_unsigned`
    格式化输出 `'-0.00%'` —— 屏幕上出现双重负号的显示 bug。

    这是 `abs(worst)` 与 `-worst` 的分界:`worst` 初值 `0.0`,单调上涨/恒定
    净值时它从未被重新赋值,`-worst` 即得 `-0.0`。14 条 fixture 里有 4 条
    命中,而没有一条只断言 `>= 0.0` 的门会红。
    """
    eq = FX[key]
    pct, _, _ = compute_max_drawdown(eq, _dates(len(eq)))

    assert pct >= 0.0
    assert math.copysign(1.0, pct) > 0.0, f"{key}: 回撤是 -0.0 —— 必须用 abs()"
    assert repr(pct) != FX["reject_negative_zero_repr"]
    # 经过取整与 JSON 之后符号位仍须为正(round 保留符号位)
    assert repr(round(pct, ROUNDING_DECIMALS)) != FX["reject_negative_zero_repr"]
    assert "-0.0" not in json.dumps(round(pct, ROUNDING_DECIMALS))


@pytest.mark.parametrize("key", _ALL_EQUITY_KEYS)
def test_peak_date_never_after_trough_date(key):
    """门 15:两日期非 `None` 时 `peak_date <= trough_date`。"""
    eq = FX[key]
    _, peak, trough = compute_max_drawdown(eq, _dates(len(eq)))
    if peak is not None and trough is not None:
        assert peak <= trough
    else:
        # 二者**同生同死** —— 不得出现一个有值一个为 None
        assert peak is None and trough is None


@pytest.mark.parametrize("key", _ALL_EQUITY_KEYS)
def test_sharpe_is_never_nan_or_inf(key):
    """门 14 的一半:夏普要么是 `None`,要么是**有限**浮点。

    `NaN` / `Infinity` 都**不是合法 JSON**,会打断前端。
    """
    got = compute_sharpe(FX[key])
    assert got is None or math.isfinite(got)


# ═══════════════════ 门 14 · 契约 / JSON ═══════════════════


def test_t13_four_keys_are_bare_scalars_in_summary_json(tmp_path):
    """门 14 · T13:四键均在 `summary.json` 且为**裸标量**。"""
    from nullhypothesis import report as report_mod

    csv = tmp_path / "d.csv"
    rows = ["Date,Open,High,Low,Close,Volume"]
    for d, c in zip(_dates(5), [100.0, 120.0, 90.0, 110.0, 150.0]):
        rows.append(f"{d},{c},{c + 1},{c - 1},{c},1000")
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
    strat = tmp_path / "bah.py"
    strat.write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "class S(Strategy):\n"
        "    def next(self):\n        self.target(weight=1.0)\n",
        encoding="utf-8",
    )
    result = run([strat], load_csv(csv), cash=100000.0)[0]
    payload = report_mod._summary_json(result.summary)

    for k in ("sharpe", "max_drawdown_pct",
              "max_drawdown_peak_date", "max_drawdown_trough_date"):
        assert k in payload, f"{k} 没进 summary.json —— 新字段止步于后端"
        assert not isinstance(payload[k], str) or k.endswith("_date"), (
            f"{k} 必须是裸标量,不是格式化字符串"
        )

    assert isinstance(payload["max_drawdown_pct"], float)
    # 百分比已乘 100 且为正数幅度 —— 不含 '%'
    text = json.dumps(payload, ensure_ascii=False)
    for token in FX["forbidden_json_tokens"]:
        assert token not in text, f"{token} 不是合法 JSON"
    assert "%" not in text, "JSON 里不得含格式化后的百分号"


def test_null_sharpe_serializes_to_json_null(tmp_path):
    """门 14:`None` → `null`(**不是** `NaN`)→ 屏幕按 `null_display` 显示。"""
    from nullhypothesis import report as report_mod
    from nullhypothesis.run import Summary

    s = Summary(
        start="2020-01-01", end="2020-01-02", bars=2,
        initial_cash=100.0, final_equity=100.0, total_return_pct=0.0,
        trade_count=0, max_gap_pct=None, max_gap_date=None,
        mean_abs_gap_pct=None,
        sharpe=None, max_drawdown_pct=0.0,
        max_drawdown_peak_date=None, max_drawdown_trough_date=None,
    )
    text = json.dumps(report_mod._summary_json(s))
    assert '"sharpe": null' in text
    for token in FX["forbidden_json_tokens"]:
        assert token not in text

    # 屏幕投影
    assert report_mod.format_field("sharpe", None) == FORMATS["null_display"]
    assert (
        report_mod.format_field("max_drawdown_peak_date", None)
        == FORMATS["null_display"]
    )


def test_sharpe_renders_without_a_percent_sign():
    """`sharpe` 用 `formats.ratio` —— **裸比率**,屏幕上不得带 `%`。

    复用 `percent_signed` 是最易犯的错(它会附加 `%` 并强加 `+` 号)。
    """
    from nullhypothesis import report as report_mod

    out = report_mod.format_field("sharpe", 1.2345)
    assert "%" not in out
    assert out == FORMATS[FIELD_FORMATS["sharpe"]].format(1.2345)


def test_max_drawdown_renders_unsigned():
    """`max_drawdown_pct` 用 `percent_unsigned` —— 正数幅度,不带 `+`。"""
    from nullhypothesis import report as report_mod

    out = report_mod.format_field("max_drawdown_pct", 23.5)
    assert not out.startswith("+") and not out.startswith("-")
    assert out.endswith("%")


# ═══════════════════ 门 16 / 17 / 18 · 结构性条款 ═══════════════════


def test_gate16_eps_is_a_named_constant():
    """门 16:`SHARPE_DEGENERATE_EPS` 是**命名常量**,不是魔法数字。"""
    assert SHARPE_DEGENERATE_EPS == FX["degenerate_eps"]
    src = (ROOT / "nullhypothesis" / "run.py").read_text(encoding="utf-8")
    assert "SHARPE_DEGENERATE_EPS = " in src
    # 判定式必须**引用常量**而不是内联字面量
    assert "SHARPE_DEGENERATE_EPS * max(" in src


def test_gate17_ddof_one_is_written_literally():
    """门 17:`ddof=1` 在代码里**字面写出**,不依赖库默认值。

    `np.std` 默认 `ddof=0`、`pd.Series.std` 默认 `ddof=1` —— 写「标准差」
    是歧义的。本门断言源码里确有那个字面量。
    """
    src = (ROOT / "nullhypothesis" / "run.py").read_text(encoding="utf-8")
    assert "ddof=1" in src, "ddof=1 必须字面写出(ADR-058)"


def test_gate17_annualization_and_rf_are_named_constants():
    """`N = 252` 与 `rf = 0` 也都是命名常量(ADR-058 硬编码且绑死日线)。"""
    assert TRADING_DAYS_PER_YEAR == FX["annualization_factor"]
    from nullhypothesis.run import RISK_FREE_RATE

    assert RISK_FREE_RATE == FX["risk_free_rate"]


def test_gate18_report_py_has_zero_arithmetic_on_risk_metrics():
    """门 18:计算在 `run.py`,`report.py` **零算术**(ADR-044)。

    `report.py` 只渲染不计算 —— 它对这四个字段只做**转录**。本门断言它
    不含任何会重算这两个指标的符号。
    """
    src = (ROOT / "nullhypothesis" / "report.py").read_text(encoding="utf-8")
    for forbidden in ("sqrt", "ddof", "running_max", "run_max", "252"):
        assert forbidden not in src, (
            f"report.py 含 {forbidden!r} —— ADR-044 规定它只渲染不计算"
        )


def test_sharpe_is_computed_from_equity_never_from_close(tmp_path):
    """ADR-058 的核心反例:**从 `close` 算的话同一 CSV 上所有策略夏普相同**。

    同一份价格序列 + 两条**行为不同**的策略 ⇒ 两个夏普必须**不同**。
    """
    csv = tmp_path / "d.csv"
    rows = ["Date,Open,High,Low,Close,Volume"]
    for d, c in zip(_dates(6), [100.0, 110.0, 95.0, 130.0, 120.0, 140.0]):
        rows.append(f"{d},{c},{c + 1},{c - 1},{c},1000")
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")

    bah = tmp_path / "bah.py"
    bah.write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "class S(Strategy):\n"
        "    def next(self):\n        self.target(weight=1.0)\n",
        encoding="utf-8",
    )
    half = tmp_path / "half.py"
    half.write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "class S(Strategy):\n"
        "    def next(self):\n"
        "        self.target(weight=0.5 if len(self.data) % 2 == 0 else 0.0)\n",
        encoding="utf-8",
    )

    a, b = run([bah, half], load_csv(csv), cash=100000.0)
    assert a.summary.sharpe != b.summary.sharpe, (
        "两条策略夏普相同 —— 实现很可能从 close 而非 equity 算"
    )


# ═══════════════════ 门 19 · 与 T22 的共享文件 ═══════════════════


def test_gate19_summary_keeps_all_prior_fields_and_adds_four():
    """门 19:`Summary` 同时含既有十字段与四个新字段(加性,顺序不变)。

    新字段**追加在末尾** —— ADR-037 钉死顺序,既有门大量使用**位置构造**,
    插在中间会让位置参数静默错位。
    """
    import dataclasses

    from nullhypothesis.run import Summary

    names = [f.name for f in dataclasses.fields(Summary)]
    prior = [
        "start", "end", "bars", "initial_cash", "final_equity",
        "total_return_pct", "trade_count",
        "max_gap_pct", "max_gap_date", "mean_abs_gap_pct",
    ]
    assert names[: len(prior)] == prior, "既有字段的名称与顺序必须逐字不变"
    assert names[len(prior):] == [
        "sharpe", "max_drawdown_pct",
        "max_drawdown_peak_date", "max_drawdown_trough_date",
    ]


def test_gate19_positional_construction_of_prior_fields_still_works():
    """既有门的**位置构造**不得被破坏 —— 这是「追加而非插入」的真正理由。"""
    from nullhypothesis.run import Summary

    s = Summary(
        "2020-01-01", "2020-01-02", 2, 100.0, 110.0, 10.0, 1,
        None, None, None,
    )
    assert s.trade_count == 1
    # 四个新字段有默认值 ⇒ 加性、向后兼容
    assert s.sharpe is None and s.max_drawdown_pct is None
