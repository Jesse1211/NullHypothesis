/** T14 · 右栏历史(`histo`)+ 归档详情。
 *
 * **ADR-032**:归档项**只显示 PNG 快照**,不重建交互图 —— 归档的意义是
 * 「当时跑出来就是这样」,用当时那张图,而不是用今天的代码重画一遍。
 * 图像 URL 走专用端点(ADR-045),不是 `/out/...` 静态路径。
 */

import type { ArchivedRun, RunListItem } from '../types'
import { money, pngUrl } from '../format'

export interface HistoryListProps {
  runs: RunListItem[]
  selected: ArchivedRun | null
  onSelect: (runId: string) => void
}

export default function HistoryList({ runs, selected, onSelect }: HistoryListProps) {
  return (
    <aside className="histo">
      <span className="lbl">历史 · out/</span>

      {runs.length === 0 ? (
        <div className="emptybox">
          还没有跑过 —— 上面跑一次就会出现在这里。
        </div>
      ) : (
        <div>
          {runs.map((r) => (
            <button
              type="button" key={r.run_id}
              className={`hitem${selected?.run_id === r.run_id ? ' cur' : ''}`}
              onClick={() => onSelect(r.run_id)}
            >
              <div className="ts">{r.run_id}</div>
              <div className="st">
                {r.strategies.join(', ')}<br />{r.data_file}
              </div>
              {/* 列表口径:多策略时取 results[0](ADR-038) */}
              <div className="fin">→ {money(r.final_equity)}</div>
            </button>
          ))}
        </div>
      )}

      <p className="hint">
        扫描 <code>out/</code> 的时间戳目录(ADR-024/032)。
        点开看当时的归档 PNG 与汇总 —— 不重建交互图。
      </p>

      {selected && (
        <div className="archive-detail">
          <span className="lbl">{selected.run_id}</span>
          {selected.results.map((r) => (
            <figure key={r.strategy}>
              <figcaption>
                {r.strategy} · 期末 {money(r.summary.final_equity)}
              </figcaption>
              {/* 归档快照,不是 Recharts(ADR-032);URL 见 ADR-045 */}
              <img
                src={pngUrl(selected.run_id, r.png_path)}
                alt={`${r.strategy} 净值曲线`}
              />
            </figure>
          ))}
          {selected.comparison_png_path && (
            <figure>
              <figcaption>对比</figcaption>
              <img
                src={pngUrl(selected.run_id, selected.comparison_png_path)}
                alt="多策略对比"
              />
            </figure>
          )}
        </div>
      )}
    </aside>
  )
}
