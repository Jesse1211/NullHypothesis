"""T6 · 汇总渲染 + summary.txt + summary.json。

**只渲染,不计算。** Summary 的值已在 `RunReport` 里算好(T5),本模块不得
重算、不得遍历 `Bar` 列表或 `Account`(ADR-044)。故这里只 import 值对象,
不 import `engine.Backtest` 或 `account` 的任何符号。

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
    if name in ("start", "end", "max_gap_date"):
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
        if i < len(report.results) - 1:
            out.append("")

    out.append(_THIN)
    # 跳空统计按【运行】只打一次 —— 它是数据的性质,不是策略的性质。
    out.extend(_gap_block(report.results[0].summary))

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
