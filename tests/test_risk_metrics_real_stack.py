"""T23 的 REAL_STACK_GATE(T14)· 真实 CSV + 完整 CLI 跑出风险指标。

与 `tests/test_risk_metrics.py` 的分工:那边用**手工构造的** equity 序列钉死
算术(含四条 ⭐ 真门),这边跑**真实的 2516 行 `data/synthetic.csv` + 完整
CLI 子进程**,证明两个指标在真实规模的数据上确实落到了 `summary.json`。

**Idle 那一半不可只在前端 mock 上验** —— 必须断言**后端真的产出了 `null`**
(ADR-058:`None` → `null` → 按 `formats.null_display` 显示)。
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))
FX = CONTRACTS["fixtures"]["adr058_metrics"]
DATA = ROOT / "data" / "synthetic.csv"
RISK_KEYS = (
    "sharpe", "max_drawdown_pct",
    "max_drawdown_peak_date", "max_drawdown_trough_date",
)


def _run_cli(tmp_path: Path, strategy: Path) -> tuple[dict, str]:
    """跑完整 CLI 子进程,返回 (summary 字典, summary.json 原文)。

    **子进程**而非进程内调用 —— REAL_STACK_GATE 要覆盖的正是 CLI 这层装配
    (参数解析 → load_csv → run → build_report → render),进程内调 `run()`
    会绕过它。
    """
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "backtest.py",
         "--data", str(DATA), "--strategy", str(strategy), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert proc.returncode == CONTRACTS["exit_codes"]["ok"], proc.stdout + proc.stderr

    archives = sorted(out.glob("*/summary.json"))
    assert archives, f"没有产出 summary.json:{proc.stdout}"
    raw = archives[-1].read_text(encoding="utf-8")
    return json.loads(raw)["results"][0]["summary"], raw


def test_real_stack_buy_and_hold_risk_metrics(tmp_path):
    """REAL_STACK_GATE · `buy_and_hold` + 真实 CSV。

    * `sharpe` 是**有限浮点**(不是 None / NaN / Infinity)
    * `0 < max_drawdown_pct < 100`
    * 两日期匹配 `contracts.yaml` 的日期正则,且 `peak <= trough`
    """
    s, raw = _run_cli(tmp_path, ROOT / "strategies" / "buy_and_hold.py")

    for k in RISK_KEYS:
        assert k in s, f"{k} 没到 summary.json"

    assert isinstance(s["sharpe"], float) and math.isfinite(s["sharpe"]), (
        f"真实数据上的夏普必须是有限浮点,得到 {s['sharpe']!r}"
    )
    assert 0.0 < s["max_drawdown_pct"] < 100.0, (
        "真实数据必然有回撤且不可能跌满 100%"
    )

    # 日期格式按 `formats.date`(%Y-%m-%d)转成的正则
    date_rx = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    assert date_rx.match(s["max_drawdown_peak_date"])
    assert date_rx.match(s["max_drawdown_trough_date"])
    assert s["max_drawdown_peak_date"] <= s["max_drawdown_trough_date"]

    # 符号位:真实数据上也不得出现 -0.0
    assert math.copysign(1.0, s["max_drawdown_pct"]) > 0.0
    for token in FX["forbidden_json_tokens"]:
        assert token not in raw, f"{token} 不是合法 JSON"


def test_real_stack_idle_strategy_emits_true_null(tmp_path):
    """REAL_STACK_GATE · `Idle` 策略 → 后端**真的**产出 `null`。

    **不可只在前端 mock 上验。** 这里断言 `summary.json` 里是字面 `null`,
    且屏幕文本按 `formats.null_display` 渲染 —— 两端同源。

    `sharpe` 为空值标记表示该值在**数学上未定义**(不交易),**不表示 0**
    (ADR-059 的声明逐字覆盖这一点)。
    """
    strat = tmp_path / "idle.py"
    strat.write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "\n"
        "\n"
        "class Idle(Strategy):\n"
        "    def next(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    s, raw = _run_cli(tmp_path, strat)

    assert s["sharpe"] is None, "后端未产出 null —— 夏普在不交易时必须未定义"
    assert '"sharpe": null' in raw, "JSON 里必须是字面 null,不是 NaN/字符串"

    # 不交易 ⇒ 净值恒定 ⇒ 回撤是**确定事实** 0.0,但两个日期 None
    assert s["max_drawdown_pct"] == 0.0
    assert s["max_drawdown_peak_date"] is None
    assert s["max_drawdown_trough_date"] is None

    # ⭐ 真实路径上的 -0.0 守卫(14 条 fixture 里 4 条命中这一类)
    assert math.copysign(1.0, s["max_drawdown_pct"]) > 0.0, "回撤是 -0.0!"
    assert "-0.0" not in raw, "summary.json 含 -0.0 —— 必须用 abs() 而非 -worst"

    for token in FX["forbidden_json_tokens"]:
        assert token not in raw


def test_real_stack_screen_text_renders_null_display(tmp_path):
    """屏幕上 `sharpe` 显示为 `formats.null_display`,且回撤不带负号。"""
    strat = tmp_path / "idle.py"
    strat.write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "\n"
        "\n"
        "class Idle(Strategy):\n"
        "    def next(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "backtest.py",
         "--data", str(DATA), "--strategy", str(strat), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert proc.returncode == CONTRACTS["exit_codes"]["ok"], proc.stderr

    text = (sorted(out.glob("*/summary.txt"))[-1]).read_text(encoding="utf-8")
    assert CONTRACTS["formats"]["null_display"] in text
    # 屏幕上**绝不**出现 -0.00% —— 这正是 abs() 要避免的那个显示 bug
    assert "-0.00%" not in text
