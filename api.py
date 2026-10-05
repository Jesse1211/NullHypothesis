"""阶段二 · FastAPI(T9/T10/T11)。

**`Presentation` BC 不得重新计算任何金融量**(ADR-025):本模块只把阶段一
算好的标量原样传出去。前端把 `916062.0` 显示成 `916,062.00`,不是把
`equity[-1]` 格式化一遍。

路由归属(ADR-035,各任务只追加自己的,不改他人的):
    T9  → GET /api/strategies, /api/data + 两个校验器 + 文件末尾的静态挂载
    T10 → POST /api/run
    T11 → GET /api/runs, /api/runs/{run_id}

**阶段一的产出者按模块属性引用**(`plot.write(...)` 而非
`from ... import write`),否则 `monkeypatch.setattr(plot, "write", ...)`
打不到已绑定的名字 —— T10 的 I9 同源门要打这个桩。
"""

from __future__ import annotations

import json
import re
import threading
import traceback
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import backtest
import nullhypothesis.plot as plot
import nullhypothesis.report as report_mod
import nullhypothesis.run as nh_run
from nullhypothesis.data import load_csv
from nullhypothesis.errors import DataValidationError, StrategyError

__all__ = [
    "app",
    "HOST",
    "PORT",
    "RUN_LOCK",
    "execute_run",
    "validate_run_id",
    "validate_strategy_name",
]

# ADR-034:**只监听回环**。提为模块级常量正是为了让门能断言它 ——
# `TestClient` 不起 uvicorn,绑定地址无从观测。
HOST = "127.0.0.1"
PORT = 8000

ROOT = Path(__file__).resolve().parent
STRATEGIES_DIR = ROOT / "strategies"
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"
FRONTEND_DIST = ROOT / "frontend" / "dist"

# ADR-028:**服务端自己串行化**。按钮禁用只是 UX 提示 —— 按钮不是事务边界,
# 两个标签页、一次 curl、一次刷新都能绕过它。
RUN_LOCK = threading.Lock()

app = FastAPI(title="NullHypothesis", version="0")


@lru_cache(maxsize=1)
def contracts() -> dict[str, Any]:
    import yaml

    return yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))


def _run_json_name() -> str:
    """归档清单的文件名 —— 取自 `contracts.yaml`,不写死。

    原先一处读契约、三处硬编码 `"run.json"`,改契约时那三处会悄悄留在旧名上。
    """
    return str(contracts()["filenames"]["run_json"])


def _read_json(path: Path) -> Any:
    """归档 JSON 一律按 UTF-8 读 —— 策略名可以是中文,系统默认编码不可靠。"""
    return json.loads(path.read_text(encoding="utf-8"))


# ═══════════════════════ 校验器(T9)═══════════════════════
#
# 纯函数 —— T9 的门直接测它们,不依赖 T10/T11 的端点(那是跨任务顺序错误)。

_RUN_ID_RE = re.compile(contracts()["patterns"]["run_id"])


def validate_run_id(s: Any) -> bool:
    """**白名单正则**,不是黑名单。

    黑名单式的 `if '..' in s or '/' in s: reject` 会放过 `%00`、Windows
    绝对路径和任意目录名。反向也要成立:过严的 `^\\d{8}-\\d{6}$` 会让
    ADR-024 的碰撞目录(`-2`/`-3`)在阶段二读不到 —— 那正是 I10 的悬空条目。
    """
    return isinstance(s, str) and bool(_RUN_ID_RE.fullmatch(s))


def _is_bare_filename(s: Any, suffix: str) -> bool:
    """「单层、指定后缀结尾、不含路径分隔符与 `..`」—— **白名单**。

    策略名与数据文件名是同一个形状,只有后缀不同。两份拷贝意味着往其中
    一份加一条禁止字符时另一份会被漏掉,故收成一个。
    """
    if not isinstance(s, str) or not s.endswith(suffix):
        return False
    if "/" in s or "\\" in s or "\x00" in s or ".." in s:
        return False
    return s == Path(s).name and bool(s.strip())


def validate_strategy_name(s: Any) -> bool:
    """I11:**只接受文件名**,绝不接受代码字符串。

    `GET /api/strategies` 返回的就是文件名,故这里只允许「单层、`.py` 结尾、
    不含路径分隔符与 `..`」的名字。
    """
    return _is_bare_filename(s, ".py")


