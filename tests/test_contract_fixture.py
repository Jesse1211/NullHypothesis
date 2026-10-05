"""阶段二 · 契约门(T10 与 T13 之间,DESIGN.md §5)。

前端 mock 必须由**真实 API 响应生成**,否则后端改字段名时前端静默显示
`undefined`,而 `tsc` / `vitest` / `npm run build` **全绿**。

两件事:
  1. 把一份真实响应落盘到 `frontend/src/__fixtures__/run_response.json`,
     **该文件必须 commit 进 git** —— T13 的前端门要在**不先跑 pytest** 的
     干净 checkout 上通过。(否则 `npm test` 在干净检出上 ENOENT;更坏的是
     T13 的 agent 发现文件不存在就**手写**一份 —— 正是此门要防的事。)
  2. 比较已提交的 fixture 与**重新生成**的那份,**递归**到所有嵌套对象。

**比较必须递归且不可自我满足**:只比顶层键集合会放过
`summary.final_equity` → `summary.finalEquity` 这类重命名 —— 而那正是
ADR-038 钉死、ADR-025 禁止前端自救的那一层。而「先生成再和自己比」永远绿,
契约从未被检查 —— 故这里比的是**磁盘上已提交的那份**与新生成的那份。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api

ROOT = Path(__file__).resolve().parent.parent

# T10 与 T12 是 DAG 兄弟(都只依赖 T9),T10 可能先跑,那时 `frontend/`
# 还不存在。故本门用 `mkdir(parents=True)` 创建该路径 —— **这是 T10 唯一
# 允许写到后端目录之外的地方**。
FIXTURE = ROOT / "frontend" / "src" / "__fixtures__" / "run_response.json"

DATA_FILE = "synthetic.csv"
STRATEGIES = ["buy_and_hold.py", "ma_cross.py"]


def _generate() -> dict:
    """跑一次真实的 `POST /api/run`,返回响应体。

    用**两条**策略 —— 单策略的响应里 `comparison_png_path` 为 `null`,
    T13 的对比表就没有可用的基底。
    """
    client = TestClient(api.app)
    r = client.post("/api/run", json={
        "strategies": STRATEGIES, "data_file": DATA_FILE,
        "cash": 100000.0, "fee": 0.0,
    })
    assert r.status_code == 200, r.text
    return r.json()


def _shape(obj, path="$"):
    """递归提取**键集合与类型**,忽略具体数值。

    数值每次跑都一样(同一份 CSV + 同一批策略),但归档路径里含 `run_id`,
    每次必然不同 —— 所以契约比的是**形状**,不是逐值相等。
    """
    out = {}
    if isinstance(obj, dict):
        out[path] = ("dict", tuple(sorted(obj)))
        for k, v in obj.items():
            out.update(_shape(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        out[path] = ("list",)
        # 只递归第 0 项 —— 同构数组,逐项递归只会放大噪声。
        if obj:
            out.update(_shape(obj[0], f"{path}[0]"))
    else:
        out[path] = (type(obj).__name__,)
    return out


def test_fixture_is_committed_and_nonempty():
    """T13 的前端门要在干净 checkout 上通过 —— 文件必须在 git 里。"""
    assert FIXTURE.is_file(), (
        f"契约 fixture 不存在:{FIXTURE.relative_to(ROOT)} —— "
        "跑 `pytest tests/test_contract_fixture.py::test_regenerate_fixture` 生成它"
    )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(FIXTURE.relative_to(ROOT))],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert tracked.returncode == 0, (
        "契约 fixture 未被 git 跟踪 —— T13 的前端门会在干净 checkout 上 ENOENT"
    )


def test_regenerate_fixture(monkeypatch):
    """生成到**另一个路径**,再与已提交的那份比形状。

    归档落在**真实 `out/`**,不能用 `tmp_path`:`png_path` 按 ADR-038 是
    仓库相对路径,而 `tmp_path` 在仓库外会被如实记成绝对路径,commit 进去
    就成了别人机器上取不到的 `<img src>`。生成的归档**保留**在 `out/` 里
    (它和任何一次正常运行的归档没有区别),这样 fixture 里的 `png_path`
    在本机确实指向一个存在的文件。

    注意契约比的是**形状**(`_shape` 丢掉所有值),所以 `run_id` 与路径
    每次不同不会让门变红;T13 也只 mock `fetch`,不真去取那个 PNG。
    """
    fresh = _generate()

    if not FIXTURE.is_file():
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(
            json.dumps(fresh, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        pytest.fail(
            f"契约 fixture 不存在,已生成到 {FIXTURE.relative_to(ROOT)} —— "
            "请 commit 它后重跑"
        )

    committed = json.loads(FIXTURE.read_text(encoding="utf-8"))

    a, b = _shape(committed), _shape(fresh)
    missing = {k: a[k] for k in a.keys() - b.keys()}
    extra = {k: b[k] for k in b.keys() - a.keys()}
    changed = {k: (a[k], b[k]) for k in a.keys() & b.keys() if a[k] != b[k]}

    assert not (missing or extra or changed), (
        "API 响应的形状与已提交的契约 fixture 不符 —— 前端会渲染 undefined。\n"
        f"  fixture 有而 API 没有:{missing}\n"
        f"  API 有而 fixture 没有:{extra}\n"
        f"  类型/键集合变了:{changed}\n"
        f"确认是有意的字段变更后,重新生成 {FIXTURE.relative_to(ROOT)} 并 commit。"
    )


def test_fixture_was_not_rewritten_by_this_run():
    """`git diff --exit-code` —— 断言跑门的过程没有改写已提交的 fixture。

    「先生成再和自己比」永远绿;这条确保上面那条比的真的是磁盘上的旧版本。
    """
    r = subprocess.run(
        ["git", "diff", "--exit-code", "--", str(FIXTURE.relative_to(ROOT))],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, (
        f"契约 fixture 在跑门的过程中被改写了:\n{r.stdout}"
    )


def test_fixture_carries_the_fields_t13_consumes():
    """正向钉死 T13 真正要读的那些键 —— 形状比较只保证两边一致,
    不保证**内容够用**(两边同时缺 `summary` 也能通过形状比较)。"""
    f = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(f) == {"run_id", "results", "comparison_png_path", "assumptions"}
    assert len(f["results"]) == 2, "契约 fixture 必须是多策略响应(T13 要画对比表)"
    for res in f["results"]:
        assert set(res) == {"strategy", "equity", "trades", "summary", "png_path"}
        # `close` 由 ADR-049 补入 —— T13 用它画价格对照曲线
        assert res["equity"]
        assert set(res["equity"][0]) == {"date", "equity", "close"}
    assert f["comparison_png_path"], "多策略响应必须有 comparison_png_path"
    assert len(f["assumptions"]) == 2
