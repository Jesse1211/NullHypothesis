/** 逐字段镜像 `api.py` 的 pydantic 模型(ADR-038 的单一真相来源)。
 *
 * **所有金融量都是已完成全部算术的标量**(ADR-025/I8):`total_return_pct`
 * 已乘 100,前端【只追加 `%`】。这里不定义任何派生字段 —— 派生就是算术。
 */

export interface EquityPoint {
  date: string
  equity: number
}

export interface Trade {
  date: string
  side: 'BUY' | 'SELL'
  shares: number
  price: number
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
  }
  results: Array<{
    strategy: string
    summary: Summary
    png_path: string
  }>
  assumptions: string[]
  comparison_png_path: string | null
}