def _validate_data_name(s: Any) -> bool:
    """同上,后缀换成 `.csv`(ADR-027:数据只读自 `data/*.csv`)。"""
    return _is_bare_filename(s, ".csv")


# ═══════════════════════ 错误(ADR-031)═══════════════════════


def _format_cause(e: BaseException) -> str:
    """策略内部那个异常的 traceback。

    格式化的是 `__cause__`、不是 `e` 本身:用户要看的是自己策略里哪一行炸了,
    而 `e` 的栈顶全是框架帧。无 cause(如策略返回了非法值)时为空串。
    """
    cause = e.__cause__
    if cause is None:
        return ""
    return "".join(traceback.format_exception(type(cause), cause, cause.__traceback__))


def _err(code: str, message: str, detail: dict[str, Any] | None = None,
         status: int = 400) -> JSONResponse:
    """结构化 JSON —— 命令行文本直接贴到网页上很难读,结构化后前端能突出
    显示行号/日期(ADR-031)。"""
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "detail": detail or {}},
    )


# ═══════════════════════ T9 · 资源列举 ═══════════════════════


@app.get("/api/strategies")
def list_strategies() -> list[str]:
    """扫描 `strategies/*.py`。目录为空 → `[]`,不报错。"""
    if not STRATEGIES_DIR.is_dir():
        return []
    return sorted(p.name for p in STRATEGIES_DIR.glob("*.py")
                  if not p.name.startswith("_"))


@app.get("/api/data")
def list_data() -> list[str]:
    """扫描 `data/*.csv`(ADR-027)。**不支持上传** —— 明确非目标。"""
    if not DATA_DIR.is_dir():
        return []
    return sorted(p.name for p in DATA_DIR.glob("*.csv"))


# ═══════════════════════ T10 · 跑一次 ═══════════════════════


class RunRequestBody(BaseModel):
    """`POST /api/run` 的请求体(ADR-038 钉死字段名)。

    `strategies` 是**文件名**列表,不是代码(I11)。
    """

    strategies: list[str] = Field(min_length=1)
    data_file: str
    cash: float = 100000.0
    fee: float = 0.0


def execute_run(body: RunRequestBody) -> dict[str, Any]:
    """唯一的 run 入口。持 `RUN_LOCK` 全程 —— 两个并发请求的**临界区**不重叠。

    锁在这里、而真正的工作在 `_execute_run_locked` 里,是为了让 T10 的串行化
    门能打桩**观测到锁本身**:对 `execute_run` 打桩只能量到「进入函数→退出」,
    那个区间**包含等锁的时间**,于是两个请求必然「重叠」,门会对**正确**的
    实现亮红灯。桩打在被锁保护的那一段上,量到的才是临界区。
    """
    with RUN_LOCK:
        return _execute_run_locked(body)


def _execute_run_locked(body: RunRequestBody) -> dict[str, Any]:
    """**调用方必须已持有 `RUN_LOCK`。** T10 的门对本函数打桩。"""
    df = load_csv(DATA_DIR / body.data_file)
    paths = [STRATEGIES_DIR / s for s in body.strategies]
    results = nh_run.run(paths, df, cash=body.cash, fee=body.fee)

    run_id, out_dir = backtest.create_archive_dir(OUT_DIR)
    request = nh_run.RunRequest(
        strategies=list(body.strategies), data_file=body.data_file,
        cash=body.cash, fee=body.fee,
    )
    report = backtest.build_report(run_id, request, results)

    # 按模块属性调用 —— T10 的 I9 门要 monkeypatch 这两个。
    png_paths = plot.write(report, out_dir)
    report_mod.render(report, out_dir)
    backtest.write_run_json(report, png_paths, out_dir)

    return _run_payload(report, png_paths)


