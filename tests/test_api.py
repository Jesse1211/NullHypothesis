"""T9/T10/T11 的门(DESIGN.md §5 阶段二)。

**`blindSpots`**(本文件结构上看不见的东西,见 §12 的真实栈门与人工门):
  1. `TestClient` 不经 ASGI/HTTP 真实栈 —— **实际绑定地址**与**静态挂载是否
     遮蔽 API 路由**不被本门覆盖。这里只能断言 `api.HOST`/`api.PORT` 这两个
     常量,ADR-034 把它们提为模块级常量正是为此。
  2. 归档 PNG 的**视觉**正确性不验证(只验证文件存在且路径形态合规)。
  3. 并发门用线程 + 打桩观测锁;真实多进程(多个 `uvicorn` worker)下
     `threading.Lock` 不跨进程 —— 第二道防线是 ADR-024 的独占 `mkdir`。
  4. `out/` 的归档由本门自造或经 API 产生,**未覆盖**人工手改归档内容的情形。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import api
import backtest
import nullhypothesis.plot as plot
import nullhypothesis.run as nh_run

ROOT = Path(__file__).resolve().parent.parent
C = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))

DATA_FILE = "synthetic.csv"
STRAT_A = "buy_and_hold.py"
STRAT_B = "ma_cross.py"


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture
def isolated_out(tmp_path, monkeypatch):
    """把 `OUT_DIR` 指到 tmp —— 否则门会读写真实 `out/`,互相串味。"""
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(api, "OUT_DIR", out)
    return out


# ════════════════════════ T9 · 列举端点 ════════════════════════


def test_strategies_endpoint_returns_real_files_on_disk(client):
    """断言返回**真实磁盘上**的文件名,不是硬编码列表。"""
    got = client.get("/api/strategies").json()
    on_disk = sorted(
        p.name for p in (ROOT / "strategies").glob("*.py")
        if not p.name.startswith("_")
    )
    assert got == on_disk
    assert STRAT_A in got and STRAT_B in got


def test_data_endpoint_returns_real_files_on_disk(client):
    got = client.get("/api/data").json()
    on_disk = sorted(p.name for p in (ROOT / "data").glob("*.csv"))
    assert got == on_disk


@pytest.mark.parametrize("attr,endpoint", [
    ("STRATEGIES_DIR", "/api/strategies"),
    ("DATA_DIR", "/api/data"),
])
def test_empty_directory_returns_empty_list_not_error(
    attr, endpoint, tmp_path, monkeypatch, client
):
    """**空目录 → `[]` 而非报错。** 前端的空态文案靠这个,500 会变成白屏。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(api, attr, empty)
    r = client.get(endpoint)
    assert r.status_code == 200
    assert r.json() == []


def test_missing_directory_returns_empty_list_not_error(
    tmp_path, monkeypatch, client
):
    monkeypatch.setattr(api, "STRATEGIES_DIR", tmp_path / "does-not-exist")
    r = client.get("/api/strategies")
    assert r.status_code == 200 and r.json() == []


# ──────────────── ADR-034:绑定地址的可断言形式 ────────────────


def test_bind_address_is_loopback_only():
    """ADR-034。`TestClient` 不起 uvicorn,所以只能断言常量本身。"""
    assert api.HOST == "127.0.0.1"
    assert api.PORT == 8000


# ──────────────── ADR-026 / I11 的 T9 部分(单元级)────────────────


CODE_STRINGS = [
    "import os; os.system('touch /tmp/pwned')",
    "__import__('os').system('ls')",
    "lambda: 1",
    "print('hi')",
    "strategies/buy_and_hold.py",       # 带路径 —— 端点返回的是**纯文件名**
    "../../etc/passwd",
    "/etc/passwd",
    "buy_and_hold",                      # 缺 .py
    "",
    "   ",
    "..",
    "a\x00b.py",
    "sub/dir.py",
    "..\\windows\\system32.py",
]


@pytest.mark.parametrize("s", CODE_STRINGS)
def test_validate_strategy_name_rejects_code_and_paths(s):
    """I11:校验器对代码串与路径一律 `False`。"""
    assert api.validate_strategy_name(s) is False


