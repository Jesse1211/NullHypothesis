"""T6 · 汇总渲染 + summary.txt + summary.json。

**只渲染,不计算。** Summary 的值已在 `RunReport` 里算好(T5),本模块不得
重算、不得遍历 `Bar` 列表或 `Account`(ADR-044)。故这里只 import 值对象,
不 import `engine.Backtest` 或 `account` 的任何符号。

报告头含本次运行的**生效**交易成本参数(`fee` / `slippage`,ADR-056)——
它们取自 `RunReport.request`,是**请求**的性质而非策略的性质,故按【运行】
只渲染一次。

屏幕文本是 JSON 的**格式化投影**:同一个数,屏幕上是 `+25.00%`,
`summary.json` 里是裸标量 `25.0`。JSON 本身不含格式化字符串 —— 否则 T10 的
逐字段对账与 T11 的递归相等永不可能通过。

所有格式与文案引用 `contracts.yaml`,本模块不复述字面量。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .run import RunReport, RunResult, Summary

__all__ = ["render", "contracts", "format_field"]

_CONTRACTS_PATH = Path(__file__).resolve().parent.parent / "contracts.yaml"


@lru_cache(maxsize=1)
def contracts() -> dict[str, Any]:
    """`contracts.yaml` —— 每个钉死字面量的单一真相来源。"""
    import yaml

    return yaml.safe_load(_CONTRACTS_PATH.read_text(encoding="utf-8"))


def format_field(name: str, value: Any) -> str:
    """按 `contracts.yaml` 的 `field_formats` 格式化一个 summary 字段。

    `None` → `formats.null_display`(单行 CSV 时跳空三值为 None)。

    注意 `mean_abs_gap_pct` 用 **percent_unsigned**:绝对值均值恒 >= 0,
    加 `+` 号零信息。而 `total_return_pct` / `max_gap_pct` 有方向,用
    percent_signed。这一处曾在 ADR-021 内部自相矛盾过,现在只有一处定义。
    """
    c = contracts()
    if value is None:
        return c["formats"]["null_display"]
    spec = c["formats"][c["field_formats"][name]]
    if name in (
        "start", "end", "max_gap_date",
        # ADR-058 的两个回撤日期 —— 同样由后端给成 YYYY-MM-DD,不参与舍入
        "max_drawdown_peak_date", "max_drawdown_trough_date",
    ):
        return str(value)  # 后端已给 YYYY-MM-DD(formats.date)
    return spec.format(value)


# ──────────────────────────── 屏幕文本 ────────────────────────────

_RULE = "═" * 47
_THIN = "─" * 47


def _summary_block(s: Summary) -> list[str]:
    f = lambda n: format_field(n, getattr(s, n))  # noqa: E731
    return [
        f" 区间          {f('start')} ~ {f('end')}",
        f" 交易日数      {f('bars')}",
        f" 初始资金      {f('initial_cash')}",
        f" 期末资产      {f('final_equity')}",
        f" 总收益        {f('total_return_pct')}",
        f" 交易次数      {f('trade_count')}",
    ]


def _gap_block(s: Summary) -> list[str]:
    date = format_field("max_gap_date", s.max_gap_date)
    return [
        f" 最大跳空      {format_field('max_gap_pct', s.max_gap_pct)}  {date}",
        f" 平均绝对跳空  {format_field('mean_abs_gap_pct', s.mean_abs_gap_pct)}",
    ]


def _cost_header(report: RunReport) -> list[str]:
    """**报告头**:本次运行的**生效**交易成本参数(ADR-056 的第 2 条机制)。

    `slippage` 与 `fee` **并列**(ADR-056 / T24 门 7):每张屏幕、每份
    `summary.txt` 都自带「这条曲线假设了多少成本」。两者并列而非二选一 ——
    `fee = 0` 在今天的美股零售是**现实的**(2019 年起主要券商普遍免佣),
    `slippage = 0` **永远不现实**,故 ADR-056 的诚实性恰恰依赖这个值**被显示**
    (「诚实来自声明与可见性,不来自默认值的大小」)。

    **只渲染,不计算**(ADR-044):两个值直接取自 `report.request`,本模块
    不做任何算术。数值格式引用 `contracts.yaml` 的 `formats.rate`,
    本模块不复述格式字面量。

    `fee` / `slippage` 不走 `format_field`:它们是 `RunRequest` 的字段而非
    `Summary` 的字段,而 `field_formats` 的键集合被 `tests/test_contracts.py`
    钉死为 `Summary` 的字段集 —— 把它们塞进去会破坏那条门。

    两个**行标签**与 `_summary_block` 的「区间 / 交易日数 / 初始资金」同属
    一类(本模块的行内字面量),不进 `contracts.yaml` 的 `frozen_text` ——
    那里装的是**跨层复述**的文案(对账行、两条假设、单日提示),由
    `tests/test_design_binding.py` 强制「DESIGN.md 只引用不复述」。把一个在
    散文里不可避免会出现的词(「滑点率」)放进去会让那条门对**文档**误报。
    """
    c = contracts()
    fmt = c["formats"]["rate"]
    return [
        f" 手续费率      {fmt.format(report.request.fee)}",
        f" 滑点率        {fmt.format(report.request.slippage)}",
    ]


def _risk_block(s: Summary) -> list[str]:
    """ADR-058 的两个风险指标。**纯渲染** —— 四个值都已由 `run.py` 算好。

    `sharpe` 用 `formats.ratio`(`{:.2f}`),**不是** `percent_signed` ——
    它是裸比率,后者会给它附加一个 `%`。
    `max_drawdown_pct` 用 `percent_unsigned`:ADR-058 定它为**正数幅度**,
    带符号格式化器会引入 `+`/`-` 双重负号的显示 bug。

    夏普为 `null` 时按 `formats.null_display` 显示 —— 表示该值在数学上
    **未定义**(不交易、或收益无波动、或样本不足 2 个收益),**不表示 0**
    (ADR-059 的声明逐字覆盖这一点)。
    """
    peak = format_field("max_drawdown_peak_date", s.max_drawdown_peak_date)
    trough = format_field("max_drawdown_trough_date", s.max_drawdown_trough_date)
    return [
        f" 夏普比率      {format_field('sharpe', s.sharpe)}",
        f" 最大回撤      {format_field('max_drawdown_pct', s.max_drawdown_pct)}"
        f"  {peak} ~ {trough}",
    ]


def _comparison_table(results: list[RunResult]) -> list[str]:
    """ADR-018:**只陈述数字,不排名、不评判。**

    没有排序、没有「最佳」高亮 —— 它仅说明这段历史,不说明哪条策略更好。
    单策略时根本不渲染(由调用方判断)。
    """
    name_w = max(len(r.strategy) for r in results)
    lines = [
        " 并排对比",
        f"   {'策略'.ljust(name_w)}  {'期末资产':>14}  {'总收益':>11}  {'交易':>6}",
    ]
    for r in results:
        s = r.summary
        lines.append(
            f"   {r.strategy.ljust(name_w)}  "
            f"{format_field('final_equity', s.final_equity):>14}  "
            f"{format_field('total_return_pct', s.total_return_pct):>11}  "
            f"{format_field('trade_count', s.trade_count):>6}"
        )
    return lines


def _screen_text(report: RunReport) -> str:
    c = contracts()
    out: list[str] = [_RULE, " 回 测 结 果", _RULE]

    for i, r in enumerate(report.results):
        if len(report.results) > 1:
            out.append(f" ▸ {r.strategy}")
        out.extend(_summary_block(r.summary))
        # 风险指标按【策略】打印 —— 与跳空不同,夏普与回撤是**策略的**性质
        # (从 equity 算,不从 close 算),故每条策略各有一份。
        out.extend(_risk_block(r.summary))
        if i < len(report.results) - 1:
            out.append("")

    out.append(_THIN)
    # 跳空统计按【运行】只打一次 —— 它是数据的性质,不是策略的性质。
    out.extend(_gap_block(report.results[0].summary))

    # 交易成本参数同样按【运行】只打一次 —— 它是**请求**的性质(ADR-056)。
    out.extend(_cost_header(report))

    if len(report.results) > 1:
        out.append(_THIN)
        out.extend(_comparison_table(report.results))

    out.append(_THIN)
    out.append(f" {c['frozen_text']['reconcile_line']}")
    for a in report.assumptions:
        out.append(f" ⚠ {a}")
    out.append(_RULE)
    return "\n".join(out)


# ──────────────────────────── JSON ────────────────────────────


def _summary_json(s: Summary) -> dict[str, Any]:
    """**裸标量**,不含任何格式化字符串 —— 屏幕才是它的格式化投影。"""
    return {
        "start": s.start,
        "end": s.end,
        "bars": s.bars,
        "initial_cash": s.initial_cash,
        "final_equity": s.final_equity,
        "total_return_pct": s.total_return_pct,
        "trade_count": s.trade_count,
        "max_gap_pct": s.max_gap_pct,
        "max_gap_date": s.max_gap_date,
        "mean_abs_gap_pct": s.mean_abs_gap_pct,
        # ── ADR-058 的四个字段 ── **纯转录,零算术**
        #
        # ADR-044 规定本模块只渲染不计算:这四个值已由 `run.py` 的 `Summary`
        # 构建处算完并取整,这里**只搬运**。任何乘除加减出现在本文件里都是
        # 违章 —— 包括「顺手」把 `max_drawdown_pct` 取个绝对值。
        "sharpe": s.sharpe,
        "max_drawdown_pct": s.max_drawdown_pct,
        "max_drawdown_peak_date": s.max_drawdown_peak_date,
        "max_drawdown_trough_date": s.max_drawdown_trough_date,
    }


def summary_json_payload(report: RunReport) -> dict[str, Any]:
    """ADR-037:顶层**恰好三个键**。"""
    return {
        "run_id": report.run_id,
        "results": [
            {"strategy": r.strategy, "summary": _summary_json(r.summary)}
            for r in report.results
        ],
        "assumptions": list(report.assumptions),
    }


# ──────────────────────────── 入口 ────────────────────────────


def render(report: RunReport, out_dir: Path) -> str:
    """返回屏幕文本,同时把 `summary.txt` 与 `summary.json` 写入 `out_dir`。

    **必须有 `out_dir`** —— `RunReport` 只带 `run_id`,不带归档路径
    (ADR-035)。没有它,T6 写不了自己被要求写的两个文件。
    """
    if not isinstance(report, RunReport):
        raise TypeError(
            f"render() 只接受 RunReport(ADR-044 的发布契约),收到 {type(report).__name__}"
        )

    c = contracts()
    text = _screen_text(report)
    out = Path(out_dir)

    (out / c["filenames"]["summary_txt"]).write_text(text + "\n", encoding="utf-8")
    (out / c["filenames"]["summary_json"]).write_text(
        json.dumps(summary_json_payload(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return text