def _run_payload(report, png_paths: dict[str, Any]) -> dict[str, Any]:
    """ADR-038 的响应体。**所有金融量都是已完成全部算术的标量。**"""
    return {
        "run_id": report.run_id,
        "results": [
            {
                "strategy": r.strategy,
                # ADR-038 的 equity[] 投影 date / equity / close 三个键
                # (`close` 由 ADR-049 补入,供前端画价格对照曲线)——
                # cash/shares 是内部对账所需,不出 API。
                "equity": [
                    {"date": p.date, "equity": p.equity, "close": p.close}
                    for p in r.equity
                ],
                "trades": [
                    {
                        "date": f.date, "side": f.side, "shares": f.shares,
                        "price": f.price, "amount": f.amount, "fee": f.fee,
                        "cash_after": ca, "shares_after": sa,
                    }
                    for f, (ca, sa) in zip(r.trades, r.trades_ledger)
                ],
                "summary": report_mod._summary_json(r.summary),
                "png_path": png_paths.get(r.strategy),
            }
            for r in report.results
        ],
        "comparison_png_path": png_paths.get("comparison"),
        "assumptions": list(report.assumptions),
    }


def _reject_bad_names(body: RunRequestBody) -> JSONResponse | None:
    """I11:策略只能按文件名引用。代码串在这里就被挡下,**绝不**到达 exec 路径。

    必须**先于**存在性检查:`is_file()` 会拿着攻击者给的字符串去拼路径,
    而这一步正是把那个字符串限制成单层文件名的那道闸。
    """
    for s in body.strategies:
        if not validate_strategy_name(s):
            return _err("INVALID_REQUEST", f"策略名非法:{s!r}",
                        {"field": "strategies", "value": s}, 422)
    if not _validate_data_name(body.data_file):
        return _err("INVALID_REQUEST", f"数据文件名非法:{body.data_file!r}",
                    {"field": "data_file", "value": body.data_file}, 422)
    return None


def _reject_missing_files(body: RunRequestBody) -> JSONResponse | None:
    """名字合法但文件不在 → 404(不是 422:请求本身没问题)。"""
    for s in body.strategies:
        if not (STRATEGIES_DIR / s).is_file():
            return _err("NOT_FOUND", f"策略不存在:{s}", {"strategy": s}, 404)
    if not (DATA_DIR / body.data_file).is_file():
        return _err("NOT_FOUND", f"数据文件不存在:{body.data_file}",
                    {"data_file": body.data_file}, 404)
    return None


def _reject_bad_values(body: RunRequestBody) -> JSONResponse | None:
    """取值约束 —— 都是 `INVALID_REQUEST`/422,故列成表而非 if 链。

    每条仍带**各自**的 message 与 detail:错误体是给人读的,合并成一句
    「参数不合法」会把「哪个参数、为什么」丢掉(ADR-031)。
    """
    stems = [Path(s).stem for s in body.strategies]
    checks: list[tuple[bool, str, dict[str, Any]]] = [
        (
            len(set(stems)) != len(stems),
            "策略 stem 重复 —— 输出文件名会互相覆盖",
            {"strategies": body.strategies},
        ),
        (
            body.cash <= 0,
            f"cash 必须 > 0,收到 {body.cash}",
            {"field": "cash", "value": body.cash},
        ),
        (
            not (0.0 <= body.fee < 0.1),
            f"fee 必须在 [0, 0.1) 内,收到 {body.fee}",
            {"field": "fee", "value": body.fee},
        ),
    ]
    for failed, message, detail in checks:
        if failed:
            return _err("INVALID_REQUEST", message, detail, 422)
    return None


@app.post("/api/run")
def post_run(body: RunRequestBody):
    # 顺序即安全边界:白名单 → 存在性 → 取值(见各函数的 docstring)。
    for reject in (_reject_bad_names, _reject_missing_files, _reject_bad_values):
        error = reject(body)
        if error is not None:
            return error

    try:
        return execute_run(body)
    except DataValidationError as e:
        return _err("DATA_VALIDATION", str(e), {
            "file": getattr(e, "file", None),
            "row": getattr(e, "row", None),
            "column": getattr(e, "column", None),
        })
    except StrategyError as e:
        return _err("STRATEGY_ERROR", str(e), {
            "strategy": getattr(e, "strategy", None),
            "bar_index": getattr(e, "bar_index", None),
            "date": getattr(e, "date", None),
            "traceback": _format_cause(e),
        })


# ═══════════════════════ T11 · 历史 ═══════════════════════


