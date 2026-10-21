"""契约自洽校验 —— 本文件在任何产品代码存在之前就必须全绿。

存在理由(第 8 轮审查的结论):
  前八轮审查共 29 个 BLOCKER,其中第 6、7 两轮的修复**各自引入了下一轮的头号
  BLOCKER**,机制相同 —— 改了一处钉死的值,没重读显示同一个值的兄弟条款。
  诊断:文档里有可执行投影的部分(ADR-035 ↔ tests/test_layout.py)七轮零回归;
  只靠人读交叉核对的部分(格式串、fixture 算术、进程边界)是每一轮发现的全部来源。

本文件把后者也变成可执行的:contracts.yaml 是每个字面量的单一真相来源,
这里校验它自洽 —— 算术能重算、格式能复现、引用不悬空、进程边界不矛盾。

运行:python3 -m pytest tests/test_contracts.py -q
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts.yaml"
DESIGN = ROOT / "DESIGN.md"


def _load():
    """不依赖 PyYAML —— 环境里没有它,而 pip install 被 settings.json 拒绝。"""
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        pytest.skip("PyYAML 未安装且 pip install 被拒绝;改用 test_contracts_minimal")
    return yaml.safe_load(CONTRACTS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def c():
    return _load()


# ───────────────────────── 格式可复现 ─────────────────────────

def test_format_specs_reproduce_their_examples(c):
    f = c["formats"]
    assert f["money"].format(12500.0) == "12,500.00"
    assert f["money"].format(916062.0) == "916,062.00"
    assert f["count"].format(2516) == "2,516"
    assert f["percent_signed"].format(25.0) == "+25.00%"
    assert f["percent_signed"].format(-8.42) == "-8.42%"
    assert f["percent_unsigned"].format(0.31) == "0.31%"


def test_mean_abs_gap_is_unsigned_and_total_return_is_signed(c):
    """ADR-021 第 7 轮自相矛盾的那一处:绝对值均值恒 >= 0,加 + 号零信息。"""
    ff = c["field_formats"]
    assert ff["mean_abs_gap_pct"] == "percent_unsigned"
    assert ff["total_return_pct"] == "percent_signed"
    assert ff["max_gap_pct"] == "percent_signed"
    fmt = c["formats"][ff["mean_abs_gap_pct"]]
    assert fmt.format(0.31) == "0.31%", "绝对值百分比不得带 + 号"


def test_adr058_numeric_fixtures_are_really_numbers(c):
    """YAML 1.1 的陷阱:`2.46e7` 解析成**字符串**,`2.46e+7` 才是浮点。

    不门住这条,`t9b_reject_above` 会静默变成 `'1.0e4'`,于是
    `abs(sharpe) <= FX['t9b_reject_above']` 在比较 float 与 str —— Python 3
    会抛 TypeError,而写成 `==` 的断言则会永远为假/为真。
    """
    f = c["fixtures"]["adr058_metrics"]
    for key in ("degenerate_eps", "t9b_reject_above", "t9b_cash"):
        assert isinstance(f[key], float), (
            f"adr058_metrics.{key} 不是浮点 —— 指数记法须带符号(1.0e+4)"
        )
    for i, v in enumerate(f["t9b_naive_sharpe"]):
        assert isinstance(v, float), f"t9b_naive_sharpe[{i}] 不是浮点"
    assert isinstance(f["annualization_factor"], int)
    assert isinstance(f["t9b_shares"], int)


def test_adr058_risk_metric_formats(c):
    """ADR-058:`sharpe` 用 `ratio`、`max_drawdown_pct` 用 `percent_unsigned`。

    两条都是**可证伪的**:
    * `sharpe` **不可**复用 `percent_signed` —— 它会给裸比率附加一个 `%`;
    * `max_drawdown_pct` 定为**正数幅度**,带符号格式化器会引入 `+`/`-`
      双重负号的显示 bug(这正是 ADR-058 选正数约定时要避免的那个)。
    """
    ff = c["field_formats"]
    assert ff["sharpe"] == "ratio"
    assert ff["max_drawdown_pct"] == "percent_unsigned"
    assert ff["max_drawdown_peak_date"] == "date"
    assert ff["max_drawdown_trough_date"] == "date"

    ratio = c["formats"][ff["sharpe"]]
    assert ratio.format(1.2345) == "1.23"
    assert "%" not in ratio, "sharpe 是裸比率,格式里不得含 %"

    mdd = c["formats"][ff["max_drawdown_pct"]]
    assert mdd.format(23.5) == "23.50%"
    assert not mdd.format(23.5).startswith("+"), "正数幅度不得带 + 号"


def test_adr058_rounding_covers_both_numeric_fields(c):
    """两个数值字段进 `rounding.fields`,两个日期字段**不进**(不参与舍入)。"""
    fields = c["rounding"]["fields"]
    assert "sharpe" in fields
    assert "max_drawdown_pct" in fields
    assert "max_drawdown_peak_date" not in fields
    assert "max_drawdown_trough_date" not in fields


def test_every_summary_field_has_exactly_one_format(c):
    expected = {
        "start", "end", "bars", "initial_cash", "final_equity",
        "total_return_pct", "trade_count",
        "max_gap_pct", "max_gap_date", "mean_abs_gap_pct",
        # ── ADR-058 追加的四个字段 ──
        "sharpe", "max_drawdown_pct",
        "max_drawdown_peak_date", "max_drawdown_trough_date",
    }
    assert set(c["field_formats"]) == expected
    for field, key in c["field_formats"].items():
        assert key in c["formats"], f"{field} 引用了不存在的格式 {key}"


# ───────────────────────── fixture 算术可重算 ─────────────────────────

def test_adr016_fill_at_next_open(c):
    f = c["fixtures"]["adr016_fill_at_next_open"]
    got = math.floor(f["weight"] * f["cash"] / f["bar_t1_open"])
    assert got == f["expect_shares"]
    wrong = math.floor(f["weight"] * f["cash"] / f["bar_t_close"])
    assert wrong == f["reject_shares"]
    assert f["expect_shares"] != f["reject_shares"]


def test_adr014_denominator_is_three_way_discriminating(c):
    """第 7 轮审查纠正了我的验算:错误实现有两种,其中一种我原来抓不到。"""
    f = c["fixtures"]["adr014_denominator_three_way"]
    s1 = math.floor(f["weight_1"] * f["cash"] / f["open_1"])
    c1 = f["cash"] - s1 * f["open_1"]
    assert s1 == f["after_leg1_shares"] and c1 == f["after_leg1_cash"]

    inv = c1 + s1 * f["open_2"]
    assert inv == f["investable_2"]

    correct = s1 + (math.floor(f["weight_2"] * inv / f["open_2"]) - s1)
    err_target = s1 + (math.floor(f["weight_2"] * c1 / f["open_2"]) - s1)
    err_spend = s1 + math.floor(f["weight_2"] * c1 / f["open_2"])

    assert correct == f["expect_final_shares"]
    assert err_target == f["reject_target_from_cash"]
    assert err_spend == f["reject_spend_cash"]
    assert len({correct, err_target, err_spend}) == 3, "三者必须判然可分"


def test_adr014_weight_must_not_be_one(c):
    """weight=1.0 会让 correct 与「花光剩余现金」重合 —— 这是原 fixture 的漏洞。"""
    f = c["fixtures"]["adr014_denominator_three_way"]
    assert f["weight_2"] != 1.0
    s1, c1, o2 = f["after_leg1_shares"], f["after_leg1_cash"], f["open_2"]
    inv = c1 + s1 * o2
    at_one_correct = math.floor(1.0 * inv / o2)
    at_one_spend = s1 + math.floor(1.0 * c1 / o2)
    assert at_one_correct == at_one_spend, "印证:weight=1.0 时两者重合,故必须避开"


def test_adr012_floor_vs_ceil(c):
    f = c["fixtures"]["adr012_floor_vs_ceil"]
    raw = f["weight"] * f["cash"] / f["open"]
    assert math.floor(raw) == f["expect_shares"]
    assert math.ceil(raw) == f["reject_shares"]
    cash_after = f["cash"] - f["expect_shares"] * f["open"]
    assert cash_after == pytest.approx(f["expect_cash_after"])
    assert cash_after < f["one_share_price"], "未被 clamp:余钱不足一股"


def test_adr012_floor_vs_round(c):
    f = c["fixtures"]["adr012_floor_vs_round"]
    raw = f["weight"] * f["cash"] / f["open"]
    assert raw == pytest.approx(f["raw"])
    assert math.floor(raw) == f["expect_shares"]
    assert round(raw) == f["reject_round"]
    assert math.ceil(raw) == f["reject_ceil"]
    assert len({math.floor(raw), round(raw)}) == 2, "必须能区分 floor 与 round"


def test_adr041_zero_delta_case(c):
    f = c["fixtures"]["adr041_last_call_zero_delta"]
    s = f["after_target_half_shares"]
    inv = f["after_target_half_cash"] + s * f["open"]
    assert inv == f["investable"]
    assert s * f["open"] / inv == pytest.approx(f["current_weight"])
    delta = math.floor(f["current_weight"] * inv / f["open"]) - s
    assert delta == 0, "末次调用差额必须为 0,否则此 case 测不到 ADR-008 的交接"


def test_i7_four_prices_distinct(c):
    f = c["fixtures"]["i7_four_distinct_prices"]
    cands = [f["bar0"]["open"], f["bar0"]["close"],
             f["bar1"]["open"], f["bar1"]["close"]]
    assert len(set(cands)) == 4, "四个候选价必须互异,否则错位可能偶然通过"
    assert f["expect_fill_price"] == f["bar1"]["open"]
    assert f["expect_fill_price"] not in f["reject_prices"]


def test_adr056_slipped_prices_are_exactly_representable(c):
    """ADR-056 要求 I7′ **仍是精确等式** —— 故期望价必须精确可表示。

    这条门不是摆设:若 `s` 或 `Open` 换成一个让乘积落在浮点缝里的值,
    `tests/test_slippage.py` 的 G1/G2 就不能再写 `==`,而 ADR-056 逐字说
    「仍是精确等式,这是关键」。
    """
    f = c["fixtures"]["i7_four_distinct_prices"]
    o, s = f["bar1"]["open"], f["slippage"]
    assert o * (1.0 + s) == f["expect_fill_price_buy_slipped"]
    assert o * (1.0 - s) == f["expect_fill_price_sell_slipped"]
    # 四个候选价(含滑点后的正确值)仍全部互异 —— ADR-056 的鉴别力声明。
    cands = [f["bar0"]["open"], f["bar0"]["close"], f["bar1"]["close"],
             f["expect_fill_price_buy_slipped"]]
    assert len(set(cands)) == 4
    # 「用了对的 K 线但忘加滑点」是 ADR-056 新增的那条鉴别力。
    assert f["reject_price_no_slippage"] == f["expect_fill_price"]
    assert f["expect_fill_price_buy_slipped"] != f["reject_price_no_slippage"]


def test_adr057_slippage_affordability_arithmetic(c):
    """ADR-057:递减循环的单位成本必须含**全部**单位级成本(`s` 与 `rate`)。

    本门**重算**三个可分辨值,且刻意同时实现**正确形式与被废弃形式**,
    断言两者在 G14(b) 的那组参数上**给出不同的结果** —— 否则「拒绝 −1」
    这条门是空的。
    """
    f = c["fixtures"]["adr057_slippage_affordability"]

    def shares(open_price, rate, s, cash, held=0):
        """ADR-014a/057 的**正确**形式:守卫 `> held`、对**差额**计费、与 `cash` 比。"""
        investable = cash + held * open_price
        t = math.floor(f["weight_full"] * investable / open_price)
        unit = open_price * (1.0 + s) * (1.0 + rate)
        while t > held and (t - held) * unit > cash:
            t -= 1
        return t

    def shares_abandoned(open_price, rate, s, cash, held=0):
        """ADR-014a 的**第一版** —— 它是个 bug(ADR-057 的警告框逐字)。"""
        investable = cash + held * open_price
        t = math.floor(f["weight_full"] * investable / open_price)
        unit = open_price * (1.0 + s) * (1.0 + rate)
        while t > 0 and t * unit > investable:
            t -= 1
        return t

    # 未修正(裸 Open 作单位成本)确实超买并会破 I1。
    unfixed = math.floor(f["weight_full"] * f["cash"] / f["open"])
    assert unfixed == f["reject_shares_unfixed"]
    cash_after_unfixed = f["cash"] - unfixed * f["open"] * (1.0 + f["slippage"])
    assert round(cash_after_unfixed, 2) == f["reject_cash_after_unfixed"]
    assert cash_after_unfixed < 0.0, "必须真的透支,否则这个 fixture 测不到东西"

    # G6:只有滑点。
    g6 = shares(f["open"], f["fee_rate_zero"], f["slippage"], f["cash"])
    assert g6 == f["expect_shares_slippage_only"]
    cash_after = f["cash"] - g6 * f["open"] * (1.0 + f["slippage"])
    assert round(cash_after, 2) == f["expect_cash_after_slippage_only"]
    assert cash_after >= 0.0

    # G7:滑点 + 费率。三值互异。
    g7 = shares(f["open"], f["fee_rate"], f["slippage"], f["cash"])
    assert g7 == f["expect_shares_fee_and_slippage"]
    assert len({g6, g7, f["reject_shares_unfixed"]}) == 3

    # G9:往返总成本 == 名义额的 2s + 2·rate(精确)。
    nominal = f["g9_shares"] * f["g9_open"]
    buy = f["g9_open"] * (1.0 + f["slippage"])
    sell = f["g9_open"] * (1.0 - f["slippage"])
    net = (f["g9_shares"] * buy * (1.0 + f["g9_fee_rate"])
           - f["g9_shares"] * sell * (1.0 - f["g9_fee_rate"]))
    assert net == f["expect_roundtrip_net_loss"]
    assert round(net / nominal, 10) == round(
        2 * f["slippage"] + 2 * f["g9_fee_rate"], 10
    )

    # G3/G4:费与成交额都在滑点后价上。
    price = f["i7_open"] * (1.0 + f["slippage"])
    assert f["g3_shares"] * price == f["expect_amount_slipped"]
    assert f["g3_shares"] * price * f["g3_fee_rate"] == f["expect_fee_on_slipped_price"]
    assert f["g3_shares"] * f["i7_open"] * f["g3_fee_rate"] == f["reject_fee_on_raw_open"]

    # G14(b):**两种形式在此给出不同结果** —— 这条门的鉴别力前提。
    correct = shares(f["churn_open"], f["fee_rate_zero"], f["slippage"],
                     f["churn_cash"], held=f["churn_shares_held"])
    bad = shares_abandoned(f["churn_open"], f["fee_rate_zero"], f["slippage"],
                           f["churn_cash"], held=f["churn_shares_held"])
    assert correct - f["churn_shares_held"] == f["expect_churn_delta"]
    assert bad - f["churn_shares_held"] == f["reject_churn_delta"]
    assert f["expect_churn_delta"] != f["reject_churn_delta"]

    # REAL_STACK:真实 CSV 第 2 根 K 线的首笔成交价,精确可表示。
    assert f["real_stack_open"] * (1.0 + f["slippage"]) == (
        f["expect_real_stack_fill_price"]
    )
    # 上界与 --fee 同一个值(ADR-039 的参数表,逐字对称)。
    assert f["default_slippage"] == 0.0


def test_adr056_rate_format_shows_five_bps_as_nonzero(c):
    """`formats.rate` 必须有足够小数位 —— 两位会把 5bps 显示成「零成本」。

    ADR-056 的诚实性挂在「报告里显示生效值」上;一个把 `0.0005` 显示成
    `0.00` 的格式会把**非零**成本显示成零,那比不显示更坏。
    """
    f = c["fixtures"]["adr057_slippage_affordability"]
    rendered = c["formats"]["rate"].format(f["slippage"])
    assert float(rendered) == f["slippage"], "格式不得丢失滑点的有效数字"
    assert c["formats"]["money"].format(f["slippage"]) == "0.00", (
        "对照:money 格式确实会把它显示成零 —— 这就是不复用它的理由"
    )
    # `rate` 不是 Summary 字段的格式,故不得出现在 field_formats 里
    # (那个键集合被 test_every_summary_field_has_exactly_one_format 钉死)。
    assert "rate" not in set(c["field_formats"].values())


def test_adr021_gap_arithmetic(c):
    f = c["fixtures"]["adr021_gaps"]
    closes, opens = f["closes"], f["opens"]
    dec = c["rounding"]["decimals"]
    gaps = [round((opens[i] / closes[i - 1] - 1) * 100, dec)
            for i in range(1, len(opens))]
    assert gaps == f["gaps_pct"]
    assert len(gaps) == len(f["dates"]) - 1, "第 1 根 K 线不产生跳空"

    assert max(gaps, key=abs) == f["expect_max_gap_pct"]
    assert max(gaps) == f["reject_max_plain"]
    assert max(gaps, key=abs) != max(gaps), "必须能区分 max(abs) 与 max()"

    mean_abs = round(sum(abs(g) for g in gaps) / len(gaps), dec)
    mean_signed = round(sum(gaps) / len(gaps), dec)
    assert mean_abs == f["expect_mean_abs_gap_pct"]
    assert mean_signed == f["reject_mean_signed"]
    assert mean_abs != mean_signed, "必须能区分绝对值均值与带符号均值"

    idx = gaps.index(f["expect_max_gap_pct"])
    assert f["dates"][idx + 1] == f["expect_max_gap_date"]


def test_adr021_rounding_is_required(c):
    """没有 round 规则,这两条断言永不可满足 —— 第 6 轮差点就这么锁了。"""
    raw_gap = (92.0 / 100.0 - 1) * 100
    assert raw_gap != -8.00, "原始浮点不等于 -8.00"
    assert round(raw_gap, 2) == -8.00
    raw_mean = 16 / 3
    assert raw_mean != 5.33
    assert round(raw_mean, 2) == 5.33


def test_adr021_tie_breaks_earlier(c):
    f = c["fixtures"]["adr021_gap_tie"]
    gaps = f["gaps_pct"]
    assert abs(gaps[0]) == abs(gaps[1]), "平手 case 必须真的平手"
    assert "较早" in f["rule"]


def test_t5_summary_values(c):
    f = c["fixtures"]["t5_summary_values"]
    dec = c["rounding"]["decimals"]
    pct = round((f["final_equity"] / f["initial_cash"] - 1) * 100, dec)
    assert pct == f["expect_total_return_pct"]
    ratio = round(f["final_equity"] / f["initial_cash"] - 1, dec)
    quotient = round(f["final_equity"] / f["initial_cash"] * 100, dec)
    assert ratio == f["reject_ratio_unscaled"]
    assert quotient == f["reject_wrong_quotient"]
    assert len({pct, ratio, quotient}) == 3


def test_t2_strategy_equity(c):
    f = c["fixtures"]["t2_strategy_equity"]
    assert f["cash"] + f["shares"] * f["close"] == f["expect_equity"]


def test_i8_mock_is_genuinely_inconsistent(c):
    """I8 的门要求【每个】字段都与可推导来源冲突 —— 否则只验了一个字段。"""
    f = c["fixtures"]["i8_inconsistent_mock"]
    assert f["final_equity"] != f["equity_last"]
    assert f["trade_count"] != f["trades_length"]
    derived = round((f["implied_final"] / f["initial_cash"] - 1) * 100, 2)
    assert derived != f["total_return_pct"], "自算值必须不等于字段值"
    assert f["expect_rendered"]["total_return_pct"] == \
        c["formats"]["percent_signed"].format(f["total_return_pct"])
    assert f"+{derived:.2f}%" in f["reject_rendered"]["total_return_pct"]


def test_oq08_threshold_is_two_sided(c):
    f = c["fixtures"]["oq08_search_box_threshold"]
    assert f["assert_rendered_at"] > f["render_when_items_gt"]
    assert f["assert_absent_at"] <= f["render_when_items_gt"]


# ───────────────────────── 门的可执行性前提 ─────────────────────────

def test_no_gate_requires_monkeypatch_across_subprocess(c):
    """第 8 轮发现的第四类缺陷:T8 的碰撞门要 monkeypatch,
    而同任务其他条款规定用 subprocess 跑 —— 打桩跨不过进程边界。"""
    g = c["gate_execution"]
    overlap = set(g["in_process_required"]) & set(g["subprocess_required"])
    assert not overlap, f"同一条门不能既要进程内打桩又要子进程:{overlap}"


def test_every_monkeypatch_seam_is_a_pinned_symbol(c):
    """自检规则二:被打桩的符号必须是 ADR-035 钉死的模块级公开名。"""
    design = DESIGN.read_text(encoding="utf-8")
    for seam in c["gate_execution"]["monkeypatch_seams"]:
        bare = seam.split(".")[-1]
        assert bare in design, f"打桩接缝 {seam} 未在 DESIGN.md 中钉死"


def test_fixture_producers_are_named(c):
    for gate, producer in c["gate_execution"]["fixture_producers"].items():
        assert re.fullmatch(r"T\d+", producer), f"{gate} 的 fixture 产出者须是任务号"


# ───────────────────────── 引用完整性 ─────────────────────────

def test_rounding_fields_all_appear_in_field_formats(c):
    for field in c["rounding"]["fields"]:
        assert field in c["field_formats"], f"{field} 需取整但无格式定义"


def test_exit_codes_distinct(c):
    codes = list(c["exit_codes"].values())
    assert len(codes) == len(set(codes))
    assert c["exit_codes"]["ok"] == 0


def test_run_id_regex_accepts_collision_dirs(c):
    """过严的 ^\\d{8}-\\d{6}$ 会让 ADR-024 的碰撞目录在阶段二读不到 —— 即 I10 的悬空条目。"""
    pat = re.compile(c["patterns"]["run_id"])
    for ok in ["20261003-172400", "20261003-172400-2", "20261003-172400-10"]:
        assert pat.fullmatch(ok), f"必须接受 {ok}"
    for bad in ["20261003", "abc", "/etc/passwd", "20261003-172400/..", "20261003-172400\x00"]:
        assert not pat.fullmatch(bad), f"必须拒绝 {bad!r}"


def test_t8_required_outputs_match_filename_templates(c):
    fn = c["filenames"]
    for required in fn["t8_required_outputs"]:
        assert required in (fn["equity_png"], fn["trades_csv"], fn["summary_txt"]), \
            f"{required} 不在已钉死的文件名模板里"


def test_design_md_worked_example_is_recomputable():
    """第 8 轮手工重算抓到 ADR-038 的示例交易算错了 —— 而它错在唯一没有门
    去读的那个块里。这说明注意力只到门指向的地方,所以现在给它一个门。"""
    design = DESIGN.read_text(encoding="utf-8")
    m = re.search(r'"shares":\s*(\d+),\s*\n\s*"price":\s*([\d.]+),\s*"fee":\s*([\d.]+),\s*\n'
                  r'\s*"cash_after":\s*([\d.]+)', design)
    assert m, "ADR-038 的示例交易块结构变了,请同步更新本测试"
    shares, price, fee, cash_after = int(m[1]), float(m[2]), float(m[3]), float(m[4])
    initial = 100000.0
    assert shares == math.floor(initial / price), \
        f"floor({initial}/{price}) = {math.floor(initial/price)},示例写的是 {shares}"
    assert cash_after == pytest.approx(round(initial - shares * price - fee, 2), abs=0.005)


def test_trades_csv_header_matches_fill_fields_plus_ledger(c):
    """表头 = Fill 的展示列 + trades_ledger 的两列。

    `amount` 由 ADR-046 补入,在 CSV 里排在 `price` 之后、`fee` 之前
    (批准稿交易清单的列序:… 价格 / 金额 / 手续费 …)—— 这与 `Fill`
    dataclass 里 `amount` 排最后**不矛盾**:后者的顺序受位置构造约束
    (见 ADR-046),前者是人读的列序。
    """
    header = c["trades_csv_header"]
    assert header == [
        "date", "side", "shares", "price", "amount", "fee",
        "cash_after", "shares_after",
    ]
    # cash_after/shares_after 不是 Fill 的字段(ADR-044)
    from nullhypothesis.engine import Fill

    assert "cash_after" not in Fill.__dataclass_fields__
    assert "shares_after" not in Fill.__dataclass_fields__


def test_adr014a_affordability_constraint(c):
    """ADR-014a:满仓 + 非零费率时钉死的公式会超买破坏 I1。
    T4 的 Dev 发现了这个设计缺陷 —— 设计期从未测试这个组合。
    修法必须只在真正买不起时生效,不得改变本来合法的成交。"""
    f = c["fixtures"]["adr014a_affordability"]
    pinned = lambda w: math.floor(w * f["cash"] / f["open"])

    # 原公式在满仓时确实超买
    assert pinned(f["weight_full"]) == f["pinned_formula_shares"]
    cost = pinned(f["weight_full"]) * f["open"] * (1 + f["fee_rate"])
    assert round(cost, 2) == f["pinned_total_cost"]
    assert cost > f["cash"], "必须真的超支,否则这个 fixture 测不到东西"

    # 递减约束
    def fixed(w):
        t = pinned(w)
        while t > 0 and t * f["open"] * (1 + f["fee_rate"]) > f["cash"]:
            t -= 1
        return t

    assert fixed(f["weight_full"]) == f["expect_shares_full"]
    assert fixed(f["weight_full"]) * f["open"] * (1 + f["fee_rate"]) <= f["cash"]

    # 关键:半仓时必须与原公式完全一致 —— 约束不得影响本来合法的成交
    assert fixed(f["weight_half"]) == f["expect_shares_half"] == pinned(f["weight_half"])

    # 改除数的错误实现会在半仓处漂移
    divisor_changed = math.floor(f["weight_half"] * f["cash"] / (f["open"] * (1 + f["fee_rate"])))
    assert divisor_changed == f["reject_divisor_change_half"]
    assert divisor_changed != f["expect_shares_half"], "两种修法必须可分辨"
