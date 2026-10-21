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
import re
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
    # ── 阶段三(ADR-053 / T20、ADR-050·051·052 / T21)加性追加 ──
    # 这几个名字已进 ADR-035 的模块清单,故也进这张可执行投影:
    # §4.5 自检规则二 —— 门要采样的内部序列必须是模块级公开名,
    # 否则实现者内联它,门就结构上无法执行。
    ("nullhypothesis.data", "load_events"),                # ADR-053
    ("nullhypothesis.data", "CorporateEvent"),             # ADR-053
    ("nullhypothesis.data", "cumfactors_from_events"),     # T21 门 4/9/10 采样它
    ("nullhypothesis.data", "cumfactors_from_adj_close"),  # ADR-052 路径一
    ("nullhypothesis.data", "UnadjustedPriceWarning"),     # ADR-051 的体检通道
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
    # `close` 由 ADR-049 补入(前端要在同一张图上画价格对照曲线)。
    ("nullhypothesis.run", "EquityPoint",
     {"date", "cash", "shares", "equity", "close"}),
    # `events`(ADR-053 / T20)与 `slippage`(ADR-056 / T22)**追加在末尾**,
    # 顺序依 ADR-044 的字段表。两者都可空/有默认,故既有的四位置参数构造
    # 逐字仍可用。追加而非插入的理由同下面的 `Fill.amount`:ADR-044 钉死了
    # 【顺序】,插在中间会让既有门的位置构造静默错位。
    # 门仍是**集合相等**(不是「包含」)—— 多一个字段同样要红,否则这条门
    # 会放过任何未经 ADR 的字段。两个字段在此**并列**,正是 T22 门 17 /
    # T23 门 19 要求的「保留双方字段、不得以单方版本整体覆盖」。
    ("nullhypothesis.run", "RunRequest",
     {"strategies", "data_file", "cash", "fee", "events", "slippage"}),
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


# ───────── ADR-038 的名字契约镜像(本轮返工补的门)─────────
#
# **此前这条契约完全没有机械门。** DESIGN.md §ADR-038 的加性扩展条款与
# §「`pydantic` 模型在 `api.py` 中是单一真相来源,`frontend/src/types.ts`
# 逐字段镜像它」把更新义务按 §4.5 的「谁引入谁负责」分派给 T20/T22/T23,
# 却没有任何测试断言镜像成立 —— `frontend/src/__tests__/layout.test.ts`
# 只断言 `types.ts` **存在**。T22 第一轮正是在这里漏了 `slippage`:后端
# 加了字段、`run.json` 也带上了,而 TS 类型仍是四字段,人读交叉核对漏掉。
#
# 用**正则读源码文本**而非解析 TS:仓库没有 TS 解析器(ADR-037b 钉死了
# 前端依赖集合,加一个解析器即违反那条契约),而字段声明的形态足够规整。
# 门放在 Python 侧而不是 vitest 侧,因为**只有这一侧能同时看到两个文件**
# ——`layout.test.ts` 的 `import.meta.glob` 作用域是 `src/**`,够不到
# `api.py`,它无从得知真相来源长什么样。

TYPES_TS = ROOT / "frontend" / "src" / "types.ts"
API_TS = ROOT / "frontend" / "src" / "api.ts"


def _ts_block_fields(source: str, header: str) -> set[str]:
    """取 `header` 处那个 `{...}` 块里的顶层字段名。

    只认 `name:` / `name?:` 形态的行,跳过注释行 —— 本仓库的 TS 块注释里
    满是 `fee` / `slippage` 这类词,按裸子串匹配会把散文当成字段声明。

    从 `header` **之后的第一个 `{`** 开始配对,而不是从 `header` 末尾开始
    计数:两个调用点的 header 一个含开括号(`... RunRequest {`)、一个不含
    (`... request:`),从末尾计数会让前者的 `depth` 以 0 起步,于是本块的
    闭括号把它降到 −1 而非 0、`break` 不触发,扫描溢出到**下一个**块里
    (实测会把 `run()` 里 fetch 选项的 `method` / `headers` / `body` 当成
    `RunRequest` 的字段)。先定位开括号则两种 header 同构。
    """
    open_at = source.index("{", source.index(header) + len(header) - 1)
    depth, end = 0, None
    for i, ch in enumerate(source[open_at:], open_at):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, f"{header} 之后没有闭合的块"
    body, fields, in_comment = source[open_at + 1:end], set(), False
    for raw in body.split("\n"):
        line = raw.strip()
        if in_comment:
            if "*/" in line:
                in_comment = False
            continue
        if line.startswith("/*"):
            in_comment = "*/" not in line
            continue
        if line.startswith("//") or line.startswith("*"):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\??\s*:", line)
        if m:
            fields.add(m.group(1))
    return fields


def test_types_ts_mirrors_the_pydantic_request_model():
    """`ArchivedRun.request` 的键集合 == `RunRequestBody` 的字段集合。

    ADR-038 的归属条款:引入字段的任务**同时**更新两侧。漏掉 TS 一侧的
    后果不止类型不准 —— `GET /api/runs/{id}` 返回的键在界面上没有落点,
    ADR-056 的机制 2(「每张图、每个 JSON 都自带『这条曲线假设了多少
    成本』」)就只到后端为止,而那条机制正是它用来替代「非零默认值」的
    诚实性论证本身。
    """
    import api

    source = TYPES_TS.read_text(encoding="utf-8")
    # 锚在 `ArchivedRun` 的声明上,再在其内部找 `request:` —— 不写成一个
    # 跨行字面量:`run_id` 就在两者之间,把它们拼成一个 header 是在断言
    # 一个**顺序**,而本门管的是字段**集合**。
    ts = _ts_block_fields(source[source.index("export interface ArchivedRun"):],
                          "request:")
    py = set(api.RunRequestBody.model_fields)
    assert ts == py, (
        "frontend/src/types.ts 的 ArchivedRun.request 与 api.py 的 "
        f"RunRequestBody 不再逐字段镜像(ADR-038):TS 多 {ts - py}、缺 {py - ts}"
    )


def test_api_ts_request_body_mirrors_the_pydantic_model():
    """`api.ts` 的 `RunRequest`(POST 请求体)同样逐字段镜像真相来源。

    与上一条分开:归档读路径与 run 写路径是两个独立的漏点,T22 第一轮
    两侧都漏了。
    """
    import api

    ts = _ts_block_fields(API_TS.read_text(encoding="utf-8"),
                          "export interface RunRequest {")
    py = set(api.RunRequestBody.model_fields)
    assert ts == py, (
        "frontend/src/api.ts 的 RunRequest 与 api.py 的 RunRequestBody "
        f"不再逐字段镜像(ADR-038):TS 多 {ts - py}、缺 {py - ts}"
    )


def test_run_json_request_block_mirrors_the_same_field_set():
    """第三处:`run.json` 的 `request` 块(ADR-037)。

    `RunRequest`(ADR-044 的值对象)与 `RunRequestBody`(pydantic)是同一
    契约的两个投影,ADR-038 要求「逐字段等于」。字段集合必须一致,否则
    CLI 归档出来的 `run.json` 与 API 归档出来的不同构,而前端用**同一个**
    TS 类型读两者。
    """
    import api
    from nullhypothesis.run import RunRequest

    assert set(RunRequest.__dataclass_fields__) == set(api.RunRequestBody.model_fields), (
        "RunRequest(ADR-044)与 RunRequestBody(ADR-038)的字段集合分叉了"
    )
