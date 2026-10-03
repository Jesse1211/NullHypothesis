"""把 DESIGN.md 绑定到 contracts.yaml —— 这是第 9 轮审查指出缺的那一层。

第 9 轮的实证:审查者把 DESIGN.md 改回第 1–8 轮的原始缺陷(含 F1、F2 原文),
`test_contracts.py` **12 次全绿**。原因是那 26 个测试里只有 2 个读 DESIGN.md,
其余都是 contracts.yaml 和自己比 —— 于是 contracts.yaml 成了**第六个**复述点,
而不是替代前五个。

本文件补上缺的那层:断言 contracts.yaml 里的每个字面量在 DESIGN.md 中
**只以引用形式出现,不被复述**。第 9 轮那 12 个绿色变异,在这里必须全红。

运行:python3 -m pytest tests/test_design_binding.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "DESIGN.md"
CONTRACTS = ROOT / "contracts.yaml"


def _yaml():
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        pytest.fail(
            "PyYAML 缺失。契约门不得静默跳过 —— 那会让 §5 的承诺"
            "(「任何对钉死值的改动若破坏自洽,在写第一行实现代码之前就会红」)变成空话。"
            "PyYAML 已在 §6 声明为依赖;若确实未装,请先装它,不要跳过本门。"
        )
    return yaml.safe_load(CONTRACTS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def c():
    return _yaml()


@pytest.fixture(scope="module")
def design():
    return DESIGN.read_text(encoding="utf-8")


# ═══════════════ 核心:字面量不得在 DESIGN.md 中被复述 ═══════════════
#
# 允许的例外只有两类,且都必须显式列出:
#   1. 「历史说明」—— 记录某轮审查发现了什么,引用当时的错值作为对照
#   2. 「可复算示例」—— ADR-038 的示例 JSON,已由 test_contracts.py 单独验算
#
# 例外必须写在下面的白名单里,附理由。白名单之外的复述 = 漂移点。

ALLOWED_RESTATEMENTS = {
    # key: (最大允许出现次数, 理由)
    "formats.date": (99, "YYYY-MM-DD 作为日期【格式说明】在散文中出现属正常表述"),
}


def _candidate_lines(design: str) -> set[int]:
    """非历史说明、非示例块的行号集合。"""
    return {i for i, line in enumerate(design.split("\n"), 1)
            if not _is_history(line)}


def _is_history(line: str) -> bool:
    s = line.strip()
    return (s.startswith("*(") or s.startswith("- *(") or s.startswith("//")
            or any(k in line for k in ("此前", "原门", "原为", "早先"))
            or ("第 " in line and "轮" in line))


def _literal_sites(design: str, needle: str) -> list[int]:
    """返回 needle 出现的行号(1-based),跳过历史说明与可复算示例块。"""
    out = []
    in_example = False
    for i, line in enumerate(design.split("\n"), 1):
        if "可复算" in line or "示意,非可复算" in line:
            in_example = True
        if in_example and line.strip().startswith("//"):
            continue
        # 历史说明:以 *( 开头的斜体括注,记录「此前写作 X」
        stripped = line.strip()
        is_history = (
            stripped.startswith("*(") or stripped.startswith("- *(")
            or "此前" in line or "原门" in line or "原为" in line
            or "早先" in line or "第 " in line and "轮" in line
        )
        if is_history:
            continue
        if needle in line:
            out.append(i)
    return out


def test_format_specs_are_not_restated_in_design(c, design):
    """F1 的根因:格式规格在 DESIGN.md 里有一张表,与 contracts.yaml 各自演化。"""
    offenders = {}
    for name, spec in c["formats"].items():
        if f"formats.{name}" in ALLOWED_RESTATEMENTS:
            continue
        sites = _literal_sites(design, spec)
        if sites:
            offenders[name] = (spec, sites)
    assert not offenders, (
        "格式规格被复述在 DESIGN.md 中 —— 这正是 F1 的根因"
        "(ADR-021 的表与 T6 的门在九行之内矛盾)。\n"
        "改为引用 `contracts.yaml` 的 `formats.<key>` / `field_formats.<field>`:\n"
        + "\n".join(f"  formats.{k} = {v[0]!r} 出现在行 {v[1]}" for k, v in offenders.items())
    )


def test_exit_codes_are_not_restated(c, design):
    """退出码在 DESIGN.md 里有四处复述 —— 改一处不会让其余三处红。"""
    offenders = {}
    for name, code in c["exit_codes"].items():
        if name == "ok":
            continue  # 退出码 0 在散文里不可避免
        # 找「退出码 `N`」式的复述
        sites = _literal_sites(design, f"退出码 `{code}`")
        if sites:
            offenders[name] = (code, sites)
    assert not offenders, (
        "退出码被复述:\n"
        + "\n".join(f"  exit_codes.{k} = {v[0]} 出现在行 {v[1]}" for k, v in offenders.items())
        + "\n改为引用 `contracts.yaml` 的 `exit_codes.<name>`"
    )


def test_frozen_text_is_not_restated(c, design):
    """冻结文案有九处复述。"""
    offenders = {}
    for name, text in c["frozen_text"].items():
        sites = _literal_sites(design, text)
        if sites:
            offenders[name] = (text, sites)
    assert not offenders, (
        "冻结文案被复述:\n"
        + "\n".join(f"  frozen_text.{k} 出现在行 {v[1]}" for k, v in offenders.items())
        + "\n改为引用 `contracts.yaml` 的 `frozen_text.<name>`"
    )


def test_numeric_fixtures_are_not_restated(c, design):
    """F1/F2 之外最危险的一类:门里的期望值与 contracts.yaml 各自漂移。
    第 9 轮实证:把 T4 的 `shares == 80` 改成 `90`(一个错误实现的值),26 个测试全绿。"""
    offenders = []
    checks = [
        ("adr014_denominator_three_way", ["expect_final_shares",
                                          "reject_target_from_cash", "reject_spend_cash"]),
        ("adr012_floor_vs_ceil", ["expect_shares", "reject_shares", "expect_cash_after"]),
        ("adr021_gaps", ["expect_mean_abs_gap_pct"]),
        ("t5_summary_values", ["expect_total_return_pct",
                               "reject_ratio_unscaled", "reject_wrong_quotient"]),
        ("t2_strategy_equity", ["expect_equity"]),
    ]
    for fx, keys in checks:
        for k in keys:
            val = c["fixtures"][fx][k]
            # 只找「== 值」或「!= 值」形式的断言复述
            # 词边界:否则 `PORT == 8000` 会被 `== 80` 误报
            for pat in (rf"==\s*`?{val}`?(?![\d.])", rf"!=\s*`?{val}`?(?![\d.])"):
                sites = [i for i, line in enumerate(design.split("\n"), 1)
                         if re.search(pat, line) and i in _candidate_lines(design)]
                if sites:
                    offenders.append(f"  fixtures.{fx}.{k} = {val} 出现在行 {sites}")
    assert not offenders, (
        "数值 fixture 的期望值被复述在门里:\n" + "\n".join(offenders)
        + "\n改为引用 `contracts.yaml` 的 `fixtures.<name>.<key>`"
    )


def test_trades_csv_header_is_not_restated(c, design):
    header = ",".join(c["trades_csv_header"])
    sites = _literal_sites(design, header)
    assert not sites, (
        f"`trades.csv` 表头被复述在行 {sites} —— 改为引用 `contracts.yaml` 的 "
        "`trades_csv_header`。第 9 轮实证:把它改成驼峰命名,26 个测试全绿。"
    )


def test_run_id_regex_is_not_restated(c, design):
    pat = c["patterns"]["run_id"].replace("\\\\", "\\")
    sites = _literal_sites(design, pat)
    assert not sites, (
        f"`run_id` 正则被复述在行 {sites}。第 9 轮实证:把它改严(破坏 I10 的碰撞目录)"
        "后 26 个测试全绿 —— 改为引用 `contracts.yaml` 的 `patterns.run_id`"
    )


# ═══════════════ 门的可执行性前提必须写在文档里 ═══════════════

def test_process_declarations_appear_in_design(c, design):
    """gate_execution 只存在于 YAML 里没有用 —— 构建 agent 读的是 DESIGN.md。"""
    missing = []
    for gate in c["gate_execution"]["in_process_required"]:
        task = gate.split(".")[0]
        # 该任务的门里必须出现「进程内」字样
        sec = re.search(rf"### {task} ·.*?(?=\n### |\Z)", design, re.S)
        if not sec or "进程内" not in sec.group(0):
            missing.append(gate)
    assert not missing, (
        "以下门在 contracts.yaml 里标了 `in_process_required`,但对应任务的门文本"
        f"从未说明进程边界:{missing}\n"
        "构建 agent 读 DESIGN.md,不读 YAML —— 自检规则四在文档侧未被满足"
    )


def test_every_pinned_symbol_is_in_the_layout_gate(c, design):
    """F4/F5 的教训:本轮新钉死的两个符号都没被布局门断言。
    而 test_contracts.py 只 grep 名字是否在文档里出现过 —— 散文提一句就满足了。"""
    sec = re.search(r"ADR-035 布局契约门.*?(?=\n- \*\*断言收集|\Z)", design, re.S)
    assert sec, "找不到布局契约门"
    gate = sec.group(0)
    missing = [s.split(".")[-1] for s in c["gate_execution"]["monkeypatch_seams"]
               if s.split(".")[-1] not in gate]
    assert not missing, (
        f"以下打桩接缝未被布局门断言存在:{missing}\n"
        "被打桩的符号必须由布局门断言 —— 否则实现者不提供它时,只有那条门会红,"
        "而且会被归因到错误的任务"
    )


# ═══════════ 兜底:contracts.yaml 自身被改时也要有东西红 ═══════════
#
# 第 9 轮实证后的第二次实证:绑定层把 12 个变异抓住了 8 个。漏掉的四个都是
# 「改 contracts.yaml 自己」—— 值改得自洽,于是没有任何东西反对。
# 这里给那几类值一个独立的真相锚(来自领域语义,不是来自 YAML 自身)。

def test_exit_codes_match_their_domain_meaning(c):
    """退出码不是任意的:0 成功、2 数据、3 策略、4 参数 —— 这是 ADR-040 的语义。"""
    assert c["exit_codes"] == {
        "ok": 0, "data_validation": 2, "strategy_error": 3, "invalid_cli_args": 4,
    }, "退出码被改动。它们是对外契约(脚本/CI 依赖),改动必须经 ADR-040 并同步此锚"


def test_frozen_text_keeps_its_load_bearing_content(c):
    """冻结文案是 ADR-001/020/023 的唯一守卫 —— 改措辞等于削弱那条守卫。"""
    ft = c["frozen_text"]
    assert "对账" in ft["reconcile_line"] and "==" in ft["reconcile_line"]
    assert "复权" in ft["assumption_adjusted"] and "不验证" in ft["assumption_adjusted"]
    assert "市价单" in ft["assumption_market_order"] and "无条件" in ft["assumption_market_order"]
    assert "1 日" in ft["single_day_notice"]
    assert "杠杆" in ft["leverage_not_implemented"]


def test_field_formats_signedness_is_semantically_correct(c):
    """带不带符号不是口味问题:绝对值量带 + 号零信息,方向量不带就丢信息。"""
    ff, fm = c["field_formats"], c["formats"]
    for field in ("total_return_pct", "max_gap_pct"):
        assert "+" in fm[ff[field]], f"{field} 是方向量,必须带符号"
    assert "+" not in fm[ff["mean_abs_gap_pct"]], "绝对值均值恒 >= 0,带 + 号零信息(F1 的根因)"


def test_adr035_pins_every_symbol_the_gates_patch(c, design):
    """漏掉的第四类:DESIGN.md 里把符号改名,YAML 的 seam 列表还指着旧名。
    `test_contracts.py` 只 grep 名字出现过,改名后新名出现、旧名消失 —— 它抓不到。"""
    import re as _re
    missing = []
    for seam in c["gate_execution"]["monkeypatch_seams"]:
        bare = seam.split(".")[-1]
        # 必须作为【带签名的定义】出现在 ADR-035 的模块布局块里,不是散文提一句
        if not _re.search(rf"{_re.escape(bare)}\s*(\(|:|->)", design):
            missing.append(seam)
    assert not missing, (
        f"以下打桩接缝未在 ADR-035 中以带签名的形式钉死:{missing}\n"
        "散文里提一句不算 —— 实现者改名后,门就指向不存在的符号"
    )
