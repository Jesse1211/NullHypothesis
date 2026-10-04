/** T14 · 历史列表 + 归档详情。
 *
 * **ADR-032**:归档项**只显示 PNG 快照**,不重建交互图 —— 归档的意义是
 * 「当时跑出来就是这样」,用当时的那张图,而不是用今天的代码重画一遍。
 */

import type { ArchivedRun, RunListItem } from '../types'
import { money } from '../format'

export interface HistoryListProps {
  runs: RunListItem[]
  selected: ArchivedRun | null
  onSelect: (runId: string) => void
}

export default function HistoryList({ runs, selected, onSelect }: HistoryListProps) {
  return (
    <section className="history">
      <h2>历史</h2>

      {runs.length === 0 ? (
        <p className="empty">还没有跑过 —— 上面跑一次就会出现在这里。</p>
      ) : (
        <ul className="run-list">
          {runs.map((r) => (
            <li key={r.run_id}>
              <button onClick={() => onSelect(r.run_id)}>
                <span className="run-id">{r.run_id}</span>
                <span className="strategies">{r.strategies.join(' / ')}</span>
                <span className="data-file">{r.data_file}</span>
                <span className="equity">{money(r.final_equity)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <div className="archive-detail">
          <h3>{selected.run_id}</h3>
          <dl>
            <dt>数据</dt><dd>{selected.request.data_file}</dd>
            <dt>初始资金</dt><dd>{money(selected.request.cash)}</dd>
            <dt>费率</dt><dd>{selected.request.fee}</dd>
          </dl>

          {selected.results.map((r) => (
            <figure key={r.strategy}>
              <figcaption>{r.strategy} · 期末 {money(r.summary.final_equity)}</figcaption>
              {/* 归档快照,不是 Recharts(ADR-032) */}
              <img src={`/${r.png_path}`} alt={`${r.strategy} 净值曲线`} />
            </figure>
          ))}

          {selected.comparison_png_path && (
            <figure>
              <figcaption>对比</figcaption>
              <img src={`/${selected.comparison_png_path}`} alt="多策略对比" />
            </figure>
          )}
        </div>
      )}
    </section>
  )
}