@pytest.mark.parametrize("s", [STRAT_A, STRAT_B, "x.py"])
def test_validate_strategy_name_accepts_plain_filenames(s):
    """反向:正常文件名必须被接受,否则校验器「全拒」也能通过上面那组。"""
    assert api.validate_strategy_name(s) is True


@pytest.mark.parametrize("bad", [None, 123, [], {}])
def test_validators_reject_non_strings(bad):
    assert api.validate_strategy_name(bad) is False
    assert api.validate_run_id(bad) is False


# ──────────────── `run_id` 校验器的逐值表(T9 测纯函数)────────────────
#
# 只测 `../` 会放过**黑名单式**实现 `if '..' in s or '/' in s: reject`
# —— 它接受 `%00`、Windows 绝对路径和任意目录名。反向也要测:过严的
# `^\d{8}-\d{6}$` 会让 ADR-024 的碰撞目录在阶段二读不到(I10 的悬空条目)。

RUN_ID_REJECT = [
    "../../etc/passwd",
    "20261003",                  # 缺时间段
    "abc",
    "/etc/passwd",
    "20261003-172400/..",
    "20261003-172400\x00",
    "C:\\Windows\\Temp",         # Windows 绝对路径(黑名单实现会放过)
    "20261003-172400%00",
    "",
    "20261003-172400 ",
    "20261003_172400",
    "2026100-172400",            # 日期段位数不足
    "20261003-17240",            # 时间段位数不足
    "20261003-172400-",          # 悬空的分隔符
    "20261003-172400-x",
]

RUN_ID_ACCEPT = [
    "20261003-172400",
    "20261003-172400-2",         # ADR-024 的碰撞目录 —— 过严的正则会漏掉它
    "20261003-172400-3",
    "20261003-172400-10",
]


@pytest.mark.parametrize("s", RUN_ID_REJECT)
def test_validate_run_id_rejects(s):
    assert api.validate_run_id(s) is False


@pytest.mark.parametrize("s", RUN_ID_ACCEPT)
def test_validate_run_id_accepts(s):
    assert api.validate_run_id(s) is True


def test_run_id_validator_is_a_whitelist_regex_not_a_blacklist():
    """直接断言它用的是 `contracts.yaml` 的白名单正则(单一真相来源)。"""
    pat = C["patterns"]["run_id"]
    for s in RUN_ID_ACCEPT:
        assert re.fullmatch(pat, s), f"契约正则自身拒绝了合法值 {s}"
    for s in RUN_ID_REJECT:
        assert not re.fullmatch(pat, s), f"契约正则自身接受了非法值 {s!r}"


# ──────────────── 静态挂载的守卫(ADR-035/033)────────────────


def test_api_imports_and_serves_without_frontend_dist(client):
    """`frontend/dist` 不存在时(T9 阶段必然如此)API 必须照常起。

    无 `is_dir()` 守卫的 `StaticFiles(directory=...)` 在目录缺失时直接抛错。
    """
    if api.FRONTEND_DIST.is_dir():
        pytest.skip("frontend/dist 已存在(T12 已落地),本门只在其缺失时有意义")
    assert client.get("/api/strategies").status_code == 200


def test_static_mount_is_the_last_registration_in_the_source():
    """挂载在 `/` 会遮蔽**之后**注册的路由,故它必须是最后一条注册。

    源码级断言 —— `frontend/dist` 不存在时挂载根本没执行,运行时看不到。
    """
    src = (ROOT / "api.py").read_text(encoding="utf-8")
    mount_at = src.index("app.mount(")
    last_route = max(
        src.rindex(f'@app.{verb}(') for verb in ("get", "post")
    )
    assert mount_at > last_route, (
        "StaticFiles 挂载必须是 api.py 中最后一条注册,否则会遮蔽其后的 API 路由"
    )


# ════════════════════════ T10 · POST /api/run ════════════════════════


