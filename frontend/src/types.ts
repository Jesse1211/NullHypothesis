/** 逐字段镜像 `api.py` 的 pydantic 模型(ADR-038 的单一真相来源)。
 *
 * **所有金融量都是已完成全部算术的标量**(ADR-025/I8):`total_return_pct`
 * 已乘 100,前端【只追加 `%`】。这里不定义任何派生字段 —— 派生就是算术。
 */

export interface EquityPoint {
  date: string
  equity: number
  /** 当日收盘价(ADR-049)。后端给的原值 —— 前端不自己取价、不做归一化。
   *  买入持有时净值曲线与它形状必须全同,那是一个肉眼可见的正确性检查。 */
  close: number
}

export interface Trade {
  date: string
  side: 'BUY' | 'SELL'
  shares: number
  price: number
  /** `shares × price`,不含手续费。**后端算好的**(ADR-046)——
   *  ADR-025 规定乘除加减一律在后端,前端不得自己乘。 */
  amount: number
  fee: number
  cash_after: number
  shares_after: number
}

export interface Summary {
  start: string
  end: string
  bars: number
  initial_cash: number
  final_equity: number
  total_return_pct: number
  trade_count: number
  /** 无跳空时为 `null`(单行 CSV)—— 按 `NULL_DISPLAY` 渲染,不是 `NaN`。 */
  max_gap_pct: number | null
  max_gap_date: string | null
  mean_abs_gap_pct: number | null
  /** 夏普比率(ADR-058)。**裸比率,不是百分数** —— 按 `formats.ratio`
   *  渲染,前端**不得**给它追加 `%`。
   *
   *  `null` 表示该值在**数学上未定义**(策略不交易、或收益无波动、或样本
   *  不足 2 个收益)—— **不表示 0**。按 `NULL_DISPLAY` 渲染,且界面不得
   *  把它当 0 参与任何比较或排序(ADR-059 的声明逐字覆盖这一点)。 */
  sharpe: number | null
  /** 最大回撤(ADR-058)。**正数表跌幅**、已乘 100(`23.5` == 跌 23.5%)。
   *  按 `percent_unsigned` 渲染 —— 带符号格式化器会引入双重负号的显示 bug。
   *  前端**不取绝对值、不取负**:后端给的就是最终值。 */
  max_drawdown_pct: number | null
  /** 回撤的峰值日 / 谷底日。无回撤时**均为 `null`** —— 给日期等于断言发生过
   *  一次没发生的回撤。**不含恢复日**(ADR-058 明确不做)。 */
  max_drawdown_peak_date: string | null
  max_drawdown_trough_date: string | null
}

export interface StrategyResult {
  strategy: string
  equity: EquityPoint[]
  trades: Trade[]
  summary: Summary
  png_path: string
}

export interface RunResponse {
  run_id: string
  results: StrategyResult[]
  comparison_png_path: string | null
  assumptions: string[]
}

export interface ApiError {
  code: 'DATA_VALIDATION' | 'STRATEGY_ERROR' | 'NOT_FOUND' | 'INVALID_REQUEST'
  message: string
  detail: {
    file?: string
    row?: number | null
    column?: string | null
    strategy?: string | null
    bar_index?: number | null
    date?: string | null
    traceback?: string
    field?: string
    value?: unknown
    run_id?: string
  }
}

export interface RunListItem {
  run_id: string
  strategies: string[]
  data_file: string
  final_equity: number
}

/** `GET /api/runs/{id}` 逐字段等于归档的 `run.json`(ADR-037)。 */
export interface ArchivedRun {
  run_id: string
  request: {
    strategies: string[]
    data_file: string
    cash: number
    fee: number
    /** ADR-053(阶段三 / T20)追加在末尾:行为表文件名,`null` = 无行为表。 */
    events?: string | null
  }
  results: Array<{
    strategy: string
    summary: Summary
    png_path: string
  }>
  assumptions: string[]
  comparison_png_path: string | null
}
