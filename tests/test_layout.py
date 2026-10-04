"""跨任务最终门 · ADR-035 的布局契约(§5)。

**没有它,布局契约没有任何强制力。** ADR-035 自述的失败模式是「`pytest`
收集为零或全是 ImportError」—— 但**收集为零时 `pytest -q` 的退出码是 5,
而「没有测试」常被误读为绿**;`compileall` 在任何布局下也都退出 0。
所以那个失败模式恰恰是其余的门看不见的。

本文件是 ADR-035 的**可执行投影**:每个模块真的能 import,每个被钉死的
符号真的存在。第 8 轮审查的诊断是「有可执行投影的部分零回归,只靠人读
交叉核对的部分每轮都出缺陷」—— 这就是那个投影。
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
C = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))


# ───────────────────────── 模块与符号 ─────────────────────────

MODULES = [
    "nullhypothesis",
    "nullhypothesis.data",
    "nullhypothesis.strategy",
    "nullhypothesis.account",
    "nullhypothesis.engine",
    "nullhypothesis.run",
    "nullhypothesis.report",
    "nullhypothesis.plot",
    "nullhypothesis.errors",
    "backtest",
]

SYMBOLS = [
    ("nullhypothesis.data", "load_csv"),
    ("nullhypothesis.strategy", "Strategy"),
    ("nullhypothesis.strategy", "load_strategy"),
    ("nullhypothesis.account", "Account"),
    ("nullhypothesis.engine", "Backtest"),
    ("nullhypothesis.engine", "Fill"),
    ("nullhypothesis.run", "run"),
    ("nullhypothesis.run", "compute_gaps"),
    ("nullhypothesis.report", "render"),
    ("nullhypothesis.plot", "write"),
    ("nullhypothesis.errors", "DataValidationError"),
    ("nullhypothesis.errors", "StrategyError"),
    ("backtest", "main"),
    ("backtest", "_timestamp"),
    ("backtest", "create_archive_dir"),
    ("backtest", "build_report"),
    ("backtest", "write_run_json"),
]

# ADR-044 的值对象 —— T6/T7 的门要**手工构造**它们,故字段名是硬契约。
VALUE_OBJECTS = [
    ("nullhypothesis.run", "EquityPoint", {"date", "cash", "shares", "equity"}),
    ("nullhypothesis.run", "RunRequest", {"strategies", "data_file", "cash", "fee"}),
    ("nullhypothesis.run", "RunResult",
     {"strategy", "equity", "trades", "trades_ledger", "summary"}),
    ("nullhypothesis.run", "RunReport",
     {"run_id", "request", "results", "assumptions"}),
    # `amount` 由 ADR-046 补入(批准稿的交易清单有「金额」列,而 ADR-025
    # 禁止前端算 shares × price)。
    ("nullhypothesis.engine", "Fill",
     {"date", "side", "shares", "price", "fee", "amount"}),
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)


@pytest.mark.parametrize("module,symbol", SYMBOLS)
def test_pinned_symbol_exists(module, symbol):
    mod = importlib.import_module(module)
    assert hasattr(mod, symbol), f"ADR-035 钉死了 {module}.{symbol},但它不存在"


def test_account_surface_is_pinned():
    """`equity_at` 此前只出现在 T3 的门里、不在任何 ADR 中 —— T3 的 agent
    可以把它叫 `value_at` 并让自己的门通过,而 T5 的 I3 门又读不到。"""
    from nullhypothesis.account import Account

    for name in ("apply", "equity_at", "cash", "shares"):
        assert hasattr(Account, name), f"ADR-035 钉死了 Account.{name}"


def test_backtest_aggregate_surface_is_pinned():
    """在**实例**上查 —— `queue` 是每实例一份的列表(它必须如此:两个
    `Backtest` 共享一个队列会让多策略隔离失效),故类上没有这个名字。"""
    from nullhypothesis.engine import Backtest

    bt = Backtest(cash=1000.0)
    for name in ("queue", "enqueue", "resolve_queue", "cash", "shares", "equity_at"):
        assert hasattr(bt, name), f"ADR-035 钉死了 Backtest.{name}"
    assert isinstance(bt.queue, list)

    # 每实例一份,不是类级共享 —— 共享会让 run([A, B]) 的两条策略互相串味。
    other = Backtest(cash=1000.0)
    assert bt.queue is not other.queue


def test_account_is_not_reachable_from_outside_the_aggregate():
    """§1:`Account` **不可被聚合外引用**;ADR-044 重申聚合只暴露不可变快照。

    第一轮 T4 把它存成公开属性 `self.account`,而它自己的测试就从聚合外
    调了 `bt.account.equity_at(...)` —— 那是边界真的开着的证据。
    """
    from nullhypothesis.engine import Backtest

    bt = Backtest(cash=1000.0)
    assert not hasattr(bt, "account"), (
        "Account 被暴露为公开属性 —— §1 声明它不可被聚合外引用"
    )


@pytest.mark.parametrize("module,name,fields", VALUE_OBJECTS)
def test_value_object_fields_are_pinned(module, name, fields):
    mod = importlib.import_module(module)
    obj = getattr(mod, name)
    assert set(obj.__dataclass_fields__) == fields, (
        f"{name} 的字段与 ADR-044 不符 —— T6/T7 的门要手工构造它"
    )


def test_summary_fields_match_the_contract():
    """`Summary` 的字段必须与 `contracts.yaml` 的 `field_formats` 逐一对应。

    两者各自演化会让屏幕渲染与 JSON 投影对不上。
    """
    from nullhypothesis.run import Summary

    assert set(Summary.__dataclass_fields__) == set(C["field_formats"])


# ───────────────── 签名完整性(第 5 轮的教训)─────────────────


def test_render_and_write_accept_out_dir():
    """`render` 的签名此前没有 `out_dir`,而 T6 的门要求它往运行目录写两个
    文件 —— 而 ADR-035 是冻结契约,T6 只能停下上报,在无人值守时就是挂死。"""
    from nullhypothesis import plot, report

    assert "out_dir" in inspect.signature(report.render).parameters
    assert "out_dir" in inspect.signature(plot.write).parameters


def test_timestamp_is_a_patchable_module_level_seam():
    """被 `monkeypatch` 的符号必须是模块级公开名 —— 内联 `datetime.now()`
    会让 T8 的碰撞门结构上无法执行(自检规则二)。"""
    import backtest

    assert inspect.getmodule(backtest._timestamp) is backtest


# ───────────────── 零收集陷阱(ADR-035 自述的失败模式)─────────────────


def test_suite_actually_collects_tests():
    """`pytest` 在零收集时退出码是 **5**,而「没有测试」常被误读为绿。

    ADR-035 自述的失败模式(布局错 → 收集为零)恰恰是其余的门看不见的,
    所以这里直接断言收集到的测试数 > 0。
    """
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", str(ROOT / "tests")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert r.returncode != 5, "pytest 零收集 —— 布局或 import 坏了"
    assert r.returncode == 0, r.stdout[-2000:]
    assert "no tests ran" not in r.stdout.lower()


# ───────────────── 目录结构 ─────────────────


def test_pinned_directories_exist():
    for d in ("nullhypothesis", "strategies", "tests"):
        assert (ROOT / d).is_dir(), f"ADR-035 钉死了目录 {d}/"


def test_sample_strategies_are_named_as_pinned():
    """文件名钉死 —— 阶段二 §12 的人工门按名引用 `buy_and_hold`。"""
    for name in C["filenames"]["sample_strategies"]:
        assert (ROOT / "strategies" / name).is_file(), f"样例策略 {name} 不存在"
