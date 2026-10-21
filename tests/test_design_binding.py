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
README = ROOT / "README.md"


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
        # T22 / ADR-056 + ADR-057(J2 裁决逐字:「落锁后须把本节门里的裸数值
        # 改为 key 引用 —— 并把新 fixture 加入本门的覆盖清单,否则它们会成为
        # 第六到第二十处复述点」)。
        #
        # **刻意不收进来的两个 key**:`expect_churn_delta`(值为 `0`)与
        # `reject_fee_on_raw_open`(值为 `2.0`)。它们太泛 —— `== 0` 在
        # DESIGN.md 里有九处**与本 fixture 无关**的合法出现(`shares == 0`、
        # `mdd == 0.0` …),收进来只会产生误报,而误报会训练读者忽略这条门。
        # 两者的鉴别力由 `tests/test_slippage.py` 的「期望值 != 拒绝值」断言
        # 与 `tests/test_contracts.py` 的算术重算共同承担。
        ("adr057_slippage_affordability", [
            "expect_shares_slippage_only", "expect_shares_fee_and_slippage",
            "reject_shares_unfixed", "expect_cash_after_slippage_only",
            "expect_buy_price", "expect_sell_price",
            "expect_fee_on_slipped_price", "expect_amount_slipped",
            "expect_roundtrip_net_loss", "expect_real_stack_fill_price",
            "reject_real_stack_shares_unfixed", "reject_churn_delta",
        ]),
        ("i7_four_distinct_prices", [
            "expect_fill_price_buy_slipped", "expect_fill_price_sell_slipped",
        ]),
        # ADR-058(T23)。J2 裁决:新 fixture **必须**加入本清单,
        # 否则它们会成为第六到第二十处复述点(§7 的回归机制)。
        ("adr058_metrics", ["t1_sharpe", "t1_max_drawdown_pct",
                            "t2_sharpe", "t2_max_drawdown_pct",
                            "t6_sharpe", "t6_reject_sharpe_ddof0",
                            "t9_sharpe", "t9_max_drawdown_pct",
                            "t11_max_drawdown_pct", "t12_max_drawdown_pct"]),
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
    """冻结文案是那些不可验证前提与已知偏差的唯一守卫 —— 改措辞等于削弱守卫。

    每条断言钉的是该文案**承载的那件事**,不是它的措辞 —— 来自 ADR 的领域
    语义,不是来自 YAML 自身(否则「改得自洽」就没有任何东西反对)。

    **T24 / ADR-055 的破坏性更新**:`assumption_adjusted` 此前被钉为
    「含『复权』且含『不验证』」。ADR-050 之后「框架不验证复权」**是假的**
    —— 框架自己在 `load_csv` 里做复权,且 ADR-051/054 确实在验证。
    继续钉「不验证」等于用一条门**强制保留一句假话**,故本门随文案一并更新:
    现在钉的是「复权发生在框架内」。*(这正是替换而非补充的理由:两条声明
    若并存,`summary.txt` 会在相邻两行自相矛盾。)*
    """
    ft = c["frozen_text"]
    assert "对账" in ft["reconcile_line"] and "==" in ft["reconcile_line"]

    # ── ADR-055:复权的七条 ──
    # 新锚:复权在【框架内】。反向断言退役的那句假话不得回归。
    assert "复权" in ft["assumption_adjusted"]
    assert "框架内" in ft["assumption_adjusted"], (
        "ADR-055:该键须声明复权发生在框架内(因子法)"
    )
    assert "不验证" not in ft["assumption_adjusted"], (
        "ADR-050 之后「框架不验证复权」是假的 —— 该说法已被 ADR-055 退役,不得回归"
    )
    assert "预先复权" not in ft["assumption_adjusted"], (
        "「OHLC 由框架外预先复权」已被 ADR-050 推翻(ADR-001 主张 B/C/D)"
    )
    assert "高估" in ft["assumption_dividend_reinvested"], (
        "ADR-055 最重要的一条:分红再投资让净值曲线【高估】真实可得收益"
    )
    assert "基准" in ft["assumption_event_basis_unverifiable"]
    assert "Volume" in ft["assumption_volume_not_adjusted"]
    assert "体检" in ft["assumption_no_factor_path"] and \
        "诊断" in ft["assumption_no_factor_path"], "体检是提示,不是诊断"
    assert "比率" in ft["assumption_ratio_not_level"] and \
        "水平" in ft["assumption_ratio_not_level"], (
            "ADR-055 的有界泄露:保比率、不保水平"
        )

    # ── ADR-002 / ADR-020:成交语义(未变)──
    assert "市价单" in ft["assumption_market_order"] and "无条件" in ft["assumption_market_order"]

    # ── ADR-059:滑点的七条 ──
    assert "合并" in ft["assumption_slippage_fixed_bps"], "价差与冲击被合并成一个常数"
    assert "规模无关" in ft["assumption_slippage_size_blind"] and \
        "偏低" in ft["assumption_slippage_size_blind"], "对大单严重偏低是方向性结论"
    assert "开盘" in ft["assumption_slippage_open_spread"]
    assert "标的" in ft["assumption_slippage_single_symbol"]
    assert "Volume" in ft["assumption_slippage_volume_coupling"], (
        "ADR-059 要求交叉引用 ADR-050:复权不调整 Volume"
    )
    assert "乐观" in ft["assumption_slippage_still_optimistic"]
    assert "不代表可实现收益" in ft["assumption_slippage_zero_default"], (
        "零滑点永远不现实 —— 默认值的含义必须被声明(ADR-059)"
    )

    # ── ADR-059:夏普与回撤的七条 ──
    assert "高估" in ft["assumption_sharpe_rf_zero"], "rf=0 → 系统性高估"
    assert "日线" in ft["assumption_sharpe_daily_bars"]
    assert "正态" in ft["assumption_sharpe_iid_normal"]
    assert "噪声" in ft["assumption_sharpe_short_sample"]
    assert "未定义" in ft["assumption_sharpe_null_is_undefined"] and \
        "不表示 0" in ft["assumption_sharpe_null_is_undefined"], (
            "空值标记表示数学上未定义,**不表示 0**(ADR-058/059)"
        )
    assert "不是风险上界" in ft["assumption_drawdown_one_path"]
    assert "恢复日" in ft["assumption_drawdown_shape"]

    assert "1 日" in ft["single_day_notice"]
    assert "杠杆" in ft["leverage_not_implemented"]


