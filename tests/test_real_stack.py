"""阶段二 · 真实栈门(DESIGN.md §5)。

> 原设计声明「无 `REAL_STACK_GATE`:本项目无 DB、无外部服务」。**该判断对
> 阶段一成立,对阶段二不成立** —— 阶段二的真实基质不是数据库,而是
> **文件系统持久层 + ASGI/HTTP 边界**,而 `TestClient` 两者都绕过。

本门真起一个 `uvicorn` 子进程。它覆盖三件 `TestClient` 结构上看不见的事:
  1. **实际绑定地址**(ADR-034)—— 从启动日志读,不靠 `lsof`
  2. **静态挂载是否遮蔽 API 路由**(ADR-033)—— 挂载顺序错误只在真实
     服务器 + 真实 `dist/` 下暴露
  3. ASGI 边界本身(真实 HTTP 请求/响应,不是直接调 ASGI app)

**不要用 `lsof`** —— 实测它存在于 `/usr/sbin/lsof` 但**不在允许列表中**,
无人值守构建会停在权限弹窗上。`psutil` 同理且未安装。解析启动日志不需要
任何额外权限。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

import api

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765          # 不用 8000,避免撞上开发者自己起的服务
BOOT_TIMEOUT = 30.0


@pytest.fixture(scope="module")
def live_server():
    """真起 `uvicorn`,把启动日志收集起来给绑定地址门用。"""
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api:app",
         "--host", api.HOST, "--port", str(PORT), "--log-level", "info"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    log: list[str] = []
    base = f"http://{api.HOST}:{PORT}"

    deadline = time.time() + BOOT_TIMEOUT
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            log.extend((proc.stdout.read() or "").splitlines())
            pytest.fail("uvicorn 退出了:\n" + "\n".join(log))
        try:
            httpx.get(f"{base}/api/strategies", timeout=1.0)
            ready = True
            break
        except httpx.TransportError:
            time.sleep(0.2)

    # 把到目前为止的启动日志读出来(非阻塞:uvicorn 启动后不再写,
    # 所以用一个独立线程读,避免 read() 挂死在还开着的管道上)。
    import threading

    def drain():
        assert proc.stdout is not None
        for line in proc.stdout:
            log.append(line.rstrip("\n"))

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    time.sleep(0.5)

    if not ready:
        proc.terminate()
        pytest.fail(f"{BOOT_TIMEOUT}s 内起不来:\n" + "\n".join(log))

    yield base, log

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_server_is_reachable_on_loopback(live_server):
    base, _ = live_server
    r = httpx.get(f"{base}/api/strategies", timeout=5.0)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_startup_log_shows_loopback_bind_and_never_0_0_0_0(live_server):
    """**主断言(离线确定、零额外权限)**:解析 uvicorn 的启动日志。

    断言出现 `Uvicorn running on http://127.0.0.1:<port>`,**且不出现
    `0.0.0.0`**。
    """
    _, log = live_server
    text = "\n".join(log)
    assert f"Uvicorn running on http://{api.HOST}:{PORT}" in text, (
        f"启动日志里没有回环绑定行:\n{text}"
    )
    assert "0.0.0.0" not in text, f"uvicorn 绑到了 0.0.0.0(违反 ADR-034):\n{text}"
    assert api.HOST == "127.0.0.1"


def test_not_reachable_from_lan(live_server):
    """**仅作可跳过的补充** —— 取不到非回环 IPv4 时 `skip` 并记入 blindSpots。

    连接须 `settimeout(2)`,断言「**未成功**」(拒绝或超时均算通过):连不
    可路由的 LAN IP 通常是**超时**而非拒绝。
    """
    lan_ip = None
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                lan_ip = ip
                break
    except socket.gaierror:
        pass

    if lan_ip is None:
        pytest.skip("取不到非回环 IPv4 —— 本门跳过,已记入 blindSpots")

    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((lan_ip, PORT))
    except (ConnectionRefusedError, socket.timeout, OSError):
        return                      # 未成功 → 通过
    finally:
        s.close()
    pytest.fail(f"从 LAN 地址 {lan_ip}:{PORT} 连上了 —— 违反 ADR-034")


def test_static_mount_does_not_shadow_api_routes(live_server):
    """ADR-033:挂载顺序错误**只在真实服务器 + 真实 `dist/` 下暴露**。"""
    base, _ = live_server

    r = httpx.get(f"{base}/api/strategies", timeout=5.0)
    assert r.status_code == 200, "API 路由被静态挂载遮蔽了"
    assert r.headers["content-type"].startswith("application/json")

    if not api.FRONTEND_DIST.is_dir():
        pytest.skip("frontend/dist 尚不存在(T12 未落地)—— `/` 的断言留给 T12 之后")

    r = httpx.get(f"{base}/", timeout=5.0)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_frontend_build_output_exists():
    """断言 `npm run build` 的产物真实存在于 `frontend/dist/index.html`。"""
    index = api.FRONTEND_DIST / "index.html"
    if not api.FRONTEND_DIST.is_dir():
        pytest.skip("frontend/dist 尚不存在(T12 未落地)")
    assert index.is_file(), "frontend/dist/index.html 不存在 —— 构建产物缺失"
