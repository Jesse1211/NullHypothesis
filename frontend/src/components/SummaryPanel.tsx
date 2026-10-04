/** T13 · 回执式汇总(等宽、框线对齐)—— 形态取自批准稿(ADR-046)。
 *
 * **I8**:每个数字都**原样**取自后端的 `summary` 字段,只做 ADR-025 允许的
 * 五类格式化。例如交易次数读 `summary.trade_count`,**不是** `trades.length`
 * —— 后者是前端自行推导,而且不含算术,连 grep 都抓不到。
 *
 * 为什么是 `<pre>` 而不是 `<dl>`:这是「一本账」的产物,回执是账的固有形态
 * —— 等宽对齐让数字可以上下对读。批准稿如此,非装饰选择。
 */

import type { StrategyResult } from '../types'
import { integer, money, orNull, percent, percentUnsigned } from '../format'

const BAR = '═'.repeat(43)
const THIN = '─'.repeat(43)

/** 右对齐到固定宽度 —— 纯字符串排版,不是算术。 */
function pad(s: string, w: number): string {
  return s.length >= w ? s : ' '.repeat(w - s.length) + s
}

export interface SummaryPanelProps {
  results: StrategyResult[]
  assumptions: string[]
  /** 本次运行的费率 —— 来自请求,后端原样回传。 */
  fee?: number
}

export default function SummaryPanel({ results, assumptions, fee }: SummaryPanelProps) {
  if (results.length === 0) return null
  // 区间/交易日数/初始资金对同一次运行的所有策略都相同,取第一条。
  const head = results[0].summary

  const lines: Array<{ cls?: string; text: string }[]> = []
  const L = (...parts: { cls?: string; text: string }[]) => lines.push(parts)

  L({ cls: 'hr', text: BAR })
  L({ text: ' ' }, { cls: 'ttl', text: '回 测 结 果' })
  L({ cls: 'hr', text: BAR })
  L({ text: ' 区间        ' }, { cls: 'n', text: `${head.start} ~ ${head.end}` })
  L({ text: ' 交易日数    ' }, { cls: 'n', text: integer(head.bars) })
  L({ text: ' 初始资金    ' }, { cls: 'n', text: money(head.initial_cash) })
  if (fee !== undefined) {
    L(
      { text: ' 手续费率    ' }, { cls: 'n', text: fee.toFixed(4) },
      ...(fee === 0 ? [{ cls: 'hr', text: '  (零成本基准)' }] : []),
    )
  }
  L({ cls: 'hr', text: THIN })

  results.forEach((r, i) => {
    const s = r.summary
    if (i) L({ text: '' })
    L({ text: ' ' }, { cls: 'n', text: r.strategy })
    L({ text: '   期末资产  ' }, { cls: 'n', text: money(s.final_equity) })
    L(
      { text: '   总收益    ' },
      // 符号判断是纯判断,不是算术(ADR-025 第 5 条)
      { cls: s.total_return_pct >= 0 ? 'pos' : 'neg', text: percent(s.total_return_pct) },
    )
    L({ text: '   交易次数  ' }, { cls: 'n', text: pad(integer(s.trade_count), 6) })
  })

  L({ cls: 'hr', text: THIN })
  L(
    { text: ' 最大跳空    ' }, { cls: 'n', text: percent(head.max_gap_pct) },
    { cls: 'hr', text: `  ${orNull(head.max_gap_date)}` },
  )
  L({ text: ' 均值跳空    ' }, { cls: 'n', text: pad(percentUnsigned(head.mean_abs_gap_pct), 7) })
  L({ cls: 'hr', text: THIN })
  L({ cls: 'chk', text: ' ✓ 对账:每日 现金+持股市值 == 总资产' })
  // ADR-021:后端逐字给的两条假设,不得改写
  assumptions.forEach((a) => L({ cls: 'wn', text: ` ⚠ ${a}` }))
  L({ cls: 'hr', text: BAR })

  return (
    <pre className="receipt" aria-label="回测汇总">
      {lines.map((parts, i) => (
        <span key={i}>
          {parts.map((p, j) =>
            p.cls
              ? <span key={j} className={p.cls}>{p.text}</span>
              : <span key={j}>{p.text}</span>,
          )}
          {i < lines.length - 1 ? '\n' : ''}
        </span>
      ))}
    </pre>
  )
}
