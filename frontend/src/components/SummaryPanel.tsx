/** T13 · 汇总面板。
 *
 * **I8**:每个数字都**原样**取自后端的 `summary` 字段,只做 ADR-025 允许的
 * 五类格式化。例如交易次数读 `summary.trade_count`,**不是** `trades.length`
 * —— 那是前端自行推导,而且不含算术,连 grep 都抓不到。
 */

import type { StrategyResult } from '../types'
import { integer, money, orNull, percent, percentUnsigned } from '../format'

export interface SummaryPanelProps {
  result: StrategyResult
  assumptions: string[]
}

export default function SummaryPanel({ result, assumptions }: SummaryPanelProps) {
  const s = result.summary
  const rows: Array<[string, string]> = [
    ['区间', `${s.start} … ${s.end}`],
    ['交易日数', integer(s.bars)],
    ['初始资金', money(s.initial_cash)],
    ['期末资产', money(s.final_equity)],
    ['总收益', percent(s.total_return_pct)],
    ['交易次数', integer(s.trade_count)],
    ['最大跳空', percent(s.max_gap_pct)],
    ['最大跳空日期', orNull(s.max_gap_date)],
    ['平均绝对跳空', percentUnsigned(s.mean_abs_gap_pct)],
  ]

  return (
    <section className="summary" aria-label={`${result.strategy} 汇总`}>
      <h3>{result.strategy}</h3>
      <dl>
        {rows.map(([k, v]) => (
          <div key={k} className="row">
            <dt>{k}</dt>
            <dd data-field={k}>{v}</dd>
          </div>
        ))}
      </dl>

      {/* ADR-021:这两条是 ADR-001 那条不可验证前提的唯一守卫,
          Web 界面是非作者实际使用的界面,不得静默丢失。 */}
      <ul className="assumptions">
        {assumptions.map((a) => (
          <li key={a}>⚠ {a}</li>
        ))}
      </ul>
    </section>
  )
}
