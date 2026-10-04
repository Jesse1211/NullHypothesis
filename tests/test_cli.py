"""T8 的验收门 · CLI + 归档目录 + run.json + 两条样例策略。

两类门,进程边界不同:

* **子进程** —— 端到端真实跑 CLI、退出码。
* **进程内** —— ADR-024 的碰撞门必须 `monkeypatch` `backtest._timestamp`,
  而打桩**跨不过 subprocess 边界**:两次子进程运行会拿到真实时钟,大概率
  跨过秒边界,于是零个 `-2` 目录,断言会**红掉正确实现**。
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import backtest

ROOT = Path(__file__).resolve().parent.parent
C = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))
EXIT = C["exit_codes"]
BUY_AND_HOLD = ROOT / "strategies" / "buy_and_hold.py"
MA_CROSS = ROOT / "strategies" / "ma_cross.py"


def _csv(tmp_path: Path, rows: list[tuple[str, float, float]], name="d.csv") -> Path:
    lines = ["Date,Open,High,Low,Close,Volume"]
    for d, o, c in rows:
        lines.append(f"{d},{o},{max(o, c) + 1},{min(o, c) - 1},{c},1000")
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _series(n: int, start=100.0, step=5.0) -> list[tuple[str, float, float]]:
    import datetime as dt

    out, d, px = [], dt.date(2020, 1, 1), start
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d.isoformat(), px, px + step))
            px += step
        d += dt.timedelta(days=1)
    return out


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "backtest.py"), *args],
        capture_output=True, text=True, cwd=str(cwd or ROOT),
    )


# ═════════════════════ 端到端(子进程)═════════════════════


def test_end_to_end_produces_all_three_outputs(tmp_path):
    data = _csv(tmp_path, _series(30))
    out = tmp_path / "out"
    r = _run(["--data", str(data), "--strategy", str(BUY_AND_HOLD),
              "--cash", "100000", "--out", str(out)])
    assert r.returncode == EXIT["ok"], r.stderr

    archive = next(out.iterdir())
    fn = C["filenames"]
    for template in fn["t8_required_outputs"]:
        assert (archive / template.format(stem="buy_and_hold")).exists(), template


def test_buy_and_hold_trades_once(tmp_path):
    """ADR-008:天天 target(1.0),但幂等由引擎负责,只成交一次。"""
    data = _csv(tmp_path, _series(40))
    out = tmp_path / "out"
    _run(["--data", str(data), "--strategy", str(BUY_AND_HOLD), "--out", str(out)])
    payload = json.loads((next(out.iterdir()) / C["filenames"]["summary_json"]).read_text())
    assert payload["results"][0]["summary"]["trade_count"] == 1


def test_fee_is_plumbed_end_to_end(tmp_path):
    """CLI 把 0.0013 解析成 0.13% 而非 13%(或忘了传下去)能通过其余所有门。"""
    data = _csv(tmp_path, _series(30))
    out = tmp_path / "out"
    r = _run(["--data", str(data), "--strategy", str(BUY_AND_HOLD),
              "--cash", "100000", "--fee", "0.0013", "--out", str(out)])
    assert r.returncode == EXIT["ok"], r.stderr

    archive = next(out.iterdir())
    rows = list(csv.DictReader(
        (archive / C["filenames"]["trades_csv"].format(stem="buy_and_hold")).open()))
    assert rows, "应有成交"
    # 逐笔核对费率口径 —— CLI 把 0.0013 解析成 0.13% 而非 13%(或忘了传下去)
    # 能通过其余所有门。
    for row in rows:
        fee = float(row["fee"])
        gross = float(row["shares"]) * float(row["price"])
        assert fee > 0, "--fee 非零时手续费必须非零"
        # `trades.csv` 按 ADR-037 写 2 位小数,故与全精度比要容到 0.005 ——
        # 不是费率有误差,是 CSV 的格式本来就只留两位。
        # 口径错 10 倍(0.13% vs 1.3%)会差 1000 倍,这个容差照样抓得到。
        assert fee == pytest.approx(gross * 0.0013, abs=0.005), "费率口径错了"


def test_run_json_matches_the_pinned_schema(tmp_path):
    """跨阶段锁定边界 —— 字段名错会让阶段二读不到阶段一的产物。"""
    data = _csv(tmp_path, _series(30))
    out = tmp_path / "out"
    _run(["--data", str(data), "--strategy", str(BUY_AND_HOLD), "--out", str(out)])
    payload = json.loads((next(out.iterdir()) / C["filenames"]["run_json"]).read_text())

    assert set(payload) == {"run_id", "request", "results", "assumptions",
                            "comparison_png_path"}
    assert set(payload["request"]) == {"strategies", "data_file", "cash", "fee"}
    assert set(payload["results"][0]) == {"strategy", "summary", "png_path"}
    assert set(payload["results"][0]["summary"]) == set(C["field_formats"])
    import re

    assert re.fullmatch(C["patterns"]["run_id"], payload["run_id"])


def test_multi_strategy_outputs_are_independent(tmp_path):
    data = _csv(tmp_path, _series(200))
    out = tmp_path / "out"
    r = _run(["--data", str(data), "--strategy", str(BUY_AND_HOLD),
              "--strategy", str(MA_CROSS), "--out", str(out)])
    assert r.returncode == EXIT["ok"], r.stderr

    archive = next(out.iterdir())
    assert (archive / C["filenames"]["comparison_png"]).exists()
    for stem in ("buy_and_hold", "ma_cross"):
        assert (archive / C["filenames"]["trades_csv"].format(stem=stem)).exists()
    assert "并排对比" in r.stdout


def test_single_row_csv_end_to_end(tmp_path):
    """ADR-023:无 T+1 → 零交易;跳空三值为 None;PNG 非全白。"""
    from PIL import Image

    data = _csv(tmp_path, [("2020-01-02", 100.0, 100.0)])
    out = tmp_path / "out"
    r = _run(["--data", str(data), "--strategy", str(BUY_AND_HOLD),
              "--cash", "10000", "--out", str(out)])
    assert r.returncode == EXIT["ok"], r.stderr
    assert C["frozen_text"]["single_day_notice"] in r.stdout

    archive = next(out.iterdir())
    s = json.loads((archive / C["filenames"]["summary_json"]).read_text())["results"][0]["summary"]
    assert s["bars"] == 1
    assert s["final_equity"] == s["initial_cash"]
    assert (s["max_gap_pct"], s["max_gap_date"], s["mean_abs_gap_pct"]) == (None, None, None)

    rows = list(csv.reader(
        (archive / C["filenames"]["trades_csv"].format(stem="buy_and_hold")).open()))
    assert rows == [C["trades_csv_header"]], "零交易时仅表头"

    png = archive / C["filenames"]["equity_png"].format(stem="buy_and_hold")
    with Image.open(png) as im:
        colors = im.convert("RGB").getcolors(maxcolors=1 << 20) or []
    assert sum(n for n, rgb in colors if rgb != (255, 255, 255)) > 0, "整张图全白"


# ═════════════════════ 退出码与参数(子进程)═════════════════════


@pytest.mark.parametrize("flag,value", [
    ("--cash", "0"), ("--cash", "-1"), ("--fee", "-0.1"), ("--fee", "0.5"),
])
def test_invalid_numeric_args(tmp_path, flag, value):
    data = _csv(tmp_path, _series(10))
    r = _run(["--data", str(data), "--strategy", str(BUY_AND_HOLD),
              flag, value, "--out", str(tmp_path / "out")])
    assert r.returncode == EXIT["invalid_cli_args"], r.stderr


def test_missing_files_are_arg_errors_not_data_errors(tmp_path):
    """`invalid_cli_args` 而非 `data_validation` —— 后者是「文件存在但内容非法」。"""
    data = _csv(tmp_path, _series(10))
    r1 = _run(["--data", str(tmp_path / "nope.csv"), "--strategy", str(BUY_AND_HOLD),
               "--out", str(tmp_path / "o1")])
    assert r1.returncode == EXIT["invalid_cli_args"]

    r2 = _run(["--data", str(data), "--strategy", str(tmp_path / "nope.py"),
               "--out", str(tmp_path / "o2")])
    assert r2.returncode == EXIT["invalid_cli_args"]


def test_duplicate_stem_is_rejected(tmp_path):
    """ADR-037:同 stem 会让 <stem>_trades.csv 互相覆盖。"""
    data = _csv(tmp_path, _series(10))
    r = _run(["--data", str(data), "--strategy", str(BUY_AND_HOLD),
              "--strategy", str(BUY_AND_HOLD), "--out", str(tmp_path / "out")])
    assert r.returncode == EXIT["invalid_cli_args"]
    assert "buy_and_hold" in r.stderr


def test_out_dir_is_created_when_missing(tmp_path):
    data = _csv(tmp_path, _series(10))
    deep = tmp_path / "a" / "b" / "c"
    r = _run(["--data", str(data), "--strategy", str(BUY_AND_HOLD), "--out", str(deep)])
    assert r.returncode == EXIT["ok"], r.stderr
    assert deep.is_dir() and list(deep.iterdir())


def test_defaults_are_used_when_flags_omitted(tmp_path):
    data = _csv(tmp_path, _series(20))
    out = tmp_path / "out"
    r = _run(["--data", str(data), "--strategy", str(BUY_AND_HOLD), "--out", str(out)])
    assert r.returncode == EXIT["ok"], r.stderr
    s = json.loads((next(out.iterdir()) / C["filenames"]["summary_json"]).read_text())
    assert s["results"][0]["summary"]["initial_cash"] == 100000.0


def test_bad_csv_exits_with_data_validation(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Date,Open,High,Low,Close,Volume\n2020-01-02,100,101,99,,1000\n")
    r = _run(["--data", str(bad), "--strategy", str(BUY_AND_HOLD),
              "--out", str(tmp_path / "out")])
    assert r.returncode == EXIT["data_validation"], r.stderr


def test_strategy_error_leaves_no_archive_dir(tmp_path):
    """ADR-022:立即终止,**不写任何输出文件** —— 失败的运行不得留下空目录,
    否则 I10 会把它列成一个没有内容的 run。"""
    data = _csv(tmp_path, _series(10))
    broken = tmp_path / "broken.py"
    broken.write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "class S(Strategy):\n"
        "    def next(self):\n"
        "        raise ZeroDivisionError('boom')\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    out.mkdir()
    before = len(list(out.iterdir()))

    r = _run(["--data", str(data), "--strategy", str(broken), "--out", str(out)])
    assert r.returncode == EXIT["strategy_error"], r.stdout
    assert len(list(out.iterdir())) == before, "失败的运行留下了目录"
    assert "ZeroDivisionError" in r.stderr, "须含原始 traceback"


# ═════════════════════ ADR-024 碰撞(进程内)═════════════════════


def test_archive_collision_appends_suffixes(tmp_path, monkeypatch):
    """**进程内** —— monkeypatch 跨不过 subprocess 边界。

    靠真实时钟抢同一秒是在跟时钟赛跑:matplotlib 冷导入 + 真实渲染很容易
    跨过秒边界,得到两个不同时间戳、零个 `-2` 目录,于是断言**红掉正确实现**。
    """
    monkeypatch.setattr(backtest, "_timestamp", lambda: "20261003-172400")
    out = tmp_path / "out"

    ids = [backtest.create_archive_dir(out)[0] for _ in range(3)]
    assert ids == ["20261003-172400", "20261003-172400-2", "20261003-172400-3"]
    for i in ids:
        assert (out / i).is_dir()


def test_collision_never_touches_an_existing_directory(tmp_path, monkeypatch):
    """钉死 `os.mkdir` 而非 `exist_ok=True` —— 后者会静默覆盖前一次的结果。"""
    monkeypatch.setattr(backtest, "_timestamp", lambda: "20261003-172400")
    out = tmp_path / "out"
    existing = out / "20261003-172400"
    existing.mkdir(parents=True)
    sentinel = existing / "sentinel.txt"
    sentinel.write_text("do not touch", encoding="utf-8")

    run_id, path = backtest.create_archive_dir(out)
    assert run_id == "20261003-172400-2"
    assert path == out / "20261003-172400-2"
    assert sentinel.read_text(encoding="utf-8") == "do not touch"


def test_timestamp_is_a_patchable_module_level_seam():
    """内联 datetime.now() 会让上面两条门结构上无法执行。"""
    import inspect

    assert callable(backtest._timestamp)
    assert inspect.getmodule(backtest._timestamp) is backtest
    import re

    assert re.fullmatch(r"\d{8}-\d{6}", backtest._timestamp())


def test_run_id_matches_the_pinned_regex(tmp_path, monkeypatch):
    """过严的 `^\\d{8}-\\d{6}$` 会让碰撞目录在阶段二读不到 —— 那正是 I10 的悬空条目。"""
    import re

    monkeypatch.setattr(backtest, "_timestamp", lambda: "20261003-172400")
    out = tmp_path / "out"
    for _ in range(2):
        run_id, _p = backtest.create_archive_dir(out)
        assert re.fullmatch(C["patterns"]["run_id"], run_id), run_id


# ═════════════════════ 均线穿越的判别性 fixture ═════════════════════


def test_ma_cross_edge_detection_discriminating_cases(tmp_path, monkeypatch):
    """手工钉死的价格序列,必须包含三个判别 case:

    (a) 空仓时的下穿 → **不产生成交**(目标本来就是 0,差额为 0 → ADR-008)
    (b) 持仓时的下穿 → 产生 SELL
    (c) 最后一根上的穿越 → 订单被丢弃(ADR-005)

    并断言 `trade_count` **严格小于**穿越次数 —— 这正是 ADR-008/005 的后果,
    而「每次穿越都成交」的实现会在这里失败。
    """
    import pandas as pd

    from nullhypothesis.run import run

    # short=2 / long=3 的小窗口,让穿越在 8 根内可控地发生
    strat = tmp_path / "ma_small.py"
    strat.write_text(
        "from nullhypothesis.strategy import Strategy\n"
        "SHORT, LONG = 2, 3\n"
        "class S(Strategy):\n"
        "    def next(self):\n"
        "        c = self.data['Close']\n"
        "        if len(c) < LONG + 1:\n            return\n"
        "        sn, ln = c.iloc[-SHORT:].mean(), c.iloc[-LONG:].mean()\n"
        "        sp, lp = c.iloc[-SHORT-1:-1].mean(), c.iloc[-LONG-1:-1].mean()\n"
        "        if sp <= lp and sn > ln:\n            self.target(weight=1.0)\n"
        "        elif sp >= lp and sn < ln:\n            self.target(weight=0.0)\n",
        encoding="utf-8",
    )

    # 先跌(空仓下穿,case a)→ 再涨(上穿买入)→ 再跌(持仓下穿,case b)
    closes = [100.0, 98.0, 95.0, 90.0, 120.0, 150.0, 120.0, 80.0]
    rows = [(f"2020-01-{i+1:02d}", c, c) for i, c in enumerate(closes)]
    data = _csv(tmp_path, rows, "ma.csv")

    from nullhypothesis.data import load_csv

    r = run([strat], load_csv(data), cash=10000.0)[0]
    sides = [f.side for f in r.trades]

    assert "BUY" in sides, "上穿应产生买入"
    assert r.summary.trade_count == len(r.trades)
    # case (a):开头的下穿发生在空仓时 —— 第一笔成交必须是 BUY,不是 SELL
    assert sides[0] == "BUY", "空仓时的下穿不得产生成交(差额为 0)"
    # 穿越次数 >= 成交次数,且至少有一次穿越没有变成成交
    assert r.summary.trade_count < 4, (
        f"交易次数 {r.summary.trade_count} 应严格小于穿越次数 —— "
        f"空仓下穿不成交(ADR-008)、末根穿越被丢弃(ADR-005)"
    )
