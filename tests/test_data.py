"""T1 的验收门:数据加载与校验(ADR-001/004/019/023/040/042)。

**真实数据状态**(T1 门的硬要求):每个测试都把 CSV **真实写到磁盘**,
再用路径调用 `load_csv` —— 不构造内存 DataFrame。内存 DataFrame 会
绕过 `pd.read_csv` 的类型推断与空文件/仅表头行为,而那恰恰是
ADR-019 要校的东西。

blindSpots(T1 门要求声明)—— 下列 CSV 变体**未被测试**:
  * 字符编码:非 UTF-8(GBK/Latin-1)、带 BOM 的 UTF-8
  * 千分位分隔符(`1,234.56`)与其他本地化数字格式
  * 行尾:CRLF / 混合行尾
  * 分隔符变体:分号 / 制表符 / 引号包裹字段
  * 超大文件的内存与耗时行为
以上均未在 ADR 中给出契约,故本任务不替设计做决定;若将来要支持,
需要先有一条 ADR 钉死行为,再加门。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from nullhypothesis.data import UnsortedDateWarning, load_csv
from nullhypothesis.errors import DataValidationError, StrategyError

# ───────────────────────────── 真实磁盘 fixture ─────────────────────────────

GOOD_CSV = """Date,Open,High,Low,Close,Volume
2020-01-02,100.0,105.0,99.0,104.0,1000
2020-01-03,104.0,108.0,103.0,107.0,1100
2020-01-06,107.0,110.0,106.0,109.0,1200
"""


def write_csv(tmp_path: Path, text: str, name: str = "prices.csv") -> Path:
    """把 CSV 文本真实写到磁盘,返回其路径。"""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    assert path.is_file(), "fixture 必须真实落盘"
    return path


# ─────────────────────────────── 正常载入 ───────────────────────────────


def test_load_good_csv_returns_dataframe(tmp_path: Path) -> None:
    path = write_csv(tmp_path, GOOD_CSV)
    df = load_csv(path)

    assert isinstance(df, pd.DataFrame)
    # ADR-042:交易日数 == 校验后的行数
    assert len(df) == 3
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    # index 为 0..n-1 连续
    assert list(df.index) == [0, 1, 2]
    assert df["Close"].tolist() == [104.0, 107.0, 109.0]


def test_load_csv_accepts_str_path(tmp_path: Path) -> None:
    path = write_csv(tmp_path, GOOD_CSV)
    assert len(load_csv(str(path))) == 3


def test_volume_optional(tmp_path: Path) -> None:
    """ADR-001:`Volume` 可选 —— 缺它不是错误。"""
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n"
        "2020-01-02,100.0,105.0,99.0,104.0\n"
        "2020-01-03,104.0,108.0,103.0,107.0\n",
    )
    df = load_csv(path)
    assert "Volume" not in df.columns
    assert len(df) == 2


# ───────────────── ADR-019 六种失败情形,每条一个测试 ─────────────────
# 全部断言:异常类型 DataValidationError(ADR-040)+ 消息含行号/列名
# + `.file`/`.row`/`.column` 字段正确。


def test_missing_required_column(tmp_path: Path) -> None:
    """情形 1:缺必须列 —— 指明缺哪列。"""
    path = write_csv(
        tmp_path,
        "Date,Open,High,Close\n2020-01-02,100.0,105.0,104.0\n",
    )
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path)

    err = excinfo.value
    assert "Low" in str(err)
    assert err.file == str(path)
    assert err.column == "Low"
    assert err.row is None          # 整表级失败,与具体行无关


def test_missing_multiple_columns_reported_at_once(tmp_path: Path) -> None:
    """ADR-019「一次校完」:两列同时缺 → 一条异常里都出现。"""
    path = write_csv(tmp_path, "Date,Open,Close\n2020-01-02,100.0,104.0\n")
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path)
    message = str(excinfo.value)
    assert "High" in message and "Low" in message


def test_nan_in_ohlc(tmp_path: Path) -> None:
    """情形 2:OHLC 含 NaN —— 指明行号 + 列名。"""
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n"
        "2020-01-02,100.0,105.0,99.0,104.0\n"
        "2020-01-03,104.0,,103.0,107.0\n",
    )
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path)

    err = excinfo.value
    message = str(err)
    assert "row=1" in message            # 行号在消息里
    assert "column=High" in message      # 列名在消息里
    assert err.file == str(path)
    assert err.row == 1
    assert err.column == "High"


def test_non_positive_price(tmp_path: Path) -> None:
    """情形 3:价格 <= 0 —— 指明行号 + 列名 + 值。"""
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n"
        "2020-01-02,100.0,105.0,99.0,104.0\n"
        "2020-01-03,104.0,108.0,-3.5,107.0\n",
    )
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path)

    err = excinfo.value
    message = str(err)
    assert "row=1" in message
    assert "column=Low" in message
    assert "-3.5" in message             # 值也在消息里
    assert (err.file, err.row, err.column) == (str(path), 1, "Low")


def test_zero_price_is_rejected(tmp_path: Path) -> None:
    """`<= 0` 含 0 —— 0 价是 ADR-019 要拦的边界,不是合法价。"""
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n2020-01-02,100.0,105.0,0.0,104.0\n",
    )
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path)
    assert excinfo.value.column == "Low"
    assert excinfo.value.row == 0


def test_duplicate_dates(tmp_path: Path) -> None:
    """情形 4:重复日期 —— 指明日期 + 出现次数。"""
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n"
        "2020-01-02,100.0,105.0,99.0,104.0\n"
        "2020-01-02,101.0,106.0,100.0,105.0\n"
        "2020-01-03,104.0,108.0,103.0,107.0\n",
    )
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path)

    err = excinfo.value
    message = str(err)
    assert "2020-01-02" in message       # 日期
    assert "occurrences=2" in message    # 出现次数
    assert "column=Date" in message
    assert err.file == str(path)
    assert err.column == "Date"
    assert err.row == 0


def test_empty_file(tmp_path: Path) -> None:
    """情形 5a:空文件(零字节)。"""
    path = write_csv(tmp_path, "")
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path)
    err = excinfo.value
    assert "空文件" in str(err)
    assert err.file == str(path)
    assert err.row is None and err.column is None


def test_header_only(tmp_path: Path) -> None:
    """情形 5b:仅表头,无数据行。"""
    path = write_csv(tmp_path, "Date,Open,High,Low,Close,Volume\n")
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path)
    err = excinfo.value
    assert "仅表头" in str(err)
    assert err.file == str(path)
    assert err.row is None


def test_unsorted_dates_are_sorted_with_notice(tmp_path: Path) -> None:
    """情形 6:日期非单调递增 → 排序 + 提示,**不抛错**。

    并断言排序后 `df.index` 为 `0..n-1` 连续 —— pandas 排序留下的陈旧
    RangeIndex 会让 `iloc[i]` 与 `loc[i]` 分叉,之后每个 `i+1` 查找都读错 bar。
    """
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n"
        "2020-01-06,107.0,110.0,106.0,109.0\n"
        "2020-01-02,100.0,105.0,99.0,104.0\n"
        "2020-01-03,104.0,108.0,103.0,107.0\n",
    )

    with pytest.warns(UnsortedDateWarning):
        df = load_csv(path)

    # 已排序
    assert df["Date"].is_monotonic_increasing
    assert [d.strftime("%Y-%m-%d") for d in df["Date"]] == [
        "2020-01-02",
        "2020-01-03",
        "2020-01-06",
    ]
    # index 为 0..n-1 连续
    assert list(df.index) == [0, 1, 2]
    assert df.index.tolist() == list(range(len(df)))
    # iloc 与 loc 不分叉 —— 这才是重建 index 的真正理由
    for i in range(len(df)):
        assert df.iloc[i]["Close"] == df.loc[i, "Close"]
    assert df["Close"].tolist() == [104.0, 107.0, 109.0]


def test_sorted_csv_emits_no_notice(tmp_path: Path) -> None:
    """已排序的 CSV 不得发出排序提示 —— 提示必须有信息量。"""
    path = write_csv(tmp_path, GOOD_CSV)
    import warnings as _warnings

    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        load_csv(path)
    assert not [w for w in caught if issubclass(w.category, UnsortedDateWarning)]


def test_unsorted_csv_equals_hand_sorted_csv(tmp_path: Path) -> None:
    """ADR-019 排序后等价:乱序 CSV 与手工排序版载入结果逐点相等。"""
    unsorted = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n"
        "2020-01-03,104.0,108.0,103.0,107.0\n"
        "2020-01-06,107.0,110.0,106.0,109.0\n"
        "2020-01-02,100.0,105.0,99.0,104.0\n",
        name="unsorted.csv",
    )
    hand_sorted = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n"
        "2020-01-02,100.0,105.0,99.0,104.0\n"
        "2020-01-03,104.0,108.0,103.0,107.0\n"
        "2020-01-06,107.0,110.0,106.0,109.0\n",
        name="sorted.csv",
    )

    with pytest.warns(UnsortedDateWarning):
        left = load_csv(unsorted)
    right = load_csv(hand_sorted)

    pd.testing.assert_frame_equal(left, right)


# ───────────────────────── ADR-023:单行 CSV ─────────────────────────


def test_single_row_csv_loads(tmp_path: Path) -> None:
    """ADR-023:仅 1 个交易日 → 允许载入,不报错。"""
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close,Volume\n2020-01-02,100.0,105.0,99.0,104.0,1000\n",
    )
    df = load_csv(path)
    assert len(df) == 1
    assert list(df.index) == [0]
    assert df.iloc[0]["Close"] == 104.0


# ══════════════ ADR-004 的三条老门 —— 已按 ADR-050 **反写** ══════════════
#
# **ADR-004 由 ADR-050 全部作废。** 这三条门原先断言「该列不被读取、
# 加不加结果相同、该列的 NaN/负值对校验不可见」—— ADR-050 之后它们会把
# **正确**的新行为判为失败(T21 门 16 要求反写它们)。
#
# 反写的方向逐条对应 ADR-050 的裁决表:
#   * 主张 A(列为 `Date,Open,High,Low,Close[,Volume]`)**保留**
#     → 「该列不出现在返回帧里」这一半**仍然断言**;
#   * 主张 B/C(框架内无复权机制 / 修正只能在框架外)**被推翻**
#     → 「不影响任何计算」反写为「**确实**由它算出因子并调整了价格」。
#
# 这不是把门改松:新门比老门**更严** —— 老门只要求「别读」,
# 新门要求「读了、算对了、且算出的因子作用在四列上」。


def test_adj_close_is_absent_from_the_returned_frame(tmp_path: Path) -> None:
    """**反写第一条**(T21 门 8①)。ADR-001 主张 A 的残留部分**仍然有效**:
    `Adj Close` **不出现**在返回的 DataFrame 里。

    这一半没有被 ADR-050 推翻,理由是结构性的:该列是**复权价**,
    `Open/High/Low/Close` 是**未复权原始价**,两列不在同一个价格体系里。
    让它留在表里等于给下游一个把两种单位混用的机会。

    本 fixture 的 `Adj Close/Close` 比值**逐行恒为 0.5** —— 归一化后
    累积因子处处为 `1.0`,故价格**恰好**不变。它同时门住了一件事:
    「一个常数比例的 `Adj Close`(即区间内无任何事件)**不得**改动价格」。
    """
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close,Adj Close,Volume\n"
        "2020-01-02,100.0,105.0,99.0,104.0,52.0,1000\n"
        "2020-01-03,104.0,108.0,103.0,107.0,53.5,1100\n",
    )
    df = load_csv(path)

    # 该列不在返回的表里 —— 下游结构上无法读到它(ADR-001 主张 A)
    assert "Adj Close" not in df.columns
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    # 比值恒定(区间内无事件)→ 因子恒为 1.0 → OHLC 逐值等于 CSV 原值
    assert df["Close"].tolist() == [104.0, 107.0]
    assert df["Open"].tolist() == [100.0, 104.0]


def test_adj_close_does_change_the_result(tmp_path: Path) -> None:
    """**反写第二条**(T21 门 8②)—— 老门断言「加/不加结果相同」,
    ADR-050 把那条主张(B/C)**推翻**了:现在必须**不同**。

    这条门是「先读取用于算因子,再从返回帧中移除」那个**顺序**的唯一
    可执行投影:`data.py` 原先的 `EXCLUDED_COLUMNS` 是**读取前就 drop**
    (注释自述「先丢弃再校验…结构上不可能影响结果」)——
    **若实现保留了 drop-before-read,本断言必红**。

    `Adj Close/Close` 取 `1.0/104` 与 `2.0/107`(比值**非**常数)→
    归一化后首行累积因子 != 1.0,故首行价格**必须**被调整,
    而**末行**逐值等于原值(后复权,ADR-050)。
    """
    without = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n"
        "2020-01-02,100.0,105.0,99.0,104.0\n"
        "2020-01-03,104.0,108.0,103.0,107.0\n",
        name="plain.csv",
    )
    with_adj = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close,Adj Close\n"
        "2020-01-02,100.0,105.0,99.0,104.0,1.0\n"
        "2020-01-03,104.0,108.0,103.0,107.0,2.0\n",
        name="with_adj.csv",
    )
    plain, adjusted = load_csv(without), load_csv(with_adj)

    # 形状与列名不变 —— 下游无从分辨(ADR-050 的结构性保证)
    assert list(plain.columns) == list(adjusted.columns)
    assert len(plain) == len(adjusted)

    # **但价格不再相同** —— 这正是 ADR-050 推翻主张 B/C 的可观测形式
    assert adjusted["Close"].tolist() != plain["Close"].tolist()

    # 因子确实由该列算出:factor_t = AdjClose_t/Close_t,按末行归一化
    f0 = (1.0 / 104.0) / (2.0 / 107.0)
    assert adjusted["Close"].iloc[0] == pytest.approx(104.0 * f0)
    assert adjusted["Open"].iloc[0] == pytest.approx(100.0 * f0)
    assert adjusted["High"].iloc[0] == pytest.approx(105.0 * f0)
    assert adjusted["Low"].iloc[0] == pytest.approx(99.0 * f0)

    # 后复权:**末行**逐值等于 CSV 原值(cumfactor == 1.0)
    assert adjusted["Close"].iloc[-1] == pytest.approx(107.0)
    assert adjusted["Open"].iloc[-1] == pytest.approx(104.0)


def test_adj_close_garbage_falls_back_to_no_adjustment(tmp_path: Path) -> None:
    """**反写第三条**。老门断言「该列的 NaN/负值对校验不可见」——
    它是 ADR-004「先 drop 再校验」的直接推论,而那个顺序已被 ADR-050 改掉。

    反写后的断言与 ADR-050 一致,且**仍不报错**:该列**不是** OHLC,
    ADR-019 的 NaN/非正校验只作用在 `PRICE_COLUMNS` 上(那一条没变)。
    一列废数据不该让一份本来能跑的价格文件跑不起来(§7 第 1 条的加性原则)
    —— 故路径一判定为「不可用」并**退化为缺席**,价格不被调整。

    这与 ADR-052 的表一致:路径一缺席且无行为表 → 不调价 + 跳空体检。
    本 fixture 的两根 bar 跳空为 `104→104` 的 0%,故**不触发**体检提示。
    """
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close,Adj Close\n"
        "2020-01-02,100.0,105.0,99.0,104.0,\n"
        "2020-01-03,104.0,108.0,103.0,107.0,-999.0\n",
    )
    df = load_csv(path)

    assert len(df) == 2
    assert "Adj Close" not in df.columns
    # 废数据 → 路径一不可用 → 不调价(价格逐值等于 CSV 原值)
    assert df["Close"].tolist() == [104.0, 107.0]
    assert df["Open"].tolist() == [100.0, 104.0]


# ───────────────────────── 其他载入期失败 ─────────────────────────


def test_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.csv"
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(missing)
    assert excinfo.value.file == str(missing)


def test_unparseable_date(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n"
        "2020-01-02,100.0,105.0,99.0,104.0\n"
        "not-a-date,104.0,108.0,103.0,107.0\n",
    )
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path)
    err = excinfo.value
    assert "row=1" in str(err)
    assert err.column == "Date"
    assert err.row == 1


def test_non_numeric_price_is_nan_case(tmp_path: Path) -> None:
    """非数值价格走 NaN 分支 —— 消息仍含行号 + 列名。"""
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n2020-01-02,abc,105.0,99.0,104.0\n",
    )
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path)
    err = excinfo.value
    assert "row=0" in str(err) and "column=Open" in str(err)
    assert (err.row, err.column) == (0, "Open")


def test_all_problems_reported_in_one_exception(tmp_path: Path) -> None:
    """ADR-019 的核心:一次校完,全部响亮失败 —— 不是「报第一个就停」。"""
    path = write_csv(
        tmp_path,
        "Date,Open,High,Low,Close\n"
        "2020-01-02,100.0,105.0,-1.0,104.0\n"
        "2020-01-03,,108.0,103.0,107.0\n"
        "2020-01-03,104.0,108.0,103.0,0.0\n",
    )
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path)
    message = str(excinfo.value)
    assert "row=0" in message and "column=Low" in message     # 负价
    assert "row=1" in message and "column=Open" in message    # NaN
    assert "row=2" in message and "column=Close" in message   # 0 价
    assert "occurrences=2" in message                         # 重复日期
    # 至少四处问题被同时报出
    assert message.count("  - ") >= 4


# ───────────────────────── ADR-040:错误类型契约 ─────────────────────────


def test_data_validation_error_is_value_error() -> None:
    """ADR-019 的行为表写 `ValueError`,ADR-040 把它收窄为具名子类。"""
    assert issubclass(DataValidationError, ValueError)


def test_data_validation_error_fields() -> None:
    err = DataValidationError("boom", file="f.csv", row=7, column="Close")
    assert (err.file, err.row, err.column) == ("f.csv", 7, "Close")
    assert str(err) == "boom"


def test_strategy_error_contract() -> None:
    """ADR-040:`StrategyError` 由 T1 一次写全(T2/T8 只 import,不新建)。"""
    assert issubclass(StrategyError, RuntimeError)
    err = StrategyError("boom", strategy="ma_cross", bar_index=42, date="2015-03-02")
    assert (err.strategy, err.bar_index, err.date) == ("ma_cross", 42, "2015-03-02")


def test_strategy_error_carries_cause() -> None:
    """ADR-022:原始异常由 `__cause__` 携带,traceback 不丢。"""
    try:
        try:
            1 / 0
        except ZeroDivisionError as exc:
            raise StrategyError("策略抛出异常", strategy="s", bar_index=1, date="d") from exc
    except StrategyError as err:
        assert isinstance(err.__cause__, ZeroDivisionError)
    else:  # pragma: no cover - 上面必然抛出
        pytest.fail("StrategyError 未抛出")


def test_errors_module_owns_both_classes() -> None:
    """§4.5 归属矩阵:两个类都在 `nullhypothesis.errors` 里,一次写全。"""
    import nullhypothesis.errors as errors

    assert hasattr(errors, "DataValidationError")
    assert hasattr(errors, "StrategyError")
