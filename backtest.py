"""CLI 入口(ADR-017/039)。归档目录的独占创建也归这里(ADR-024/044)。

为什么 `RunReport` 在这一层组装而不在聚合根里:`run_id` 只有在归档目录
独占 `mkdir` 成功后才确定(可能带 `-2`/`-3` 后缀),而聚合根不拥有输出目录。

用法:
    python3 backtest.py --data data/aapl.csv --strategy strategies/buy_and_hold.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from nullhypothesis.errors import DataValidationError, StrategyError

__all__ = [
    "main",
    "_timestamp",
    "create_archive_dir",
    "build_report",
    "write_run_json",
]

_CONTRACTS_PATH = Path(__file__).resolve().parent / "contracts.yaml"


@lru_cache(maxsize=1)
def contracts() -> dict[str, Any]:
    import yaml

    return yaml.safe_load(_CONTRACTS_PATH.read_text(encoding="utf-8"))


def _exit_code(name: str) -> int:
    return int(contracts()["exit_codes"][name])


# ───────────────────────── 归档目录(ADR-024)─────────────────────────


def _timestamp() -> str:
    """`YYYYMMDD-HHMMSS`(本地时区)。

    **必须是模块级命名函数** —— 它是碰撞门的 monkeypatch 接缝。内联
    `datetime.now()` 会让「同秒两次运行」的门结构上无法执行,而那条门
    自己的回退方案(靠真实时钟抢同一秒)会红掉正确实现。
    """
    return datetime.now().strftime(contracts()["patterns"]["timestamp_format"])


def create_archive_dir(out: str | Path) -> tuple[str, Path]:
    """独占创建 `out/<ts>`,碰撞则 `-2`、`-3`… 返回 `(run_id, dir)`。

    **`os.mkdir` 而非 `exist_ok=True`** —— 时间戳是秒粒度而单次运行是毫秒级,
    同秒两次运行必然碰撞;`exist_ok=True` 会让第二次静默覆盖第一次的结果。
    这是数据丢失,不是 UI 问题,也是 I10(历史无悬空条目)的基础。
    """
    base = Path(out)
    base.mkdir(parents=True, exist_ok=True)   # `--out` 本身可以不存在(ADR-039)
    ts = _timestamp()
    suffix = 1
    while True:
        run_id = ts if suffix == 1 else f"{ts}-{suffix}"
        path = base / run_id
        try:
            os.mkdir(path)                     # 独占;已存在则抛
        except FileExistsError:
            suffix += 1
            continue
        return run_id, path


# ───────────────────────── RunReport / run.json ─────────────────────────


def build_report(run_id: str, request, results) -> Any:
    from nullhypothesis.run import RunReport

    c = contracts()
    return RunReport(
        run_id=run_id,
        request=request,
        results=list(results),
        assumptions=[
            c["frozen_text"]["assumption_adjusted"],
            c["frozen_text"]["assumption_market_order"],
        ],
    )


def write_run_json(report, png_paths: dict[str, str], out_dir: Path) -> None:
    """ADR-037 的 schema,逐字段等于 `GET /api/runs/{id}` 的响应体(阶段二)。

    跨阶段锁定边界 —— 字段名错会让阶段二读不到阶段一的产物。
    """
    from nullhypothesis.report import _summary_json

    payload = {
        "run_id": report.run_id,
        "request": {
            "strategies": list(report.request.strategies),
            "data_file": report.request.data_file,
            "cash": report.request.cash,
            "fee": report.request.fee,
        },
        "results": [
            {
                "strategy": r.strategy,
                "summary": _summary_json(r.summary),
                "png_path": png_paths.get(r.strategy),
            }
            for r in report.results
        ],
        "assumptions": list(report.assumptions),
        "comparison_png_path": png_paths.get("comparison"),
    }
    (out_dir / contracts()["filenames"]["run_json"]).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ───────────────────────── CLI(ADR-039)─────────────────────────


class _ArgError(Exception):
    """参数非法 —— 退出码 `exit_codes.invalid_cli_args`。"""


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="backtest.py", add_help=True)
    p.add_argument("--data", required=True)
    p.add_argument("--strategy", action="append", required=True)
    p.add_argument("--cash", type=float, default=100000.0)
    p.add_argument("--fee", type=float, default=0.0)
    p.add_argument("--out", default="out")
    return p.parse_args(argv)


def _validate(ns: argparse.Namespace) -> None:
    if ns.cash <= 0:
        raise _ArgError(f"--cash 必须 > 0,收到 {ns.cash}")
    if not (0.0 <= ns.fee < 0.1):
        raise _ArgError(f"--fee 必须在 [0, 0.1) 内,收到 {ns.fee}")
    if not Path(ns.data).is_file():
        raise _ArgError(f"--data 文件不存在:{ns.data}")

    stems: dict[str, str] = {}
    for s in ns.strategy:
        path = Path(s)
        if not path.is_file():
            raise _ArgError(f"--strategy 文件不存在:{s}")
        # ADR-037:stem 相同会让 <stem>_trades.csv 互相覆盖。
        if path.stem in stems:
            raise _ArgError(
                f"--strategy 的 stem 重复:{path.stem}("
                f"{stems[path.stem]} 与 {s})—— 输出文件名会互相覆盖"
            )
        stems[path.stem] = s


def main(argv: Sequence[str] | None = None) -> int:
    from nullhypothesis.data import load_csv
    from nullhypothesis.run import RunRequest, run
    from nullhypothesis import plot, report as report_mod

    try:
        ns = _parse(argv)
        _validate(ns)
    except _ArgError as e:
        print(f"❌ 参数错误:{e}", file=sys.stderr)
        return _exit_code("invalid_cli_args")
    except SystemExit as e:                     # argparse 自己的退出
        return _exit_code("invalid_cli_args") if e.code else _exit_code("ok")

    try:
        df = load_csv(ns.data)
    except DataValidationError as e:
        print(f"❌ {e}", file=sys.stderr)
        return _exit_code("data_validation")

    try:
        results = run(ns.strategy, df, cash=ns.cash, fee=ns.fee)
    except StrategyError as e:
        # ADR-022:立即终止,**不写任何输出文件**,错误含交易日序号+日期+traceback。
        print(f"❌ {e}", file=sys.stderr)
        if e.__cause__ is not None:
            traceback.print_exception(
                type(e.__cause__), e.__cause__, e.__cause__.__traceback__,
                file=sys.stderr,
            )
        return _exit_code("strategy_error")

    # 跑成功了才建归档目录 —— 失败的运行不得留下空目录,
    # 否则 I10 会把它列成一个没有内容的 run。
    run_id, out_dir = create_archive_dir(ns.out)
    request = RunRequest(
        strategies=[Path(s).name for s in ns.strategy],
        data_file=Path(ns.data).name,
        cash=ns.cash,
        fee=ns.fee,
    )
    report = build_report(run_id, request, results)

    png_paths = plot.write(report, out_dir)
    text = report_mod.render(report, out_dir)
    write_run_json(report, png_paths, out_dir)

    print(text)
    if results[0].summary.bars == 1:
        print(f"\n⚠ {contracts()['frozen_text']['single_day_notice']}")
    print(f"\n归档:{out_dir}")
    return _exit_code("ok")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
