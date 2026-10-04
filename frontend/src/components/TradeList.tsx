/** T13 · 交易清单(由 **ADR-046** 补入 ADR-037b 的组件清单)。
 *
 * 七列与批准稿一致:日期 / 动作 / 股数 / 价格 / 金额 / 成交后现金 / 成交后持股。
 *
 * **「金额」读 `trade.amount`,不是 `shares × price`**(ADR-025 规定乘除加减
 * 一律在后端;该字段由 ADR-046 加在 `Fill` 上)。
 */

import type { StrategyResult } from '../types'
import { integer, money } from '../format'

export const TRADE_COLUMNS = [
  '日期', '动作', '股数', '价格', '金额', '成交后现金', '成交后持股',
] as const

const SIDE_LABEL: Record<string, string> = { BUY: '买入', SELL: '卖出' }

export interface TradeListProps {
  result: StrategyResult
}

export default function TradeList({ result }: TradeListProps) {
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
