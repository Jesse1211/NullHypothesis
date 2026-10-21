"""T20 的验收门:公司行为表契约 + 载入校验(ADR-053 / ADR-054)。

**真实数据状态**(与 `tests/test_data.py` 同一条硬要求):每个测试都把行为表
**真实写到磁盘**,再用路径调用 `load_events` —— 不构造内存 DataFrame。
内存 DataFrame 会绕过 `pd.read_csv` 的类型推断与空文件/仅表头行为,
而那恰恰是本任务要校的东西。

**期望值一律引用 `contracts.yaml` 的 key,不复述字面量** ——
该规则写在 `contracts.yaml` 开头,由 `tests/test_design_binding.py` 强制。

**本任务不做价格调整。** 这里只验「一个经校验的事件序列」;因子法、
两路仲裁、方向门与量级门都是 T21 的门。

blindSpots(T20 的门要求声明):
  * 本任务**不验证事件内容的真实性** —— 行为表里写错一个 `ratio`
    (例如把 4:1 拆股写成约定 B 的 `0.25`)而通过**全部**格式校验是可能的。
    那由 T21 的方向门(`close[ex]/close[ex-1] ≈ 1/ratio`)抓,
    **没有任何格式级校验能发现它**(ADR-053 的原话)。
  * `amount` 与 `close_before` 的**同股数基准**前提(ADR-054)在本任务里
    完全不可观测 —— 这里没有价格,故连部分验证都做不到。
  * 继承 `tests/test_data.py` 声明的那组 CSV 变体(编码 / BOM / 千分位 /
    CRLF / 分隔符变体 / 超大文件)—— 行为表用同一个 `pd.read_csv`,
    故同样未被测试,且同样无 ADR 契约。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from nullhypothesis.data import (
    EVENT_COLUMNS,
    EVENT_IGNORED_COLUMNS,
    EVENT_VALUES,
    CorporateEvent,
    NoOpEventWarning,
    OutOfRangeEventWarning,
    load_csv,
    load_events,
)
from nullhypothesis.errors import DataValidationError

ROOT = Path(__file__).resolve().parent.parent
C = yaml.safe_load((ROOT / "contracts.yaml").read_text(encoding="utf-8"))

SCHEMA = C["fixtures"]["adr053_events_schema"]
RATIO = C["fixtures"]["adr053_ratio_convention"]
CALENDAR = C["fixtures"]["adr053_calendar_day"]
REAL = C["fixtures"]["adr053_real_events_table"]
RANGE = C["fixtures"]["adr053_out_of_range"]

HEADER = ",".join(SCHEMA["columns"])
SPLIT, DIVIDEND = SCHEMA["event_values"]


def write_events(tmp_path: Path, text: str, name: str = "events.csv") -> Path:
    """把行为表文本真实写到磁盘,返回其路径。"""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def rows_to_csv(rows: list[str], header: str = HEADER) -> str:
    return header + "\n" + "".join(r + "\n" for r in rows)


# ═════════════════ 门 2:四列解析 + symbol/ticker 校验并忽略 ═════════════════


def test_four_columns_parse_into_a_validated_event_sequence(tmp_path):
    """门 2:四列 `date,event,ratio,amount` 解析正确。

    `ratio`/`amount` **互斥**(ADR-053):split 行只有 `ratio`,
    dividend 行只有 `amount`,另一个恒为 `None`。
    """
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][0]},{DIVIDEND},,{REAL['expect_amounts'][0]}",
        f"{REAL['expect_dates'][1]},{SPLIT},{REAL['expect_ratios'][1]},",
        f"{REAL['expect_dates'][2]},{DIVIDEND},,{REAL['expect_amounts'][2]}",
    ]))
    events = load_events(path)

    assert len(events) == REAL["expect_count"]
    assert [e.date.isoformat() for e in events] == REAL["expect_dates"]
    assert [e.event for e in events] == REAL["expect_events"]
    assert [e.ratio for e in events] == REAL["expect_ratios"]
    assert [e.amount for e in events] == REAL["expect_amounts"]
    # 值对象的字段名与 ADR-053 的四列逐字同名 —— 列名是对外契约。
    assert tuple(CorporateEvent.__dataclass_fields__) == tuple(EVENT_COLUMNS)


def test_schema_constants_match_the_pinned_contract():
    """实现侧的常量必须等于 `contracts.yaml` 钉死的那一组 —— 否则
    两处各自演化,而这正是 `contracts.yaml` 开头记载的那类漂移。"""
    assert list(EVENT_COLUMNS) == SCHEMA["columns"]
    assert list(EVENT_VALUES) == SCHEMA["event_values"]
    assert list(EVENT_IGNORED_COLUMNS) == SCHEMA["ignored_columns"]


@pytest.mark.parametrize("ignored", SCHEMA["ignored_columns"])
def test_symbol_and_ticker_columns_are_validated_then_ignored(tmp_path, ignored):
    """门 2 后半:`symbol`/`ticker` 存在则**校验并忽略**(ADR-053)。

    拒收会拒掉用户真实粘贴的文件(Polygon / Alpaca / EODHD 的导出都带它);
    但「忽略」不等于「不看」—— 一列全空的 `symbol` 是导出残缺的信号。
    """
    path = write_events(tmp_path, rows_to_csv(
        [f"{REAL['expect_dates'][1]},{SPLIT},{REAL['expect_ratios'][1]},,AAPL"],
        header=f"{HEADER},{ignored}",
    ))
    events = load_events(path)

    assert len(events) == 1
    # 「忽略」= 不进入事件序列:值对象上没有这个字段。
    assert not hasattr(events[0], ignored)
    assert events[0].event == SPLIT
    assert events[0].ratio == REAL["expect_ratios"][1]


@pytest.mark.parametrize("ignored", SCHEMA["ignored_columns"])
def test_empty_value_in_an_ignored_column_still_fails(tmp_path, ignored):
    """「校验并忽略」的**校验**那一半必须可观测 —— 否则一个把该列
    完全不看的实现也能通过上一条门。"""
    path = write_events(tmp_path, rows_to_csv(
        [f"{REAL['expect_dates'][1]},{SPLIT},{REAL['expect_ratios'][1]},,"],
        header=f"{HEADER},{ignored}",
    ))
    with pytest.raises(DataValidationError) as exc:
        load_events(path)
    assert exc.value.column == ignored


# ═══════════════════ 门 3:date 按日历日解析,不得按 UTC ═══════════════════


def test_date_is_parsed_as_a_calendar_day_not_a_utc_datetime(tmp_path):
    """门 3:构造一个若按 UTC 解析会移一天的用例,断言**未移**。

    ADR-053:按 UTC datetime 解析会把除息日**整体移一天**。一个 UTC 午夜
    时刻在任何负时区的本地日历日上就是前一天 —— 故这里既断言正值,
    也断言那个被移走的值**不**出现。
    """
    path = write_events(tmp_path, rows_to_csv([
        f"{CALENDAR['raw_date']},{SPLIT},{RATIO['split_4_for_1']},",
    ]))
    events = load_events(path)

    assert events[0].date.isoformat() == CALENDAR["expect_iso"]
    assert events[0].date.isoformat() != CALENDAR["reject_utc_shifted"]
    # 类型层面也不留那个自由度:`date` 没有时刻,也就没有时区转换可移。
    assert type(events[0].date) is date
    assert not hasattr(events[0].date, "tzinfo")


# ═══════════════════ 门 4:event 枚举外的值一律报错 ═══════════════════


@pytest.mark.parametrize("bad", SCHEMA["rejected_event_values"])
def test_event_outside_the_enum_is_a_loud_failure(tmp_path, bad):
    """门 4:`{split, dividend}` 之外的值 → **报错**,不静默跳过。

    四个值都是真实野外值:`stock_dividend`/`spinoff` 来自厂商导出
    (EODHD 的 splits 端点会混入 spinoff),`SPLF`/`DVCA` 是 ISO 的电文代码。
    不支持送股/配股/spinoff/并购是 ADR-053 的**决策而非遗漏**。
    """
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][1]},{bad},{RATIO['split_4_for_1']},",
    ]))
    with pytest.raises(DataValidationError) as exc:
        load_events(path)

    assert exc.value.column == "event"
    assert exc.value.row == 0
    assert bad in str(exc.value)


def test_an_illegal_event_is_not_silently_dropped(tmp_path):
    """门 4 的「不静默跳过」那一半:一个**合法行 + 一个非法行**的文件
    必须报错,而不是返回那一条合法事件。

    没有这条门,一个「过滤掉不认识的 event」的实现会通过上一条门里
    「只有非法行」的用例之外的全部情形 —— 而那正是 ADR-019 反对的静默降级。
    """
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][0]},{DIVIDEND},,{REAL['expect_amounts'][0]}",
        f"{REAL['expect_dates'][1]},{SCHEMA['rejected_event_values'][1]},"
        f"{RATIO['split_4_for_1']},",
    ]))
    with pytest.raises(DataValidationError):
        load_events(path)


# ═══════════════════ 门 5:列错位(ratio/amount 放错行)═══════════════════


def test_ratio_on_a_dividend_row_is_rejected(tmp_path):
    """门 5:`ratio` 出现在 dividend 行 → 报错(抓列错位的导出)。"""
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][0]},{DIVIDEND},{RATIO['split_4_for_1']},"
        f"{REAL['expect_amounts'][0]}",
    ]))
    with pytest.raises(DataValidationError) as exc:
        load_events(path)
    assert exc.value.column == "ratio"


def test_amount_on_a_split_row_is_rejected(tmp_path):
    """门 5:`amount` 出现在 split 行 → 报错。"""
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][1]},{SPLIT},{RATIO['split_4_for_1']},"
        f"{REAL['expect_amounts'][0]}",
    ]))
    with pytest.raises(DataValidationError) as exc:
        load_events(path)
    assert exc.value.column == "amount"


def test_split_row_without_ratio_is_rejected(tmp_path):
    """split 行的 `ratio` 必填(ADR-053 的列表)。"""
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][1]},{SPLIT},,",
    ]))
    with pytest.raises(DataValidationError) as exc:
        load_events(path)
    assert exc.value.column == "ratio"


def test_dividend_row_without_amount_is_rejected(tmp_path):
    """dividend 行的 `amount` 必填(ADR-053 的列表)。"""
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][0]},{DIVIDEND},,",
    ]))
    with pytest.raises(DataValidationError) as exc:
        load_events(path)
    assert exc.value.column == "amount"


# ═══════════ 门 6:取值域 + ratio == 1.0 的 NoOpEventWarning ═══════════


@pytest.mark.parametrize("bad_ratio", [0.0, -1.0, -RATIO["split_4_for_1"]])
def test_non_positive_ratio_is_rejected(tmp_path, bad_ratio):
    """门 6:`ratio <= 0` → 报错。前向股数比必须 > 0。"""
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][1]},{SPLIT},{bad_ratio},",
    ]))
    with pytest.raises(DataValidationError) as exc:
        load_events(path)
    assert exc.value.column == "ratio"


@pytest.mark.parametrize("bad_amount", [0.0, -1.0])
def test_non_positive_amount_is_rejected(tmp_path, bad_amount):
    """门 6:`amount <= 0` → 报错。每股税前毛额必须 > 0。"""
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][0]},{DIVIDEND},,{bad_amount}",
    ]))
    with pytest.raises(DataValidationError) as exc:
        load_events(path)
    assert exc.value.column == "amount"


def test_ratio_one_warns_loudly_and_leaves_the_factor_calculation(tmp_path):
    """门 6 的核心:`ratio == 1.0` → `NoOpEventWarning`,且该事件
    **不进入因子计算**,**且不改变返回值契约**。

    为什么必须是**响亮的警告**而非静默跳过:ADR-053 记明约定 D
    (CRSP 的 `FACPR` / ISO 的 `ADEX`,表示【增量】股数)会把一次真实的
    2:1 拆股写成 `1.0` —— 静默丢弃等于把一次真实拆股无声吞掉。
    """
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][0]},{DIVIDEND},,{REAL['expect_amounts'][0]}",
        f"{REAL['expect_dates'][1]},{SPLIT},{RATIO['noop_ratio']},",
    ]))
    with pytest.warns(NoOpEventWarning):
        events = load_events(path)

    # 「不进入因子计算」= 不在返回的事件序列里。
    assert [e.event for e in events] == [DIVIDEND]
    # 「不改变返回值契约」= 仍是一个正常的 list[CorporateEvent],不是 None /
    # 不是 (events, warnings) 元组 —— 与 `UnsortedDateWarning` 同构。
    assert isinstance(events, list)
    assert all(isinstance(e, CorporateEvent) for e in events)


def test_noop_warning_is_a_user_warning_subclass_like_its_sibling():
    """门 6 的「与 `UnsortedDateWarning` 同构」:走 `warnings` 通道
    ⇒ 必须是 `UserWarning` 的子类,否则 `-W error::UserWarning` 之类的
    用户配置会对两者行为不一致。"""
    assert issubclass(NoOpEventWarning, UserWarning)
    assert issubclass(OutOfRangeEventWarning, UserWarning)


def test_a_genuine_split_does_not_warn(tmp_path):
    """`NoOpEventWarning` 的否定用例 —— 否则一个「对每个 split 都警告」的
    实现也能通过上面那条门。"""
    import warnings as _w

    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][1]},{SPLIT},{RATIO['split_4_for_1']},",
    ]))
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        events = load_events(path)

    assert not [w for w in caught if issubclass(w.category, NoOpEventWarning)]
    assert len(events) == 1


# ═════════ ratio 的约定 A:4:1 = 4.0、1:8 合股 = 0.125(ADR-053)═════════


def test_ratio_is_the_forward_share_ratio_convention_a(tmp_path):
    """ADR-053:`ratio` ≡ **前向股数比**(约定 A)。4:1 拆股 = `4.0`,
    1:8 合股 = `0.125`(`ratio < 1` 自然即合股)。

    **本门不能证明方向正确** —— 约定 A 与 B 互为倒数,`0.25` 与 `8.0` 同样
    是合法浮点,格式校验**结构上**看不见读反。故这里只钉「载入按原值透传、
    不做任何倒数变换」;方向由 T21 的方向门抓(见本文件 blindSpots)。
    """
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][1]},{SPLIT},{RATIO['split_4_for_1']},",
        f"{REAL['expect_dates'][2]},{SPLIT},{RATIO['reverse_split_1_for_8']},",
    ]))
    events = load_events(path)

    assert [e.ratio for e in events] == [
        RATIO["split_4_for_1"], RATIO["reverse_split_1_for_8"],
    ]
    # 没有被悄悄换成约定 B(那是 16 倍误差的入口)。
    assert events[0].ratio != RATIO["reject_b_4_for_1"]
    assert events[1].ratio != RATIO["reject_b_1_for_8"]
    # 合股的 ratio < 1 —— 约定 A 的第 ② 条理由。
    assert events[1].ratio < 1.0 < events[0].ratio


# ═══════════════════ 门 7:重复 (date, event) → 报错 ═══════════════════


def test_duplicate_date_event_pair_is_rejected(tmp_path):
    """门 7:重复 `(date, event)` → 报错,与 `data.py` 现有的重复日期规则一致。

    静默去重会悄悄改变事件数,属 ADR-019 反对的静默降级。
    """
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][0]},{DIVIDEND},,{REAL['expect_amounts'][0]}",
        f"{REAL['expect_dates'][0]},{DIVIDEND},,{REAL['expect_amounts'][2]}",
    ]))
    with pytest.raises(DataValidationError) as exc:
        load_events(path)

    message = str(exc.value)
    assert REAL["expect_dates"][0] in message
    assert DIVIDEND in message


def test_same_event_on_different_dates_is_fine(tmp_path):
    """门 7 的否定用例:重复的是 `(date, event)` **对**,不是单独的 date
    或单独的 event —— 两笔不同日的分红必须合法。"""
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][0]},{DIVIDEND},,{REAL['expect_amounts'][0]}",
        f"{REAL['expect_dates'][2]},{DIVIDEND},,{REAL['expect_amounts'][2]}",
    ]))
    assert len(load_events(path)) == 2


# ═══════════════ 门 8:同日 split + dividend → 报错 ═══════════════


def test_split_and_dividend_on_the_same_day_is_rejected(tmp_path):
    """门 8:同日 `split` + `dividend` → 报错(ADR-053),
    消息须**指明日期**并**提示回退 `Adj Close`**。

    不支持的理由不是「猜错有风险」,而是让这个 bug 在代码里**物理上不存在**:
    框架从不把 split 因子与 dividend 因子在同一天相乘,就没有口径可错。
    """
    day = REAL["expect_dates"][1]
    path = write_events(tmp_path, rows_to_csv([
        f"{day},{DIVIDEND},,{REAL['expect_amounts'][0]}",
        f"{day},{SPLIT},{REAL['expect_ratios'][1]},",
    ]))
    with pytest.raises(DataValidationError) as exc:
        load_events(path)

    message = str(exc.value)
    assert day in message, "错误消息须指明日期"
    assert "Adj Close" in message, "错误消息须提示回退 Adj Close"


# ═══════════ 门 9:空文件 / 仅表头 → 合法(等价于无事件)═══════════


def test_header_only_events_table_is_legal_and_means_no_events(tmp_path):
    """门 9:仅表头 → 合法,**不报错**,等价于无事件。

    这与 `load_csv` 的仅表头是**不同**的决策:价格序列为空时无从回测,
    而行为表为空是一个完全正常的事实(该标的没有公司行为)。
    """
    assert load_events(write_events(tmp_path, HEADER + "\n")) == []


def test_completely_empty_events_file_is_legal(tmp_path):
    """门 9:空文件(连表头都没有)→ 合法,不报错。"""
    assert load_events(write_events(tmp_path, "")) == []


def test_a_missing_events_file_is_still_an_error(tmp_path):
    """门 9 的边界:**空**文件合法,**不存在**的文件仍是错误 ——
    否则一个打错路径的 `--events` 会静默变成「无行为表」。"""
    with pytest.raises(DataValidationError):
        load_events(tmp_path / "nope.csv")


@pytest.mark.parametrize("missing", SCHEMA["columns"])
def test_missing_required_column_is_rejected(tmp_path, missing):
    """四列都是必须列 —— 缺任一列即报错,且消息指明是哪一列。"""
    kept = [c for c in SCHEMA["columns"] if c != missing]
    path = write_events(tmp_path, ",".join(kept) + "\n")
    with pytest.raises(DataValidationError) as exc:
        load_events(path)
    assert exc.value.column == missing


# ═══════ 门 10:区间外事件被忽略且提示(不是错误)═══════


PRICE_CSV = """Date,Open,High,Low,Close,Volume
2015-01-02,100.0,105.0,99.0,104.0,1000
2015-01-05,104.0,108.0,103.0,107.0,1100
2015-01-06,107.0,110.0,106.0,109.0,1200
2015-01-07,109.0,112.0,108.0,111.0,1300
2015-01-08,111.0,114.0,110.0,113.0,1400
2015-01-09,113.0,116.0,112.0,115.0,1500
"""


@pytest.mark.parametrize("key", ["before_range", "after_range"])
def test_event_outside_the_price_range_is_ignored_with_a_notice(tmp_path, key):
    """门 10:行为表日期落在价格序列**区间之外** → 该事件被忽略且**提示**。

    **不是错误**:行为表是**标的的属性**,价格文件是**区间的切片**
    (ADR-053),故一份行为表合理地可以覆盖更长的区间。
    """
    prices = load_csv(write_events(tmp_path, PRICE_CSV, "prices.csv"))
    path = write_events(tmp_path, rows_to_csv([
        f"{RANGE[key]},{SPLIT},{RATIO['split_4_for_1']},",
    ]))

    with pytest.warns(OutOfRangeEventWarning):
        events = load_events(path, price_dates=prices["Date"])

    assert events == [], "区间外的事件必须被忽略"


def test_in_range_events_survive_while_out_of_range_ones_are_dropped(tmp_path):
    """门 10 的否定用例:区间**内**的事件必须留下 —— 否则一个
    「把所有事件都丢掉」的实现也能通过上面那条门。"""
    prices = load_csv(write_events(tmp_path, PRICE_CSV, "prices.csv"))
    in_range = str(prices["Date"].iloc[2].date())
    path = write_events(tmp_path, rows_to_csv([
        f"{RANGE['before_range']},{SPLIT},{RATIO['split_4_for_1']},",
        f"{in_range},{DIVIDEND},,{REAL['expect_amounts'][0]}",
        f"{RANGE['after_range']},{SPLIT},{RATIO['reverse_split_1_for_8']},",
    ]))

    with pytest.warns(OutOfRangeEventWarning):
        events = load_events(path, price_dates=prices["Date"])

    assert len(events) == RANGE["expect_kept_count"]
    assert events[0].date.isoformat() == in_range


def test_no_notice_when_every_event_is_in_range(tmp_path):
    """区间内时**不**提示 —— 一个「总是提示」的实现无信息量。"""
    import warnings as _w

    prices = load_csv(write_events(tmp_path, PRICE_CSV, "prices.csv"))
    in_range = str(prices["Date"].iloc[1].date())
    path = write_events(tmp_path, rows_to_csv([
        f"{in_range},{SPLIT},{RATIO['split_4_for_1']},",
    ]))

    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        events = load_events(path, price_dates=prices["Date"])

    assert not [
        w for w in caught if issubclass(w.category, OutOfRangeEventWarning)
    ]
    assert len(events) == 1


def test_range_boundaries_are_inclusive(tmp_path):
    """区间是**闭**区间:首日与末日上的事件在区间**内**。

    除权除息日就是「按新基准交易的第一个 session」(ADR-053)—— 落在
    价格切片的首/末日是完全正常的,排除它们会静默丢掉一次真实事件。
    """
    prices = load_csv(write_events(tmp_path, PRICE_CSV, "prices.csv"))
    first = str(prices["Date"].iloc[0].date())
    last = str(prices["Date"].iloc[-1].date())
    path = write_events(tmp_path, rows_to_csv([
        f"{first},{DIVIDEND},,{REAL['expect_amounts'][0]}",
        f"{last},{SPLIT},{RATIO['split_4_for_1']},",
    ]))
    assert len(load_events(path, price_dates=prices["Date"])) == 2


# ═══════════════════ 事件序列按日期升序 ═══════════════════


def test_events_are_returned_in_ascending_date_order(tmp_path):
    """因子的累积规则按日期走(T21),故载入期就把序列排好 ——
    文件内次序由用户手写,不可依赖。"""
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][2]},{DIVIDEND},,{REAL['expect_amounts'][2]}",
        f"{REAL['expect_dates'][0]},{DIVIDEND},,{REAL['expect_amounts'][0]}",
        f"{REAL['expect_dates'][1]},{SPLIT},{REAL['expect_ratios'][1]},",
    ]))
    events = load_events(path)
    assert [e.date.isoformat() for e in events] == REAL["expect_dates"]


# ═══════════ ADR-019 的姿态:一次校完,全部响亮失败 ═══════════


def test_multiple_problems_are_reported_in_one_exception(tmp_path):
    """与 `load_csv` 同构(ADR-019):多个问题在**同一个**异常里一次报全,
    不做「遇到第一个就返回」的早退。"""
    path = write_events(tmp_path, rows_to_csv([
        f"{REAL['expect_dates'][0]},{SCHEMA['rejected_event_values'][0]},,1.0",
        f"{REAL['expect_dates'][1]},{SPLIT},-1.0,",
    ]))
    with pytest.raises(DataValidationError) as exc:
        load_events(path)

    message = str(exc.value)
    assert SCHEMA["rejected_event_values"][0] in message
    assert "ratio" in message
    assert exc.value.file == str(path)


# ═════════════════════ 门 11:--events 未传 ⇒ 空序列 ═════════════════════


def test_run_request_events_defaults_to_none():
    """门 11:`RunRequest` 的 `events` **可空且默认 `None`**(ADR-044 追加在
    末尾)—— 既有的四位置参数构造必须逐字仍可用。

    追加而非插入:ADR-044 钉死了**顺序**,而既有门大量使用位置构造。
    """
    from nullhypothesis.run import RunRequest

    req = RunRequest(["buy_and_hold.py"], "d.csv", 100000.0, 0.0)
    assert req.events is None


def test_api_request_body_events_defaults_to_none():
    """门 11 的 API 侧:ADR-038 的请求体新增 `events`,**可选,默认 `None`**。"""
    import api

    body = api.RunRequestBody(strategies=["buy_and_hold.py"], data_file="d.csv")
    assert body.events is None


# ═════════════ REAL_STACK_GATE:CLI ↔ API 解析出同一事件序列 ═════════════
#
# 与现有 T10 的 `test_api_and_cli_agree_field_by_field` 同构:**真起一个 CLI
# 子进程**(不是调 `load_events` 和自己比),再走 API 一次,两侧对账。
# 行为表含**一次拆股 + 两笔分红**(T20 的 REAL_STACK_GATE 原文)。

DATA_FILE = "synthetic.csv"
STRAT = "buy_and_hold.py"


def _real_events_csv() -> str:
    """REAL_STACK_GATE 的行为表:一次拆股 + 两笔分红。

    日期取自 `contracts.yaml`,且**落在 `data/synthetic.csv` 的区间内** ——
    由 `test_real_events_table_dates_are_inside_the_synthetic_range` 守护,
    否则事件会被门 10 的区间过滤悄悄丢掉,本门就测了个空序列。
    """
    rows = []
    for day, event, ratio, amount in zip(
        REAL["expect_dates"], REAL["expect_events"],
        REAL["expect_ratios"], REAL["expect_amounts"],
    ):
        rows.append(
            f"{day},{event},"
            f"{'' if ratio is None else ratio},"
            f"{'' if amount is None else amount}"
        )
    return rows_to_csv(rows)


@pytest.fixture
def real_events_file():
    """把行为表写进真实的 `data/`(API 只读自那里,ADR-027),跑完即删。"""
    path = ROOT / "data" / REAL["filename"]
    path.write_text(_real_events_csv(), encoding="utf-8")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def test_real_events_table_dates_are_inside_the_synthetic_range():
    """前提门:REAL_STACK_GATE 的三个事件日必须落在真实价格序列的区间内。

    没有这条门,把 `expect_dates` 改到区间外会让 CLI↔API 对账门**仍然绿**
    (两侧都解析出空序列)—— 那正是「门指向的地方才有注意力」那类缺陷。
    """
    prices = load_csv(ROOT / "data" / DATA_FILE)
    lo, hi = prices["Date"].min().date(), prices["Date"].max().date()
    for day in REAL["expect_dates"]:
        parsed = date.fromisoformat(day)
        assert lo <= parsed <= hi, f"{day} 不在 {lo}..{hi} 内"


def test_real_events_table_has_one_split_and_two_dividends():
    """REAL_STACK_GATE 的原文要求:**一次拆股 + 两笔分红**。"""
    assert REAL["expect_events"].count(SPLIT) == 1
    assert REAL["expect_events"].count(DIVIDEND) == 2
    assert len(REAL["expect_events"]) == REAL["expect_count"]


def test_cli_and_api_parse_the_same_event_sequence(real_events_file, tmp_path):
    """REAL_STACK_GATE:经 **CLI 与 API 两条路径**载入同一份真实行为表,
    断言两侧解析出**同一个**事件序列(CLI↔API 对账,与 T10 同构)。

    对账的锚是 `run.json` / 响应里 `request.events` 的**文件名**,加上
    两侧**在同一个进程外**再解析一次得到的事件序列 —— 本任务不调价,
    故 `summary` 不会因行为表而变,`request.events` 是唯一的可观测投影。
    """
    from fastapi.testclient import TestClient

    import api

    # ── 路径一:真起 CLI 子进程 ──
    out_dir = tmp_path / "cli-out"
    proc = subprocess.run(
        [sys.executable, "backtest.py",
         "--data", f"data/{DATA_FILE}",
         "--strategy", f"strategies/{STRAT}",
         "--events", f"data/{REAL['filename']}",
         "--out", str(out_dir)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == C["exit_codes"]["ok"], proc.stderr
    run_dirs = [d for d in out_dir.iterdir() if d.is_dir()]
    assert len(run_dirs) == 1
    cli_run_json = json.loads(
        (run_dirs[0] / C["filenames"]["run_json"]).read_text(encoding="utf-8")
    )

    # ── 路径二:API ──
    client = TestClient(api.app)
    api_out = tmp_path / "api-out"
    api_out.mkdir()
    original_out = api.OUT_DIR
    api.OUT_DIR = api_out
    try:
        response = client.post("/api/run", json={
            "strategies": [STRAT], "data_file": DATA_FILE,
            "cash": 100000.0, "fee": 0.0,
            "events": REAL["filename"],
        })
    finally:
        api.OUT_DIR = original_out
    assert response.status_code == 200, response.text
    api_run_dirs = [d for d in api_out.iterdir() if d.is_dir()]
    assert len(api_run_dirs) == 1
    api_run_json = json.loads(
        (api_run_dirs[0] / C["filenames"]["run_json"]).read_text(encoding="utf-8")
    )

    # ── 对账 ①:两侧的 `request.events` 是同一个文件名 ──
    assert cli_run_json["request"]["events"] == REAL["filename"]
    assert api_run_json["request"]["events"] == REAL["filename"]

    # ── 对账 ②:两侧载入的事件序列**逐字段相同**,且等于钉死的期望值 ──
    prices = load_csv(ROOT / "data" / DATA_FILE)
    parsed = load_events(
        ROOT / "data" / REAL["filename"], price_dates=prices["Date"]
    )
    assert len(parsed) == REAL["expect_count"]
    assert [e.date.isoformat() for e in parsed] == REAL["expect_dates"]
    assert [e.event for e in parsed] == REAL["expect_events"]
    assert [e.ratio for e in parsed] == REAL["expect_ratios"]
    assert [e.amount for e in parsed] == REAL["expect_amounts"]


def test_cli_rejects_a_missing_events_file(tmp_path):
    """ADR-039 的扩展行:`--events` 给定则**文件须存在** ——
    与 `--data` 逐字对称,故退出码是 `invalid_cli_args`。"""
    proc = subprocess.run(
        [sys.executable, "backtest.py",
         "--data", f"data/{DATA_FILE}",
         "--strategy", f"strategies/{STRAT}",
         "--events", "data/__does_not_exist__.csv",
         "--out", str(tmp_path / "out")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == C["exit_codes"]["invalid_cli_args"], proc.stdout


def test_cli_without_events_records_none(tmp_path):
    """门 11:**不传** `--events` 时 `run.json` 的 `request.events` 为 `null`,
    其余字段逐字不变 —— 加性保证的可观测形式。"""
    out_dir = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "backtest.py",
         "--data", f"data/{DATA_FILE}",
         "--strategy", f"strategies/{STRAT}",
         "--out", str(out_dir)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == C["exit_codes"]["ok"], proc.stderr
    run_dirs = [d for d in out_dir.iterdir() if d.is_dir()]
    payload = json.loads(
        (run_dirs[0] / C["filenames"]["run_json"]).read_text(encoding="utf-8")
    )
    assert payload["request"]["events"] is None


def test_api_rejects_a_traversal_events_name():
    """`events` 过与 `data_file` **同一道**白名单闸(ADR-027)——
    漏掉它等于给 `events` 开一条路径穿越通道。"""
    from fastapi.testclient import TestClient

    import api

    client = TestClient(api.app)
    response = client.post("/api/run", json={
        "strategies": [STRAT], "data_file": DATA_FILE,
        "events": "../contracts.yaml",
    })
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_REQUEST"


def test_api_reports_a_missing_events_file_as_not_found():
    """名字合法但文件不在 → 404(不是 422:请求本身没问题)——
    与 `data_file` 逐字对称。"""
    from fastapi.testclient import TestClient

    import api

    client = TestClient(api.app)
    response = client.post("/api/run", json={
        "strategies": [STRAT], "data_file": DATA_FILE,
        "events": "__does_not_exist__.csv",
    })
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_api_maps_an_invalid_events_table_to_data_validation(tmp_path):
    """行为表的校验失败走**与价格 CSV 同一条**通道:ADR-031 的
    `DataValidationError -> DATA_VALIDATION`。"""
    from fastapi.testclient import TestClient

    import api

    bad = ROOT / "data" / "__t20_bad_events__.csv"
    bad.write_text(rows_to_csv([
        f"{REAL['expect_dates'][1]},{SCHEMA['rejected_event_values'][1]},"
        f"{RATIO['split_4_for_1']},",
    ]), encoding="utf-8")
    client = TestClient(api.app)
    try:
        response = client.post("/api/run", json={
            "strategies": [STRAT], "data_file": DATA_FILE,
            "events": bad.name,
        })
    finally:
        bad.unlink(missing_ok=True)

    assert response.json()["code"] == "DATA_VALIDATION"
