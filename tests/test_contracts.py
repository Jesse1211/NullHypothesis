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


def test_every_summary_field_has_exactly_one_format(c):
    expected = {
        "start", "end", "bars", "initial_cash", "final_equity",
        "total_return_pct", "trade_count",
        "max_gap_pct", "max_gap_date", "mean_abs_gap_pct",
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
    """Fill 恰好五个字段;cash_after/shares_after 来自 trades_ledger,不是 Fill 的字段。"""
    header = c["trades_csv_header"]
    assert header[:5] == ["date", "side", "shares", "price", "fee"]
    assert header[5:] == ["cash_after", "shares_after"]
