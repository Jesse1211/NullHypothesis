"""T7 · 曲线与交易清单输出。

**`matplotlib.use("Agg")` 必须在 import pyplot 之前** —— 实测本机默认后端是
`macosx`。它在当前 GUI 会话里 savefig 恰好能成功,所以这个缺陷不会立刻暴露,
但无人值守构建会依赖一个未被断言的环境属性(ADR-013)。
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # 必须在下一行之前(ADR-013)
import matplotlib.font_manager as _fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from .run import RunReport, RunResult  # noqa: E402


def _pick_cjk_font() -> str | None:
    """找一个能渲染中文的字体,找不到就退回 ASCII 标题。

    matplotlib 的默认字体 DejaVu Sans **没有** CJK 字形:中文标题会渲染成
    一排豆腐块,而「字节数 > 0 且可被 PIL 重读」照样通过 —— 这正是
    §12 人工门存在的那类缺陷(自动化门看不见「图上写了什么」)。
    这里主动避开它,而不是依赖某台机器恰好装了某个字体。
    """
    installed = {f.name for f in _fm.fontManager.ttflist}
    for name in ("PingFang SC", "Hiragino Sans GB", "Heiti SC", "Songti SC",
                 "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei",
                 "SimHei", "Arial Unicode MS"):
        if name in installed:
            return name
    return None


_CJK_FONT = _pick_cjk_font()
if _CJK_FONT:
    plt.rcParams["font.sans-serif"] = [_CJK_FONT, *plt.rcParams["font.sans-serif"]]
plt.rcParams["axes.unicode_minus"] = False  # 否则负号渲染成方块

__all__ = ["write"]

_CONTRACTS_PATH = Path(__file__).resolve().parent.parent / "contracts.yaml"


@lru_cache(maxsize=1)
def _contracts() -> dict[str, Any]:
    import yaml

    return yaml.safe_load(_CONTRACTS_PATH.read_text(encoding="utf-8"))


def _write_trades_csv(result: RunResult, path: Path) -> None:
    """表头与列序由 `contracts.yaml` 的 `trades_csv_header` 钉死。

    `cash_after` / `shares_after` 取自 `RunResult.trades_ledger` 的并行序列
    —— 本模块**不得 import account**,没有账本可读(ADR-037/044)。
    """
    header = _contracts()["trades_csv_header"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for fill, (cash_after, shares_after) in zip(
            result.trades, result.trades_ledger
        ):
            w.writerow([
                fill.date,
                fill.side,
                fill.shares,          # 恒为正,方向由 side 承载(ADR-037)
                f"{fill.price:.2f}",
                f"{fill.fee:.2f}",
                f"{cash_after:.2f}",
                shares_after,
            ])


def _equity_png(result: RunResult, path: Path) -> None:
    dates = [p.date for p in result.equity]
    values = [p.equity for p in result.equity]
    fig, ax = plt.subplots(figsize=(10, 4.5))
    # marker:单点曲线(ADR-023 的单行 CSV)用默认线型会渲染出空白绘图区 ——
    # 一张字面意义的白图,而「字节数 > 0 且可重读」照样通过。
    ax.plot(dates, values, linewidth=1.4, marker="o" if len(values) == 1 else None)
    ax.set_title(f"{result.strategy} · " + ("账户净值" if _CJK_FONT else "equity curve"))
    ax.set_ylabel("equity")
    ax.grid(alpha=0.25, linewidth=0.6)
    _thin_xticks(ax, dates)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _comparison_png(results: list[RunResult], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for r in results:
        ax.plot([p.date for p in r.equity], [p.equity for p in r.equity],
                linewidth=1.4, label=r.strategy)
    ax.set_title("账户净值 · 并排对比" if _CJK_FONT else "equity curves · comparison")
    ax.set_ylabel("equity")
    ax.grid(alpha=0.25, linewidth=0.6)
    ax.legend(frameon=False)
    _thin_xticks(ax, [p.date for p in results[0].equity])
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _thin_xticks(ax, dates: list[str], max_ticks: int = 8) -> None:
    if len(dates) <= max_ticks:
        return
    step = max(1, len(dates) // max_ticks)
    idx = list(range(0, len(dates), step))
    ax.set_xticks([dates[i] for i in idx])
    ax.tick_params(axis="x", rotation=45, labelsize=8)


def write(report: RunReport, out_dir: Path) -> dict[str, str]:
    """写 PNG 与 trades.csv,返回各 png 的路径。

    **不创建目录** —— `out_dir` 由调用方(CLI 层的 `create_archive_dir`)
    创建,本模块只往里写。**不写 run.json** —— 那是按运行的元数据,归 T8。

    返回 `{stem: equity_png_path, ..., "comparison": path|None}`,
    供 CLI 层填进 `run.json` 的 `png_path` / `comparison_png_path`。
    """
    if not isinstance(report, RunReport):
        raise TypeError(
            f"write() 只接受 RunReport(ADR-044 的发布契约),收到 {type(report).__name__}"
        )
    out = Path(out_dir)
    if not out.is_dir():
        raise FileNotFoundError(
            f"out_dir 不存在:{out} —— plot.write 不创建目录,"
            f"归档目录由 CLI 层的 create_archive_dir 独占创建(ADR-024/035)"
        )

    fn = _contracts()["filenames"]
    paths: dict[str, str] = {}

    for r in report.results:
        png = out / fn["equity_png"].format(stem=r.strategy)
        _equity_png(r, png)
        paths[r.strategy] = str(png)
        _write_trades_csv(r, out / fn["trades_csv"].format(stem=r.strategy))

    # ADR-037:`comparison.png` **仅多策略时产生**。
    if len(report.results) > 1:
        comp = out / fn["comparison_png"]
        _comparison_png(report.results, comp)
        paths["comparison"] = str(comp)
    else:
        paths["comparison"] = None  # type: ignore[assignment]

    return paths
