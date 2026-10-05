/** T13 · 多策略并排对比(五列,含最大跳空 —— 批准稿如此)。
 *
 * **ADR-018:不排名。** 表头无 `onClick`、无 `aria-sort`;表内无
 * `best`/`highlight`/`winner` 类名。并列陈述数字,由人自己看。
 */

import type { StrategyResult } from '../types'
import { integer, money0, orNull, percent } from '../format'
import { seriesColor } from './EquityChart'

export interface ComparisonTableProps {
  results: StrategyResult[]
}

export default function ComparisonTable({ results }: ComparisonTableProps) {
  if (results.length < 2) return null

  return (
    <section className="zone" aria-label="并排对比">
      <div className="zhead">
        <span className="ztitle">并排对比</span>
        {/* 多策略时本表是按策略数字的**唯一**落点(回执只留整次运行的属性),
            故把区间与交易日数带上 —— 否则这些数字失去了时间上下文。 */}
        <span className="zsub">
          {results[0].summary.start} → {results[0].summary.end}
          {' · '}{integer(results[0].summary.bars)} 个交易日 · 仅陈述数字
        </span>
      </div>
      <div className="tblwrap">
        <table className="comparison">
          <thead>
            <tr>
              <th>策略</th><th>期末资产</th><th>总收益</th>
              <th>交易次数</th><th>最大跳空</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r, i) => (
              <tr key={r.strategy}>
                <td>
                  <span className="tag">
                    <span className="k" style={{ background: seriesColor(i) }} />
                    {r.strategy}
                  </span>
                </td>
                <td className="v">{money0(r.summary.final_equity)}</td>
                <td className="v">{percent(r.summary.total_return_pct)}</td>
                <td className="v">{integer(r.summary.trade_count)}</td>
                <td>
                  {r.summary.max_gap_pct === null
                    ? orNull(null)
                    : percent(r.summary.max_gap_pct)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="nonote">
          无排序、无排名、无「最佳」标记(ADR-018 / ADR-029)。
          结论的限定词只能是「在这段历史上」。
        </div>
      </div>
    </section>
  )
}
