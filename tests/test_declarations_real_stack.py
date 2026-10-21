"""T24 的 REAL_STACK_GATE · 声明文案经真实栈逐字到达每一个出口。

**门的原话**(DESIGN.md §15 T24):跑一次完整 CLI,`cat` 出 `summary.txt`,
断言新声明**逐字出现**;再经 API 跑一次,断言 `summary.json` 的 `assumptions`
字段同源。

与 `tests/test_design_binding.py` 的分工:那边断言 **`contracts.yaml` 自洽**
(每条声明承载的语义、`assumption_order` 的覆盖、OQ-12 的无数值);这边跑
**真实的 2516 行 `data/synthetic.csv` + 完整 CLI 子进程 + 真实 API 调用**,
证明那些声明确实**落到了用户看得见的字节上**。

**为什么这条门必须存在,而且必须是「全集 + 有序 + 同源」三件事一起断言**:

  * **全集** —— 一个只印旧两条的实现(或新增一条声明却忘了加进
    `assumption_order` 的实现)在功能上完全正常:没有异常、没有空值、
    回测数字分毫不差。它的唯一症状是**一条已知偏差从屏幕上消失了**。
    这是本阶段最危险的失效模式:它不产生错误,它产生**沉默**。
  * **有序** —— 顺序是 ADR-055/059 的叙事顺序(价格怎么来的 → 成交怎么
    发生 → 成交贵了多少 → 由此算出的两个比率意味着什么)。乱序不会让任何
    数字变错,但会让这组声明读不成一段话。
  * **同源** —— CLI 与 API 若各自装配一份清单,两份清单会漂移。T24 的做法
    是**唯一装配点** `backtest.assumption_texts()`,本门从**产物**(而不是
    从那个函数)反向验证这件事:四个出口的列表必须逐项相等。

**并反向断言被退役的说法不得回归**(ADR-050 / ADR-055 / ADR-056):
「OHLC 已预先复权」「框架不验证」「未计滑点」。这三句在今天的代码上**是假的**,
而假话留在屏幕上不会让任何既有门变红 —— 故这里给它们一条专门的门。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import api
import backtest

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))
DATA = ROOT / "data" / "synthetic.csv"
STRATEGY = ROOT / "strategies" / "buy_and_hold.py"

# ADR-050 / ADR-055 / ADR-056 退役的说法。**逐字**,不用正则 ——
# 这些是要被禁的**字面串**,正则只会引入元字符误报。
RETIRED_CLAIMS = (
    "OHLC 已预先复权",      # ADR-050:复权在框架内(ADR-001 主张 B/C 被推翻)
    "框架不验证",            # ADR-051/054:框架确实在验证
    "未计滑点",              # ADR-056:框架有 slippage,且其边界已被声明
    "复权只能发生在数据",    # ADR-050:复权就发生在框架内的 load_csv
    "复权是框架外的责任",    # 同上
)


@pytest.fixture(scope="module")
def expected() -> list[str]:
    """唯一装配点产出的那个列表 —— 本门的参照系。"""
    texts = backtest.assumption_texts()
    # 正向钉死规模与无重复:一个只印旧两条的实现不得通过本门
    assert len(texts) == len(CONTRACTS["assumption_order"]) > 2, (
        "assumption_order 的规模不对 —— ADR-055 + ADR-059 的声明远多于两条"
    )
    assert len(set(texts)) == len(texts), "有声明被印了两遍"
    return texts


def _run_cli(tmp_path: Path, *extra: str) -> tuple[str, dict, dict]:
    """跑完整 CLI **子进程**,返回 `(summary.txt 原文, summary.json, run.json)`。

    **子进程**而非进程内调用 —— REAL_STACK_GATE 要覆盖的正是 CLI 这层装配
    (参数解析 → load_csv → run → build_report → render → write_run_json),
    进程内调 `run()` 会绕过它。
    """
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "backtest.py",
         "--data", str(DATA), "--strategy", str(STRATEGY), "--out", str(out),
         *extra],
        cwd=ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert proc.returncode == CONTRACTS["exit_codes"]["ok"], proc.stdout + proc.stderr

    archives = sorted(out.glob("*/summary.txt"))
    assert archives, f"没有产出 summary.txt:{proc.stdout}"
    adir = archives[-1].parent
    return (
        (adir / "summary.txt").read_text(encoding="utf-8"),
        json.loads((adir / "summary.json").read_text(encoding="utf-8")),
        json.loads((adir / "run.json").read_text(encoding="utf-8")),
    )


# ═══════════════ CLI 侧 ═══════════════


def test_real_stack_cli_summary_txt_carries_every_declaration(tmp_path, expected):
    """门 6 的 CLI 一半:每条声明**逐字**出现在 `summary.txt` 里。

    用真实的 2516 行 CSV + `--slippage` 的一个非零值跑 —— 后者让 ADR-059
    的滑点七条在一条**真的扣了成本**的曲线旁边出现,而不是在默认 0 的
    happy path 上。
    """
    txt, _, _ = _run_cli(tmp_path, "--slippage", "0.0005")
    for a in expected:
        assert a in txt, f"summary.txt 里缺这条声明:{a}"


def test_real_stack_cli_json_outputs_are_same_source_and_ordered(tmp_path, expected):
    """`summary.json` 与 `run.json` 的 `assumptions` **逐项有序**相等。

    两个文件由不同的写入路径产出(`report.render` 与
    `backtest.write_run_json`),它们相等**不是**同义反复 —— 它证明两条路径
    消费的是同一个 `RunReport.assumptions`。
    """
    _, sj, rj = _run_cli(tmp_path)
    assert sj["assumptions"] == expected, "summary.json 与装配点不同源"
    assert rj["assumptions"] == expected, "run.json 与装配点不同源"


def test_real_stack_cli_prints_no_retired_claim(tmp_path, expected):
    """退役的说法不得出现在屏幕上。

    这条门守的是 ADR-055 那句话:若只新增新文案而不退役旧的,
    `summary.txt` 会在相邻两行印出**两条互相矛盾的复权声明**。
    """
    txt, sj, rj = _run_cli(tmp_path, "--slippage", "0.0005")
    for dead in RETIRED_CLAIMS:
        assert dead not in txt, (
            f"summary.txt 还在印一句已被推翻的话:「{dead}」—— "
            "它与同一块里的新声明互相矛盾(ADR-055)"
        )
        for blob in (json.dumps(sj, ensure_ascii=False),
                     json.dumps(rj, ensure_ascii=False)):
            assert dead not in blob, f"JSON 产物里还留着:「{dead}」"


def test_real_stack_cli_declaration_order_is_the_narrative_order(tmp_path, expected):
    """声明在屏幕上的**先后**与 `assumption_order` 一致。

    按在 `summary.txt` 里的字节位置排序,断言与 `expected` 同序 ——
    不只是「都出现了」。
    """
    txt, _, _ = _run_cli(tmp_path)
    positions = [txt.index(a) for a in expected]
    assert positions == sorted(positions), (
        "声明在 summary.txt 里的顺序与 assumption_order 不一致 —— "
        "顺序是 ADR-055/059 的叙事顺序,乱序会让这组声明读不成一段话"
    )


# ═══════════════ API 侧(门 6 的另一半)═══════════════


def test_real_stack_api_is_same_source_as_the_cli(tmp_path, expected):
    """经 API 跑一次,断言响应、归档的 `summary.json`、以及
    `GET /api/runs/{id}` 三者的 `assumptions` 与 CLI 同源。

    `GET /api/runs/{id}` 这一跳是必须的:ADR-037 声明它「逐字段等于归档的
    `run.json`」,而一个在**输出时**丢掉或改写 `assumptions` 的实现能通过
    前两跳。
    """
    client = TestClient(api.app)
    r = client.post("/api/run", json={
        "strategies": [STRATEGY.name], "data_file": DATA.name,
        "cash": 100000.0, "fee": 0.0, "slippage": 0.0005,
    })
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["assumptions"] == expected, "POST /api/run 与装配点不同源"

    run_id = payload["run_id"]
    archived = json.loads(
        (api.OUT_DIR / run_id / "summary.json").read_text(encoding="utf-8")
    )
    assert archived["assumptions"] == expected, "API 归档的 summary.json 不同源"

    got = client.get(f"/api/runs/{run_id}")
    assert got.status_code == 200, got.text
    assert got.json()["assumptions"] == expected, (
        "GET /api/runs/{id} 丢掉或改写了 assumptions(ADR-037 的逐字段相等)"
    )
