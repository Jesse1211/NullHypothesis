/** T13 · 「这条曲线的已知局限」—— **结构性区块,不是脚注**(ADR-046)。
 *
 * 批准稿给它的标题是「由设计决定,非遗漏」,位置**紧跟曲线** —— 目的是让人
 * 看曲线时无法忽略它。它是 ADR-001(不可验证的复权前提)与 ADR-002(T+1
 * 无条件成交)在界面上的唯一守卫。
 *
 * 四条里有两条来自后端的 `assumptions`(逐字,ADR-021),两条是本界面
 * 对 ADR-002/020 与「非目标」的陈述 —— 后者不含任何数字,故不违反 ADR-025。
 */

import type { Summary } from '../types'
import { orNull, percent } from '../format'

export interface LimitationsPanelProps {
  /** 后端逐字给的两条假设(ADR-021),不得改写。 */
  assumptions: string[]
  /** 取最大跳空用 —— 直接读字段,不自己算。 */
  summary: Summary
}

export default function LimitationsPanel({ assumptions, summary }: LimitationsPanelProps) {
  const gap = summary.max_gap_pct
  return (
    <section className="limits" aria-label="已知局限">
      <h4>这条曲线的已知局限 · 由设计决定,非遗漏</h4>
      <ul>
        <li>
          成交假设 <code>T 日 Close 决策 → T+1 日 Open 成交</code>,仅市价单,
          T 日意图在 T+1 开盘<b>无条件执行</b>(ADR-002 / ADR-020)
        </li>
        <li>
          最大单日跳空{' '}
          <code data-field="max_gap">
            {gap === null ? orNull(null) : `${percent(gap)} @ ${orNull(summary.max_gap_date)}`}
          </code>
          {' '}—— 该笔成交实际发生在与决策时相差这么多的价位上
        </li>
        {/* 后端逐字给的两条(ADR-021)—— 不改写、不省略 */}
        {assumptions.map((a) => (
          <li key={a} data-assumption={a}>{a}</li>
        ))}
        <li>
          未计滑点 · 不支持做空 / 杠杆 / 限价 / 止损 ——{' '}
          <b>此输出不能用来做决定</b>
        </li>
      </ul>
    </section>
  )
}
