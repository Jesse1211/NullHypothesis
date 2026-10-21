/** T13 · 「这条曲线的已知局限」—— **结构性区块,不是脚注**(ADR-046)。
 *
 * 批准稿给它的标题是「由设计决定,非遗漏」,位置**紧跟曲线** —— 目的是让人
 * 看曲线时无法忽略它。它是框架那些不可验证前提与**已知偏差**在界面上的
 * 唯一守卫(ADR-021 的 `assumptions` 通道 + ADR-002 的 T+1 无条件成交)。
 *
 * 本区块里**只有一条**是本界面自己的陈述(「不支持做空 / 杠杆 / 限价 /
 * 止损」—— §0 的明确非目标),其余全部来自后端的 `assumptions`,**逐字**
 * 渲染。本界面不含任何数字,故不违反 ADR-025 / I8。
 *
 * ═══ T24 / ADR-055 · ADR-059 的更新 ═══
 * 原文此处写死「**未计滑点** · 不支持做空 / 杠杆 / 限价 / 止损」。
 * ADR-056 之后「未计滑点」**是假的** —— 框架有 `slippage`,且它的全部诚实
 * 边界(规模无关、价差与冲击合并、开盘价差、默认 0 不代表可实现收益)已由
 * ADR-059 的七条声明经 `assumptions` 通道**逐字**送到这里。
 * 继续印那四个字会让屏幕与后端自相矛盾:一边说「未计滑点」,紧邻几行就印着
 * 「滑点为固定 bps:买入按 Open×(1+s)…」。故该说法**退役**,不是补充 ——
 * 同 `frozen_text.assumption_adjusted` 的处置(ADR-055)。
 * 原文注释说「四条里有两条来自后端」—— 条数不再是常数(由
 * `contracts.yaml::assumption_order` 决定),故改为按来源描述。
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
        {/* 本界面自己的唯一一条:§0 的明确非目标。**滑点已不在此列** ——
            它由 ADR-056 实现、由 ADR-059 的声明经上面的 `assumptions`
            逐字声明(T24)。 */}
        <li>
          不支持做空 / 杠杆 / 限价 / 止损 ——{' '}
          <b>此输出不能用来做决定</b>
        </li>
      </ul>
    </section>
  )
}