def test_assumption_order_covers_every_assumption_key_exactly_once(c):
    """ADR-021 的 `assumptions` 通道:`assumption_order` 是唯一的装配清单。

    三件事:
      1. 每个键都存在于 `frozen_text`(否则 `backtest.assumption_texts()`
         在运行时 KeyError —— 而那是在用户跑完一次回测之后才炸)
      2. 无重复(同一条声明印两遍,就是本阶段要消灭的那类噪声)
      3. **`frozen_text` 里每个 `assumption_` 前缀的键都被收录** ——
         这是真正要守的那条:新增一条声明却忘了加进 `assumption_order`,
         **不会让任何既有门变红**,它只是让一条已知偏差从屏幕上消失。
    """
    ft, order = c["frozen_text"], c["assumption_order"]
    assert len(order) == len(set(order)), f"assumption_order 有重复:{order}"
    missing = [k for k in order if k not in ft]
    assert not missing, f"assumption_order 指向不存在的 frozen_text 键:{missing}"
    uncovered = sorted(k for k in ft if k.startswith("assumption_") and k not in order)
    assert not uncovered, (
        f"这些声明文案存在于 frozen_text 但【不会被输出】:{uncovered}\n"
        "把它们加进 contracts.yaml 的 `assumption_order`,或说明为何不输出"
    )


# `rf=0` / `T+1` / `1+s` / `不表示 0` 这类是**公式与结构标识**,不是校准量级。
# OQ-12 禁的是后者:没核实过的 bps / 倍数 / 根数 / 百分比。
_CALIBRATION_SHAPED = re.compile(
    r"\d+\s*(?:bps|倍|%|根|个交易日|个月)"          # 5bps / 2.2 倍 / 30 根
    r"|\d+\s*[-–~]\s*\d+\s*(?:bps|倍|%)"          # 1-5bps / 1.5–2 倍
    r"|年化因子(?:固定|为)?\s*\d"                    # 年化因子固定 252
)


