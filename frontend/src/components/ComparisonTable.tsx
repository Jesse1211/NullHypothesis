/** T13 · 多策略对比表。
 *
 * **ADR-018:不排名。** 表头无 `onClick`、无 `aria-sort`;表内无
 * `best`/`highlight`/`winner` 类名。并列呈现,由人自己看。
 */

import type { StrategyResult } from '../types'
import { integer, money, percent } from '../format'

export interface ComparisonTableProps {
  results: StrategyResult[]
}

export default function ComparisonTable({ results }: ComparisonTableProps) {
  if (results.length < 2) return null

  return (
    <table className="comparison">
      <thead>
        <tr>
          <th>策略</th>
          <th>期末资产</th>
          <th>总收益</th>
          <th>交易次数</th>
        </tr>
      </thead>
      <tbody>
        {results.map((r) => (
          <tr key={r.strategy}>
            <td>{r.strategy}</td>
            <td>{money(r.summary.final_equity)}</td>
            <td>{percent(r.summary.total_return_pct)}</td>
            <td>{integer(r.summary.trade_count)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