def _archives() -> list[Path]:
    """**扫描即事实** —— 没有第二份存储,故 I10 天然成立。

    缺 `run.json` 的目录(失败运行留下的空壳)**不被列出**。
    """
    if not OUT_DIR.is_dir():
        return []
    manifest = _run_json_name()
    return sorted(
        (d for d in OUT_DIR.iterdir()
         if d.is_dir() and validate_run_id(d.name) and (d / manifest).is_file()),
        key=lambda d: d.name, reverse=True,          # 最新优先
    )


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    """列表口径由 ADR-038 钉死 —— 四个字段,不多不少。"""
    items = []
    for d in _archives():
        archived = _read_json(d / _run_json_name())
        items.append({
            "run_id": archived["run_id"],
            "strategies": archived["request"]["strategies"],
            "data_file": archived["request"]["data_file"],
            # 多策略时取 results[0](即 --strategy 的第一条),ADR-038。
            "final_equity": archived["results"][0]["summary"]["final_equity"],
        })
    return items


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    """**逐字段等于**该归档的 `run.json`(ADR-037)—— 直接返回文件内容。

    任何转换都会让这条跨阶段锁定边界的契约出现第二个真相来源。
    """
    if not validate_run_id(run_id):
        return _err("INVALID_REQUEST", f"run_id 非法:{run_id!r}",
                    {"run_id": run_id}, 422)
    path = OUT_DIR / run_id / _run_json_name()
    if not path.is_file():
        return _err("NOT_FOUND", f"找不到归档:{run_id}", {"run_id": run_id}, 404)
    return _read_json(path)


def _declared_png_names(archived: dict[str, Any]) -> set[str]:
    """该归档 `run.json` **声明过**的图名(只取文件名,丢掉目录部分)。

    这就是 `name` 的白名单本体 —— 归档里躺着的可枚举事实,见 `get_run_png`
    的 docstring 为何不用正则。
    """
    declared = [r.get("png_path") for r in archived.get("results", [])]
    declared.append(archived.get("comparison_png_path"))
    return {Path(p).name for p in declared if p}


@app.get("/api/runs/{run_id}/png/{name}")
def get_run_png(run_id: str, name: str):
    r"""ADR-045:归档 PNG 经**专用端点**服务,`out/` 不挂成静态目录。

    把 `out/` 整个挂出去等于把全部历史归档变成可枚举的静态资源。这里只
    暴露「这个 run 的这张图」,且两段路径都走**白名单**:

      * `run_id` → `validate_run_id`,与 T11 的详情端点同一套正则
      * `name`   → **必须出现在该归档 `run.json` 声明的图名集合里**

    `name` 的白名单来自 `run.json` 而不是正则:`<stem>` 由用户提供的策略
    文件名决定,拿 `{stem}_equity.png` 反推成正则等于把 `.*_equity\.png`
    放进来 —— 而真正可枚举的事实就在归档里躺着。
    """
    if not validate_run_id(run_id):
        return _err("INVALID_REQUEST", f"run_id 非法:{run_id!r}",
                    {"run_id": run_id}, 422)

    meta = OUT_DIR / run_id / _run_json_name()
    if not meta.is_file():
        return _err("NOT_FOUND", f"找不到归档:{run_id}", {"run_id": run_id}, 404)

    allowed = _declared_png_names(_read_json(meta))
    if name not in allowed:
        # 不回显 `name` 的内容,只说它不在白名单里。
        return _err("NOT_FOUND", f"该归档没有这张图:{name}",
                    {"run_id": run_id, "allowed": sorted(allowed)}, 404)

    png = OUT_DIR / run_id / name
    if not png.is_file():
        return _err("NOT_FOUND", f"图像文件缺失:{name}",
                    {"run_id": run_id, "name": name}, 404)
    return FileResponse(png, media_type="image/png")


# ═══════════════════════ 静态挂载(T9,必须最后)═══════════════════════
#
# 挂在 "/" 会遮蔽**之后**注册的路由,故必须是文件最后一条注册。
# T9 先于 T12,那时 `frontend/dist` 还不存在 —— 故必须有 is_dir() 守卫,
# 否则 `StaticFiles(directory=...)` 在目录缺失时直接抛错,T9 根本起不来。
if FRONTEND_DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="spa")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
