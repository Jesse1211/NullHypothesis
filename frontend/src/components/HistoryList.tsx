/** T14 · 归档列表 —— **左栏**(ADR-048 的「历史」tab)。
 *
 * 只负责列表。选中那次的 PNG 与汇总归 `ArchiveDetail`(主栏)—— 两栏布局
 * 下一个组件没法同时待在两边,故 ADR-048 把原来横跨两栏的 `HistoryList`
 * 拆成了两个。
 */

import type { RunListItem } from '../types'
import { money } from '../format'

export interface HistoryListProps {
  runs: RunListItem[]
  /** 当前选中的 `run_id` —— 只用来标高亮,不需要整个 `ArchivedRun`。 */
  selectedId: string | null
  onSelect: (runId: string) => void
}

export default function HistoryList({ runs, selectedId, onSelect }: HistoryListProps) {
  return (
    <aside className="rail">
      <span className="lbl">历史 · out/</span>

      {runs.length === 0 ? (
        <div className="emptybox">
          还没有跑过 —— 去「跑一次」跑一回就会出现在这里。
        </div>
      ) : (
        <div>
          {runs.map((r) => (
            <button
              type="button" key={r.run_id}
              className={`hitem${selectedId === r.run_id ? ' cur' : ''}`}
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
    </aside>
  )
}