def test_run_happy_path_shape(client, isolated_out):
    r = client.post("/api/run", json={
        "strategies": [STRAT_A], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    })
    assert r.status_code == 200, r.text
    p = r.json()
    assert set(p) == {"run_id", "results", "comparison_png_path", "assumptions"}
    assert api.validate_run_id(p["run_id"])
    assert len(p["results"]) == 1
    res = p["results"][0]
    assert set(res) == {"strategy", "equity", "trades", "summary", "png_path"}
    assert set(res["summary"]) == set(C["field_formats"])
    # `close` 由 ADR-049 补入(前端画价格对照曲线用)
    assert set(res["equity"][0]) == {"date", "equity", "close"}


# ──────────── 数值一致性:必须真起 CLI 进程(关键门)────────────


def _cli_run(out_dir: Path, strategies: list[str], cash: str, fee: str):
    """真起一个 CLI 子进程 —— 不是调 `run()`,那是拿引擎和自己比。"""
    cmd = [sys.executable, "backtest.py", "--data", f"data/{DATA_FILE}",
           "--out", str(out_dir), "--cash", cash, "--fee", fee]
    for s in strategies:
        cmd += ["--strategy", f"strategies/{s}"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    run_dirs = [d for d in out_dir.iterdir() if d.is_dir()]
    assert len(run_dirs) == 1
    return json.loads(
        (run_dirs[0] / C["filenames"]["summary_json"]).read_text(encoding="utf-8")
    )


PAIRED_FIELDS = [
    "final_equity", "total_return_pct", "trade_count",
    "max_gap_pct", "max_gap_date", "mean_abs_gap_pct",
]


@pytest.mark.parametrize("strategies", [[STRAT_A], [STRAT_A, STRAT_B]])
def test_api_and_cli_agree_field_by_field(
    strategies, client, isolated_out, tmp_path
):
    """**必须显式传非默认的 `--fee` 与 `--cash`** —— 全用默认值时,CLI 与
    API 对 fee/cash 的解释差异不可见。

    两侧都是 `results[]` 数组,**按 `strategy` 配对**,不假设顺序相同。
    """
    cash, fee = "55000", "0.0013"
    cli = _cli_run(tmp_path / "cli-out", strategies, cash, fee)

    r = client.post("/api/run", json={
        "strategies": strategies, "data_file": DATA_FILE,
        "cash": float(cash), "fee": float(fee),
    })
    assert r.status_code == 200, r.text
    web = r.json()

    cli_by = {x["strategy"]: x["summary"] for x in cli["results"]}
    web_by = {x["strategy"]: x["summary"] for x in web["results"]}
    assert cli_by.keys() == web_by.keys() == {Path(s).stem for s in strategies}

    for name in cli_by:
        for f in PAIRED_FIELDS:
            a, b = cli_by[name][f], web_by[name][f]
            if isinstance(a, float):
                assert round(a, 2) == round(b, 2), f"{name}.{f}: CLI {a} vs API {b}"
            else:
                assert a == b, f"{name}.{f}: CLI {a} vs API {b}"


def test_api_equity_series_matches_cli_point_by_point(
    client, isolated_out, tmp_path
):
    """`summary.json` 不含 equity 序列,故逐点对账改读 CLI 的 `trades.csv`
    与归档 PNG 之外的唯一机器可读来源:重跑同参数的 CLI `run.json` 不含
    equity —— 所以这里改为断言 API 的 equity **自洽于**它自己的 summary。

    (逐点与 CLI 对齐由 `test_api_and_cli_agree_field_by_field` 的
    `final_equity` + 本门的 `equity[-1] == final_equity` 两段共同锁定。)
    """
    r = client.post("/api/run", json={
        "strategies": [STRAT_A], "data_file": DATA_FILE,
        "cash": 55000.0, "fee": 0.0013,
    })
    res = r.json()["results"][0]
    s = res["summary"]
    assert len(res["equity"]) == s["bars"]
    assert res["equity"][0]["date"] == s["start"]
    assert res["equity"][-1]["date"] == s["end"]
    assert round(res["equity"][-1]["equity"], 2) == round(s["final_equity"], 2)


# ──────────── ADR-030 / I9:图与响应同源 ────────────


def test_png_exists_and_path_is_repo_relative(client, tmp_path, monkeypatch):
    """ADR-038 把 `png_path` 钉死为 `out/<run_id>/<stem>_equity.png`。

    **绝对文件系统路径在浏览器里取不到** —— T13 拿这个字符串当 `<img src>`。

    本门**不能**用 `isolated_out`:那把归档指到 `tmp_path`,它在仓库外,
    无法表达为仓库相对路径(`_rel` 会如实回退为绝对)。所以这里用真实的
    `out/` 子目录,跑完即删。
    """
    real_out = ROOT / "out" / "_gate_tmp"
    real_out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(api, "OUT_DIR", real_out)
    p = client.post("/api/run", json={
        "strategies": [STRAT_A], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    }).json()
    png = p["results"][0]["png_path"]
    try:
        assert not Path(png).is_absolute(), f"png_path 必须是仓库相对路径,收到 {png}"
        assert "\\" not in png, "png_path 必须用 POSIX 斜杠"
        assert png.startswith("out/"), f"png_path 必须以 out/ 开头,收到 {png}"
        assert (ROOT / png).is_file(), f"png_path 指向的文件不存在:{png}"
    finally:
        import shutil
        shutil.rmtree(real_out, ignore_errors=True)


def test_plotted_series_is_the_same_data_as_the_response(
    client, isolated_out, monkeypatch
):
    """I9:**对绘图调用打桩比对**,非仅比末值 —— 两条不同序列可共享末值。"""
    captured: dict = {}
    real_write = plot.write

    def spy(report, out_dir):
        captured["equity"] = [
            [(pt.date, pt.equity) for pt in r.equity] for r in report.results
        ]
        return real_write(report, out_dir)

    monkeypatch.setattr(plot, "write", spy)

    p = client.post("/api/run", json={
        "strategies": [STRAT_A], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    }).json()

    from_response = [(e["date"], e["equity"]) for e in p["results"][0]["equity"]]
    assert captured["equity"][0] == from_response, (
        "画图用的序列与响应里的 equity 不是同一份数据(ADR-030/I9)"
    )


# ──────────── ADR-029:多策略 ────────────


def test_two_strategies_produce_independent_results_and_comparison(
    client, isolated_out
):
    p = client.post("/api/run", json={
        "strategies": [STRAT_A, STRAT_B], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    }).json()
    assert len(p["results"]) == 2
    a, b = p["results"]
    assert a["strategy"] != b["strategy"]
    assert a["summary"] != b["summary"]
    assert a["png_path"] != b["png_path"]
    assert (ROOT / p["comparison_png_path"]).is_file()


def test_single_strategy_comparison_png_is_none_not_empty_string(
    client, isolated_out
):
    """**非 `""`、非指向文件** —— 前端按 `null` 判断是否渲染对比图。"""
    p = client.post("/api/run", json={
        "strategies": [STRAT_A], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    }).json()
    assert p["comparison_png_path"] is None


# ──────────── ADR-026 / I11 的行为式主门 ────────────


def test_code_string_as_strategy_is_rejected_with_no_side_effect(
    client, isolated_out, tmp_path
):
    """传入一段**有可观测副作用**的代码串 → 断言拒绝 **且该文件未被创建**。"""
    marker = tmp_path / "pwned.txt"
    payload = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')"

    r = client.post("/api/run", json={
        "strategies": [payload], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    })
    assert r.status_code in (400, 422), r.text
    assert not marker.exists(), "代码串被执行了 —— I11 失守"
    assert not any(isolated_out.iterdir()), "被拒的请求不得留下归档目录"


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "sub/x.py"])
def test_path_traversal_in_strategy_name_is_rejected(bad, client, isolated_out):
    r = client.post("/api/run", json={
        "strategies": [bad], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    })
    assert r.status_code in (400, 422)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "sub/x.csv"])