def test_assumption_texts_carry_no_unverified_magnitudes(c):
    """T24 门 8 / OQ-12:**声明里不含任何未核验的数值。**

    设计期未能核验「开盘约为午间 1.5–2 倍」「大盘股 1–5bps / 小盘股 20–50bps」
    「周线约 2.2 倍」「30 根 K 线」这类量级,**构建期不得凭记忆补上**。
    ADR-059 原话:**在一个讲诚实的阶段里放一个未核实的数,正是本阶段反对的东西。**

    本门扫的是**校准形状**的数值(带 bps / 倍 / % / 根 之类单位的量级),
    而不是一切数字 —— `T+1`、`Open×(1+s)`、`rf=0`、「不表示 0」是公式与
    结构标识,去掉它们会让声明读不懂。

    *(ADR-059 自己的草稿文案里带着 `252` / `2.2 倍` / `30 根` / `10 倍` ——
    T24 落地时按本门去掉了它们,并在 §13 记明。这条纪律对设计稿同样适用。)*
    """
    offenders = {}
    for k in c["assumption_order"]:
        hits = _CALIBRATION_SHAPED.findall(c["frozen_text"][k])
        if hits:
            offenders[k] = hits
    assert not offenders, (
        "声明文案里出现了未核验的校准量级(OQ-12 / T24 门 8):\n"
        + "\n".join(f"  frozen_text.{k}: {v}" for k, v in offenders.items())
        + "\n只写方向性结论。要给具体数值,须先核实文献 + 一份近期券商成本报告"
    )


def test_readme_declaration_counts_match_assumption_order(c):
    """README 的声明索引表写着「七条 / 一条 / 七条 / 七条」—— 那是**手写的数**。

    为什么要一条门:README §「已知局限」的那张表是给人读的**索引**(它开头就
    声明「声明本体不在本文件」,只列有哪几类、各守什么),故它不是复述点、
    不违反 §12 的反复述纪律。但表里的条数是人手维护的,而
    `test_frozen_text_is_not_restated` 只扫 `DESIGN.md`、且只匹配**逐字**串,
    所以 README 的这几个数是**无门守护**的:下一次往 `assumption_order` 里
    加一条声明,README 会静默地继续写旧数,读者按它去数屏幕上的声明就会
    以为少印了一条。

    本门只核**数**,不核文案 —— 文案的唯一家仍是 `contracts.yaml`。
    分组按键名前缀,与 `assumption_order` 的注释所述的四段顺序一致。
    """
    order = c["assumption_order"]
    groups = {
        "adjust": [k for k in order if k.startswith("assumption_slippage_") is False
                   and k.startswith("assumption_sharpe_") is False
                   and k.startswith("assumption_drawdown_") is False
                   and k != "assumption_market_order"],
        "market": [k for k in order if k == "assumption_market_order"],
        "slippage": [k for k in order if k.startswith("assumption_slippage_")],
        "risk": [k for k in order if k.startswith("assumption_sharpe_")
                 or k.startswith("assumption_drawdown_")],
    }
    # 分组必须是 assumption_order 的一个划分 —— 否则新增一族声明会从本门的
    # 视野里整体消失(与 `uncovered` 那条同一个失效模式)。
    assert sum(len(v) for v in groups.values()) == len(order), (
        f"分组未覆盖 assumption_order 全集:{groups}"
    )

    cn = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七",
          8: "八", 9: "九", 10: "十"}
    text = README.read_text(encoding="utf-8")
    labels = {
        "adjust": "**ADR-055**(复权,{n}条)",
        "market": "**ADR-002 / ADR-020**(成交语义,{n}条)",
        "slippage": "**ADR-059**(滑点,{n}条)",
        "risk": "**ADR-059**(风险指标,{n}条)",
    }
    for key, tmpl in labels.items():
        n = len(groups[key])
        expected = tmpl.format(n=cn.get(n, str(n)))
        assert expected in text, (
            f"README 的声明索引表与 contracts.yaml::assumption_order 不符。\n"
            f"  期望出现:{expected}(实际 {n} 条)\n"
            "改了 assumption_order 就要同步 README 的这张表 —— 它只写条数与"
            "主题,文案本体仍只在 contracts.yaml"
        )


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
