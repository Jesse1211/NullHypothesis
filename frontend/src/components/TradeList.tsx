/** T13 · 交易清单(由 **ADR-046** 补入 ADR-037b 的组件清单)。
 *
 * 七列与批准稿一致:日期 / 动作 / 股数 / 价格 / 金额 / 成交后现金 / 成交后持股。
 *
 * **「金额」读 `trade.amount`,不是 `shares × price`**(ADR-025 规定乘除加减
 * 一律在后端;该字段由 ADR-046 加在 `Fill` 上)。
 *
 * 多策略时上方渲染一排**可切换的策略名**(ADR-047)。此前只显示
 * `results[0]` 且界面上看不出另一条被省了 —— 静默丢信息比显式不显示更糟。
 */

import { useState } from 'react'
import type { StrategyResult } from '../types'
import { integer, money } from '../format'

export const TRADE_COLUMNS = [
  '日期', '动作', '股数', '价格', '金额', '成交后现金', '成交后持股',
] as const

const SIDE_LABEL: Record<string, string> = { BUY: '买入', SELL: '卖出' }

export interface TradeListProps {
  /** 全部策略 —— 由本组件自己管「看哪一条」,App 不需要持这个状态
   *  (它不是跨组件共享的,也不影响任何请求)。 */
  results: StrategyResult[]
}

export default function TradeList({ results }: TradeListProps) {
  const [active, setActive] = useState(0)

  if (results.length === 0) return null
  const idx = Math.min(active, results.length - 1)
  const result = results[idx]
  const t = result.trades

  return (
    <section className="zone" aria-label="交易清单">
      <div className="zhead">
        <span className="ztitle">交易清单</span>
        {/* 笔数读 summary.trade_count,**不是** trades.length(ADR-025)。 */}
        <span className="zsub" data-field="trade_count">
          {result.strategy} · {integer(result.summary.trade_count)} 笔
        </span>
      </div>

      {/* 单策略不渲染切换条 —— 一个选项的选择器是噪声 */}
      {results.length > 1 && (
        <div className="tabs" role="tablist" aria-label="按策略查看交易">
          {results.map((r, i) => (
            <button
              type="button"
              key={r.strategy}
              role="tab"
              aria-selected={i === idx}
              className={`tab${i === idx ? ' on' : ''}`}
              onClick={() => setActive(i)}
            >
              {r.strategy}
              <span className="cnt">{integer(r.summary.trade_count)}</span>
            </button>
          ))}
        </div>
      )}

      {t.length === 0 ? (
        <div className="emptybox">
          该策略全程未开仓 —— <code>trades.csv</code> 仅有表头
        </div>
      ) : (
        <div className="tblwrap">
          <table>
            <thead>
              <tr>{TRADE_COLUMNS.map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {t.map((x, i) => (
                <tr key={`${x.date}-${i}`}>
                  <td>{x.date}</td>
                  <td>{SIDE_LABEL[x.side] ?? x.side}</td>
                  <td className="v">{integer(x.shares)}</td>
                  <td className="v">{money(x.price)}</td>
                  {/* 后端算好的成交额(ADR-046)—— 前端零乘法 */}
                  <td className="v" data-field="amount">{money(x.amount)}</td>
                  <td className="v">{money(x.cash_after)}</td>
                  <td className="v">{integer(x.shares_after)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
