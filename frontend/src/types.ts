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
    /** ADR-056(阶段三 / T22)追加在末尾:滑点率,与 `fee` 同口径的小数
     *  (`0.0005` = 5bps)。省略 = 阶段二的归档(当时还没有这个字段)。
     *
     *  **它必须镜像到这里,而不是止步于后端**:ADR-056 的机制 2
     *  (「报告里显示生效值 —— 每张图、每个 JSON 都自带『这条曲线假设了
     *  多少成本』」)是该 ADR 用来替代「非零默认值」的诚实性论证本身。
     *  后端有、界面无,等于这条论证在 Web 界面上断掉。 */
    slippage?: number
  }
  results: Array<{
    strategy: string
    summary: Summary
    png_path: string
  }>
  assumptions: string[]
  comparison_png_path: string | null
}
