"""T6/T7 的验收门 · 渲染与出图。

两者都只消费 `RunReport`(ADR-044)。门里用 **纯 `RunReport`** —— 与任何
`Backtest` 实例无关的手工构造物。伸手进聚合的实现结构上过不了这条。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

from nullhypothesis import plot, report as report_mod
from nullhypothesis.engine import Fill
from nullhypothesis.run import (
    EquityPoint,
    RunReport,
    RunRequest,
    RunResult,
    Summary,
)

ROOT = Path(__file__).resolve().parent.parent
C = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))


# ───────────────────── 纯 RunReport(不经 Backtest)─────────────────────


def _summary(**over) -> Summary:
    base = dict(
        start="2015-01-02", end="2024-12-31", bars=2516,
        initial_cash=100000.0, final_equity=916062.0,
        total_return_pct=816.06, trade_count=1,
        max_gap_pct=-8.42, max_gap_date="2020-03-16", mean_abs_gap_pct=0.31,
    )
    base.update(over)
    return Summary(**base)  # type: ignore[arg-type]


def _result(name="buy_and_hold", *, trades=None, ledger=None, **over) -> RunResult:
    trades = trades if trades is not None else [
        Fill(date="2015-01-05", side="BUY", shares=3694, price=27.07, fee=0.0)
    ]
    ledger = ledger if ledger is not None else [(3.42, 3694)]
    return RunResult(
        strategy=name,
        equity=[
            EquityPoint(date="2015-01-02", cash=100000.0, shares=0, equity=100000.0),
            EquityPoint(date="2015-01-05", cash=3.42, shares=3694, equity=98106.0),
        ],
        trades=trades,
        trades_ledger=ledger,
        summary=_summary(**over),
    )


def _report(results=None) -> RunReport:
    return RunReport(
        run_id="20261003-172400",
        request=RunRequest(strategies=["buy_and_hold.py"], data_file="aapl.csv",
                           cash=100000.0, fee=0.0),
        results=results or [_result()],
        assumptions=[C["frozen_text"]["assumption_adjusted"],
                     C["frozen_text"]["assumption_market_order"]],
    )


# ═══════════════════════════ T6 · 渲染 ═══════════════════════════


def test_render_only_accepts_a_pure_runreport(tmp_path):
    """ADR-044:传入与任何 Backtest 无关的纯 RunReport,输出必须完全正确。"""
    text = report_mod.render(_report(), tmp_path)
    assert "回 测 结 果" in text
    with pytest.raises(TypeError):
        report_mod.render({"not": "a report"}, tmp_path)  # type: ignore[arg-type]


def _imported_names(module_path: Path) -> set[str]:
    """只看真正的 import 语句 —— 不是文本里出现过这个词。

    早先这条门直接 grep "Backtest",结果被**说明自己不 import 它**的那句
    docstring 误报了。断言「断言的对象」要是 AST,不是子串。
    """
    import ast

    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def test_report_module_does_not_reach_into_the_aggregate():
    names = _imported_names(ROOT / "nullhypothesis" / "report.py")
    assert not {n for n in names if "account" in n}, "T6 不得 import account(ADR-044)"
    assert "Backtest" not in names, "T6 不得 import 聚合类(ADR-044)"
    assert ".run" in names or "run" in {n.split(".")[-1] for n in names}, \
        "T6 只消费值对象"


def test_screen_text_contains_every_required_item(tmp_path):
    text = report_mod.render(_report(), tmp_path)
    for token in ("区间", "交易日数", "初始资金", "期末资产", "总收益", "交易次数"):
        assert token in text
    assert C["frozen_text"]["reconcile_line"] in text


def test_screen_is_a_formatted_projection_of_bare_json_scalars(tmp_path):
    """屏幕 `+816.06%`,JSON 裸标量 `816.06` —— JSON 不含格式化字符串。

    JSON 里存格式化串会让 T10 的逐字段对账与 T11 的递归相等永不可能通过。
    """
    rep = _report()
    text = report_mod.render(rep, tmp_path)
    payload = json.loads((tmp_path / C["filenames"]["summary_json"]).read_text())
    s = payload["results"][0]["summary"]

    assert "+816.06%" in text and "916,062.00" in text and "2,516" in text
    assert s["total_return_pct"] == 816.06 and isinstance(s["total_return_pct"], float)
    assert s["final_equity"] == 916062.0 and isinstance(s["final_equity"], float)
    assert s["bars"] == 2516 and isinstance(s["bars"], int)
    assert s["trade_count"] == 1 and isinstance(s["trade_count"], int)
    for v in s.values():
        assert not isinstance(v, str) or "%" not in v, "JSON 不得含格式化百分比"


def test_mean_abs_gap_is_rendered_unsigned(tmp_path):
    """绝对值均值恒 >= 0,加 `+` 号零信息 —— ADR-021 曾在此自相矛盾。"""
    text = report_mod.render(_report(), tmp_path)
    assert "0.31%" in text and "+0.31%" not in text
    assert "-8.42%" in text          # 有方向的量带符号
    assert "2020-03-16" in text


def test_null_gaps_render_as_na(tmp_path):
    rep = _report([_result(max_gap_pct=None, max_gap_date=None,
                           mean_abs_gap_pct=None)])
    text = report_mod.render(rep, tmp_path)
    assert C["formats"]["null_display"] in text
    assert "None" not in text and "nan" not in text.lower()


def test_assumptions_appear_on_screen_and_in_json(tmp_path):
    """ADR-001 那条不可验证前提的唯一守卫,不得在任何一侧静默丢失。"""
    rep = _report()
    text = report_mod.render(rep, tmp_path)
    payload = json.loads((tmp_path / C["filenames"]["summary_json"]).read_text())
    for a in (C["frozen_text"]["assumption_adjusted"],
              C["frozen_text"]["assumption_market_order"]):
        assert a in text
        assert a in payload["assumptions"]


def test_summary_txt_is_a_verbatim_copy_of_the_screen(tmp_path):
    text = report_mod.render(_report(), tmp_path)
    on_disk = (tmp_path / C["filenames"]["summary_txt"]).read_text(encoding="utf-8")
    assert on_disk.rstrip("\n") == text


def test_summary_json_has_exactly_three_top_level_keys(tmp_path):
    report_mod.render(_report(), tmp_path)
    payload = json.loads((tmp_path / C["filenames"]["summary_json"]).read_text())
    assert set(payload) == {"run_id", "results", "assumptions"}


def test_summary_json_results_count_matches_strategies(tmp_path):
    rep = _report([_result("buy_and_hold"), _result("ma_cross", trade_count=47)])
    report_mod.render(rep, tmp_path)
    payload = json.loads((tmp_path / C["filenames"]["summary_json"]).read_text())
    assert len(payload["results"]) == 2


def test_comparison_table_appears_only_for_multiple_strategies(tmp_path):
    """ADR-018:两行、各含策略名与数字;单策略时不渲染。"""
    two = _report([_result("buy_and_hold"),
                   _result("ma_cross", final_equity=412330.0,
                           total_return_pct=312.33, trade_count=47)])
    text = report_mod.render(two, tmp_path)
    assert "并排对比" in text
    assert "buy_and_hold" in text and "ma_cross" in text
    assert "412,330.00" in text and "+312.33%" in text

    one = report_mod.render(_report(), tmp_path)
    assert "并排对比" not in one


def test_comparison_table_does_not_rank(tmp_path):
    """只陈述数字 —— 无排名、无「最佳」标记(ADR-018)。"""
    text = report_mod.render(
        _report([_result("a"), _result("b", final_equity=1.0,
                                       total_return_pct=-99.0)]), tmp_path)
    for word in ("最佳", "best", "winner", "排名", "↑", "↓"):
        assert word not in text


# ═══════════════════════════ T7 · 出图 ═══════════════════════════


def test_agg_backend_is_selected_before_pyplot():
    """实测本机默认是 macosx —— ADR-013 此前无任何门。"""
    import matplotlib

    assert matplotlib.get_backend().lower() == "agg"
    src = (ROOT / "nullhypothesis" / "plot.py").read_text(encoding="utf-8")
    assert src.index('matplotlib.use("Agg")') < src.index("import matplotlib.pyplot")


def test_png_is_really_rendered_and_readable(tmp_path):
    """不接受 mock 掉 savefig —— 真实文件、真实字节、能被 PIL 重读。"""
    from PIL import Image

    paths = plot.write(_report(), tmp_path)
    png = tmp_path / C["filenames"]["equity_png"].format(stem="buy_and_hold")
    assert png.exists() and png.stat().st_size > 0
    assert paths["buy_and_hold"] == str(png)
    with Image.open(png) as im:
        im.verify()


def test_single_point_curve_is_not_a_blank_image(tmp_path):
    """ADR-023 的单行 CSV:默认线型画单点会渲染出空白绘图区 ——
    那是一张字面意义的白图,而「字节数 > 0 且可重读」照样通过。"""
    from PIL import Image

    one = RunResult(
        strategy="flat",
        equity=[EquityPoint(date="2020-01-02", cash=1.0, shares=0, equity=1.0)],
        trades=[], trades_ledger=[],
        summary=_summary(bars=1, max_gap_pct=None, max_gap_date=None,
                         mean_abs_gap_pct=None),
    )
    plot.write(_report([one]), tmp_path)
    png = tmp_path / C["filenames"]["equity_png"].format(stem="flat")
    with Image.open(png) as im:
        colors = im.convert("RGB").getcolors(maxcolors=1 << 20) or []
    non_bg = sum(n for n, rgb in colors if rgb != (255, 255, 255))
    assert non_bg > 0, "整张图全白 —— 单点没有被画出来"


def test_trades_csv_header_is_verbatim(tmp_path):
    plot.write(_report(), tmp_path)
    p = tmp_path / C["filenames"]["trades_csv"].format(stem="buy_and_hold")
    rows = list(csv.reader(p.open(encoding="utf-8")))
    assert rows[0] == C["trades_csv_header"]


def test_trades_csv_rows_field_by_field(tmp_path):
    plot.write(_report(), tmp_path)
    p = tmp_path / C["filenames"]["trades_csv"].format(stem="buy_and_hold")
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert len(rows) == 1
    r = rows[0]
    assert r["date"] == "2015-01-05"
    assert r["side"] in C["trade_sides"]
    assert int(r["shares"]) == 3694 > 0, "shares 恒为正,方向在 side 里"
    assert float(r["price"]) == 27.07
    assert float(r["cash_after"]) == 3.42 and int(r["shares_after"]) == 3694


def test_cash_after_comes_from_trades_ledger(tmp_path):
    """T7 不得 import account,故这两列必须由聚合根提供(ADR-037/044)。"""
    res = _result(ledger=[(99.99, 1234)])
    plot.write(_report([res]), tmp_path)
    p = tmp_path / C["filenames"]["trades_csv"].format(stem="buy_and_hold")
    row = next(csv.DictReader(p.open(encoding="utf-8")))
    assert float(row["cash_after"]) == 99.99 and int(row["shares_after"]) == 1234
    assert len(res.trades_ledger) == len(res.trades)


def test_zero_trade_csv_is_header_only(tmp_path):
    plot.write(_report([_result(trades=[], ledger=[])]), tmp_path)
    p = tmp_path / C["filenames"]["trades_csv"].format(stem="buy_and_hold")
    rows = list(csv.reader(p.open(encoding="utf-8")))
    assert rows == [C["trades_csv_header"]], "零交易时仅表头,不报错不缺文件"


def test_plot_module_does_not_reach_into_the_aggregate():
    names = _imported_names(ROOT / "nullhypothesis" / "plot.py")
    assert not {n for n in names if "account" in n}, "T7 不得 import account(ADR-044)"
    assert "Backtest" not in names, "T7 不得 import 聚合类(ADR-044)"


def test_write_only_accepts_a_pure_runreport(tmp_path):
    with pytest.raises(TypeError):
        plot.write({"nope": True}, tmp_path)  # type: ignore[arg-type]


def test_write_does_not_create_the_directory(tmp_path):
    """归档目录由 CLI 层独占创建(ADR-024)—— plot 静默 mkdir 会绕过碰撞保护。"""
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        plot.write(_report(), missing)
    assert not missing.exists()


def test_comparison_png_only_for_multiple_strategies(tmp_path):
    single = plot.write(_report(), tmp_path)
    assert single["comparison"] is None
    assert not (tmp_path / C["filenames"]["comparison_png"]).exists()

    multi_dir = tmp_path / "multi"
    multi_dir.mkdir()
    paths = plot.write(_report([_result("a"), _result("b")]), multi_dir)
    comp = multi_dir / C["filenames"]["comparison_png"]
    assert comp.exists() and paths["comparison"] == str(comp)