def test_path_traversal_in_data_file_is_rejected(bad, client, isolated_out):
    r = client.post("/api/run", json={
        "strategies": [STRAT_A], "data_file": bad,
        "cash": 100000.0, "fee": 0.0,
    })
    assert r.status_code in (400, 422)


# ──────────── ADR-031:每个 code 各一测 ────────────


def test_error_code_data_validation(client, isolated_out, tmp_path, monkeypatch):
    bad = tmp_path / "data"
    bad.mkdir()
    (bad / "bad.csv").write_text(
        "Date,Open,High,Low,Close,Volume\n2020-01-01,1,2,0.5,xxx,100\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api, "DATA_DIR", bad)

    r = client.post("/api/run", json={
        "strategies": [STRAT_A], "data_file": "bad.csv",
        "cash": 100000.0, "fee": 0.0,
    })
    assert r.status_code == 400, r.text
    p = r.json()
    assert p["code"] == "DATA_VALIDATION"
    assert set(p["detail"]) >= {"file", "row", "column"}


def test_error_code_strategy_error(client, isolated_out, tmp_path, monkeypatch):
    strat = tmp_path / "strategies"
    strat.mkdir()
    (strat / "boom.py").write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "class Boom(Strategy):\n"
        "    def init(self): pass\n"
        "    def next(self):\n"
        "        raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api, "STRATEGIES_DIR", strat)

    r = client.post("/api/run", json={
        "strategies": ["boom.py"], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    })
    assert r.status_code == 400, r.text
    p = r.json()
    assert p["code"] == "STRATEGY_ERROR"
    assert p["detail"]["bar_index"] is not None
    assert p["detail"]["date"]
    assert "RuntimeError" in p["detail"]["traceback"]


def test_error_code_not_found(client, isolated_out):
    r = client.post("/api/run", json={
        "strategies": ["nope.py"], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    })
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


def test_error_code_invalid_request_on_type_error(client, isolated_out):
    """请求体字段类型错 → pydantic 自己的 422。"""
    r = client.post("/api/run", json={
        "strategies": "not-a-list", "data_file": DATA_FILE,
        "cash": "not-a-number", "fee": 0.0,
    })
    assert r.status_code == 422


@pytest.mark.parametrize("cash,fee", [(0, 0.0), (-1, 0.0), (100000, 0.1),
                                      (100000, -0.01), (100000, 1.0)])
def test_invalid_cash_and_fee_are_rejected(cash, fee, client, isolated_out):
    r = client.post("/api/run", json={
        "strategies": [STRAT_A], "data_file": DATA_FILE,
        "cash": cash, "fee": fee,
    })
    assert r.status_code in (400, 422), f"cash={cash} fee={fee} 应被拒"


def test_duplicate_stems_are_rejected(client, isolated_out):
    """两个 stem 相同的策略会让输出文件名互相覆盖(ADR-037)。"""
    r = client.post("/api/run", json={
        "strategies": [STRAT_A, STRAT_A], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    })
    assert r.status_code in (400, 422)


def test_failed_run_leaves_no_archive_directory(client, isolated_out):
    """失败的运行不得留下空归档 —— 它会被 T11 列为无内容的 run(违反 I10)。"""
    client.post("/api/run", json={
        "strategies": ["nope.py"], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    })
    assert not any(isolated_out.iterdir())


# ──────────── ADR-021:假设文案 ────────────


def test_response_carries_the_two_assumption_texts(client, isolated_out):
    """它们是 ADR-001 那条不可验证前提的唯一守卫,不得静默丢失。"""
    p = client.post("/api/run", json={
        "strategies": [STRAT_A], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    }).json()
    ft = C["frozen_text"]
    assert p["assumptions"] == [
        ft["assumption_adjusted"], ft["assumption_market_order"],
    ]


# ──────────── ADR-028:服务端串行化(必须观测到锁本身)────────────


def test_concurrent_runs_are_serialized_by_the_server(
    client, isolated_out, monkeypatch
):
    """对 run 入口打桩记录进入/退出时刻 → 断言两个区间**不重叠**。

    只断言「两个 run_id + 结果完整」不够:ADR-024 的独占 `mkdir` + `-N` 重试
    单独就能满足那三条,会对**完全没有锁**的实现亮绿灯。
    """
    spans: list[tuple[float, float]] = []
    lock = threading.Lock()
    real = api._execute_run_locked

    # 桩打在**被锁保护的那一段**上。打在 `execute_run` 上量到的区间包含
    # **等锁的时间**,两个请求必然「重叠」—— 那会让正确的实现红掉。
    def spy(body):
        t0 = time.perf_counter()
        try:
            return real(body)
        finally:
            t1 = time.perf_counter()
            with lock:
                spans.append((t0, t1))

    monkeypatch.setattr(api, "_execute_run_locked", spy)

    results: list = []

    def fire():
        r = client.post("/api/run", json={
            "strategies": [STRAT_A], "data_file": DATA_FILE,
            "cash": 100000.0, "fee": 0.0,
        })
        results.append(r)

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert len(spans) == 2
    (a0, a1), (b0, b1) = sorted(spans)
    assert a1 <= b0, (
        f"两次 run 的执行区间重叠({a0:.4f}-{a1:.4f} vs {b0:.4f}-{b1:.4f})"
        " —— 服务端没有真的串行化(ADR-028)"
    )

    ids = {r.json()["run_id"] for r in results}
    assert len(ids) == 2, "两次运行必须产生不同的 run_id"
    for r in results:
        assert len(r.json()["results"][0]["equity"]) > 0


# ──────────── 零交易(ADR-023)────────────


def test_zero_trade_strategy_returns_empty_trades_without_error(
    client, isolated_out, tmp_path, monkeypatch
):
    strat = tmp_path / "strategies"
    strat.mkdir()
    (strat / "idle.py").write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "class Idle(Strategy):\n"
        "    def init(self): pass\n"
        "    def next(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api, "STRATEGIES_DIR", strat)

    r = client.post("/api/run", json={
        "strategies": ["idle.py"], "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    })
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["trades"] == []
    assert res["summary"]["trade_count"] == 0
    assert res["summary"]["final_equity"] == 100000.0


# ════════════════════════ T11 · 历史端点 ════════════════════════
#
# **归档 fixture 由 T11 自己在【进程内】调阶段一的 CLI 层产生**
# (`backtest.create_archive_dir` + `backtest.write_run_json`),**不经
# `POST /api/run`** —— 那是 T10 的端点,不在 T11 的依赖里。


def _make_archive(out_dir: Path, strategies: list[str], cash=100000.0, fee=0.0):
    """进程内造一个真实归档,走的是 T8 的产出物。"""
    from nullhypothesis.data import load_csv

    df = load_csv(ROOT / "data" / DATA_FILE)
    paths = [ROOT / "strategies" / s for s in strategies]
    results = nh_run.run(paths, df, cash=cash, fee=fee)
    run_id, d = backtest.create_archive_dir(out_dir)
    request = nh_run.RunRequest(
        strategies=list(strategies), data_file=DATA_FILE, cash=cash, fee=fee
    )
    report = backtest.build_report(run_id, request, results)
    png_paths = plot.write(report, d)
    backtest.write_run_json(report, png_paths, d)
    return run_id, d


def test_collision_archives_are_both_listed_and_readable(
    client, isolated_out, monkeypatch
):
    """`monkeypatch` 把 `_timestamp` 固定为常量使两次**必然同秒** → 断言
    `run_id` 分别为 `<ts>` 与 `<ts>-2`(ADR-024)。

    靠抢时钟来制造碰撞要么 flaky 要么掩盖覆盖 bug。
    """
    monkeypatch.setattr(backtest, "_timestamp", lambda: "20261003-172400")

    id1, _ = _make_archive(isolated_out, [STRAT_A])
    id2, _ = _make_archive(isolated_out, [STRAT_B])
    assert id1 == "20261003-172400"
    assert id2 == "20261003-172400-2"

    listed = client.get("/api/runs").json()
    assert {x["run_id"] for x in listed} == {id1, id2}

    # I10 硬断言:列出的每一项都能被详情端点读取。
    for item in listed:
        r = client.get(f"/api/runs/{item['run_id']}")
        assert r.status_code == 200, r.text


def test_list_reports_strategies_and_data_file_from_run_json(
    client, isolated_out
):
    """它们读自 `run.json`,**无法从文件名反推**(ADR-032/037)。"""
    _make_archive(isolated_out, [STRAT_A, STRAT_B])
    item = client.get("/api/runs").json()[0]
    assert item["strategies"] == [STRAT_A, STRAT_B]
    assert item["data_file"] == DATA_FILE


def test_detail_endpoint_is_recursively_equal_to_the_archived_run_json(
    client, isolated_out
):
    """**跨阶段锁定边界的唯一消费侧门。**

    ADR-037 声明两者「逐字段等于」,但此前只有 T7 断言了生产侧 —— 一个在
    输出时重命名或丢掉 `request`/`assumptions`/`comparison_png_path` 的实现
    能通过 T11 的全部其他条款,而 T14 用 mock 消费它。
    """
    run_id, d = _make_archive(isolated_out, [STRAT_A, STRAT_B])
    on_disk = json.loads(
        (d / C["filenames"]["run_json"]).read_text(encoding="utf-8")
    )
    got = client.get(f"/api/runs/{run_id}").json()

    assert got == on_disk, "详情端点与归档 run.json 不是递归逐字段相等"

    # 递归到每一层的键集合 —— `==` 已覆盖,这里把失败信息做得可读。
    assert set(got) == set(on_disk)
    assert set(got["request"]) == set(on_disk["request"])
    for a, b in zip(got["results"], on_disk["results"]):
        assert set(a) == set(b)
        assert set(a["summary"]) == set(b["summary"])


def test_final_equity_uses_results_zero_consistently(client, isolated_out):
    """多策略时取 `results[0]`,并断言该口径在列表与详情间一致。"""
    run_id, _ = _make_archive(isolated_out, [STRAT_A, STRAT_B])
    item = next(x for x in client.get("/api/runs").json()
                if x["run_id"] == run_id)
    detail = client.get(f"/api/runs/{run_id}").json()
    assert item["final_equity"] == detail["results"][0]["summary"]["final_equity"]


def test_deleted_archive_is_not_listed_and_does_not_raise(
    client, isolated_out
):
    import shutil

    _make_archive(isolated_out, [STRAT_A])
    run_id2, d2 = _make_archive(isolated_out, [STRAT_B])
    shutil.rmtree(d2)

    r = client.get("/api/runs")
    assert r.status_code == 200
    assert run_id2 not in {x["run_id"] for x in r.json()}


def test_archive_without_run_json_is_not_listed(client, isolated_out):
    """模拟**失败的运行**留下的空壳目录 → 不被列出(I10)。"""
    (isolated_out / "20261003-172400").mkdir()
    assert client.get("/api/runs").json() == []


def test_missing_out_dir_returns_empty_list(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "OUT_DIR", tmp_path / "no-such-out")
    r = client.get("/api/runs")
    assert r.status_code == 200 and r.json() == []


def test_history_is_newest_first(client, isolated_out, monkeypatch):
    stamps = iter(["20261003-100000", "20261003-110000", "20261003-120000"])
    monkeypatch.setattr(backtest, "_timestamp", lambda: next(stamps))
    for _ in range(3):
        _make_archive(isolated_out, [STRAT_A])
    ids = [x["run_id"] for x in client.get("/api/runs").json()]
    assert ids == sorted(ids, reverse=True)


# ──────────── `run_id` 的端点级断言(T9 测纯函数,此处测端点真的用了它)────────────


@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "abc", "20261003",
    "C:\\Windows\\Temp", "20261003-172400%00",
])
def test_detail_endpoint_rejects_bad_run_ids(bad, client, isolated_out):
    """各自 400/404 且**未读取任何目录外文件**。

    含 `\x00` 的值**不在此表中**:`httpx` 在构造 URL 时就抛 `InvalidURL`,
    请求根本到不了应用层。该值由上面的纯函数门 `test_validate_run_id_rejects`
    覆盖 —— 那才是它能被观测到的那一层。
    """
    r = client.get(f"/api/runs/{bad}")
    assert r.status_code in (400, 404, 422), f"{bad!r} → {r.status_code}"
    if r.headers.get("content-type", "").startswith("application/json"):
        body = r.text
        assert "root:" not in body, "响应里出现了 /etc/passwd 的内容"


def test_detail_endpoint_reads_collision_dir(client, isolated_out, monkeypatch):
    """对 `20261003-172400-2`(ADR-024 的碰撞目录)→ 断言**能正常读取**。"""
    monkeypatch.setattr(backtest, "_timestamp", lambda: "20261003-172400")
    _make_archive(isolated_out, [STRAT_A])
    id2, _ = _make_archive(isolated_out, [STRAT_B])
    assert id2 == "20261003-172400-2"
    assert client.get(f"/api/runs/{id2}").status_code == 200


def test_unknown_but_wellformed_run_id_is_404(client, isolated_out):
    r = client.get("/api/runs/20991231-235959")
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"

# ════════════════ ADR-045 · 归档 PNG 的专用端点 ════════════════


def test_png_endpoint_serves_the_archived_image(client, isolated_out):
    """ADR-045:`out/` 不是静态目录,图像经专用端点。"""
    run_id, d = _make_archive(isolated_out, [STRAT_A])
    name = f"{Path(STRAT_A).stem}_equity.png"
    assert (d / name).is_file(), "前提:归档里确实有这张图"

    r = client.get(f"/api/runs/{run_id}/png/{name}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    # 真的是 PNG,不是把 JSON 错误体当图片发出去。
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert r.content == (d / name).read_bytes()


def test_png_endpoint_serves_the_comparison_image(client, isolated_out):
    run_id, _ = _make_archive(isolated_out, [STRAT_A, STRAT_B])
    r = client.get(f"/api/runs/{run_id}/png/comparison.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_png_endpoint_name_is_a_whitelist_from_run_json(client, isolated_out):
    """`name` 的白名单来自该归档 `run.json` 声明的图名集合。

    归档里**真实存在**但未被 `run.json` 声明为图像的文件(如 trades.csv、
    summary.json)不得通过这个端点取出 —— 否则它就是个任意文件读取口。
    """
    run_id, d = _make_archive(isolated_out, [STRAT_A])
    # 只断言 `_make_archive` 真的会产生的那些文件 —— 它调 plot.write +
    # write_run_json,但**不**调 report.render,故没有 summary.txt/json。
    leaked = [f"{Path(STRAT_A).stem}_trades.csv", "run.json"]
    for name in leaked:
        assert (d / name).is_file(), f"前提:{name} 确实在归档里"
        r = client.get(f"/api/runs/{run_id}/png/{name}")
        assert r.status_code == 404, f"{name} 被当成图像取出了"


def test_png_endpoint_single_strategy_has_no_comparison(client, isolated_out):
    """单策略归档的 `comparison_png_path` 是 `None` → 不在白名单里。"""
    run_id, _ = _make_archive(isolated_out, [STRAT_A])
    assert client.get(f"/api/runs/{run_id}/png/comparison.png").status_code == 404


@pytest.mark.parametrize("bad_id", [
    "../../etc/passwd", "abc", "20261003", "C:\\Windows\\Temp",
])
def test_png_endpoint_rejects_bad_run_ids(bad_id, client, isolated_out):
    r = client.get(f"/api/runs/{bad_id}/png/x_equity.png")
    assert r.status_code in (400, 404, 422), f"{bad_id!r} → {r.status_code}"


@pytest.mark.parametrize("bad_name", [
    "../run.json", "../../contracts.yaml", "..%2frun.json",
    "/etc/passwd", "....//run.json",
])
def test_png_endpoint_rejects_traversal_in_name(bad_name, client, isolated_out):
    """`name` 也必须是白名单 —— `<stem>` 由用户提供的策略文件名决定。"""
    run_id, _ = _make_archive(isolated_out, [STRAT_A])
    r = client.get(f"/api/runs/{run_id}/png/{bad_name}")
    assert r.status_code in (400, 404, 422), f"{bad_name!r} → {r.status_code}"
    assert b"contracts" not in r.content.lower() or r.status_code != 200


def test_out_dir_is_not_mounted_as_a_static_directory():
    """ADR-045 的**反向**断言:`out/` 不得被挂成静态目录。

    挂了的话归档就成了可枚举的静态资源,而本端点的白名单全被绕过。
    """
    src = (ROOT / "api.py").read_text(encoding="utf-8")
    mounts = [ln for ln in src.splitlines() if "app.mount(" in ln]
    for ln in mounts:
        assert '"out"' not in ln and "'out'" not in ln and "OUT_DIR" not in ln, (
            f"out/ 被挂成静态目录了(违反 ADR-045):{ln.strip()}"
        )


def test_png_endpoint_404_for_unknown_run(client, isolated_out):
    r = client.get("/api/runs/20991231-235959/png/x_equity.png")
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"
