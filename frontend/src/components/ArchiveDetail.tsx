/** T14 · 归档详情 —— **主栏**(由 ADR-048 从 `HistoryList` 拆出)。
 *
 * **ADR-032**:归档项**只显示 PNG 快照**,不重建交互图 —— 归档的意义是
 * 「当时跑出来就是这样」,用当时那张图,而不是用今天的代码重画一遍。
 * 图像 URL 走专用端点(ADR-045),不是 `/out/...` 静态路径。
 */

import type { ArchivedRun } from '../types'
import { integer, money, percent, pngUrl } from '../format'

export interface ArchiveDetailProps {
  run: ArchivedRun | null
}

export default function ArchiveDetail({ run }: ArchiveDetailProps) {
  if (!run) {
    return (
      <div className="emptybox" style={{ padding: '44px 20px' }}>
        <div style={{ fontSize: 13, color: 'var(--ink-2)', marginBottom: 6 }}>
          选一次归档
        </div>
        <div>左边点一条,这里显示当时那次跑出来的曲线与汇总。</div>
      </div>
    )
  }

  return (
    <div className="archive-detail">
      <div className="zhead">
        <span className="ztitle">{run.run_id}</span>
        <span className="zsub">
          {run.request.data_file} · 初始 {money(run.request.cash)} · 费率 {run.request.fee}
        </span>
      </div>

      {run.results.map((r) => (
        <figure key={r.strategy}>
          <figcaption>
            {r.strategy} · 期末 {money(r.summary.final_equity)}
            {' · '}{percent(r.summary.total_return_pct)}
            {' · '}{integer(r.summary.trade_count)} 笔
          </figcaption>
          {/* 归档快照,不是 Recharts(ADR-032);URL 见 ADR-045 */}
          <img src={pngUrl(run.run_id, r.png_path)} alt={`${r.strategy} 净值曲线`} />
        </figure>
      ))}

      {run.comparison_png_path && (
        <figure>
          <figcaption>对比</figcaption>
          <img src={pngUrl(run.run_id, run.comparison_png_path)} alt="多策略对比" />
        </figure>
      )}

      {/* ADR-021:归档也要带着那两条假设 —— 它们是读这些数字的前提 */}
      <ul className="assumptions">
        {run.assumptions.map((a) => <li key={a}>⚠ {a}</li>)}
      </ul>
    </div>
  )
}
