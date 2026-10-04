/** T13 · Recharts 净值曲线。只接 props,不自己取数。
 *
 * **I9**:画的 `data` 就是响应里的 `equity` 序列,不做任何变换 —— 不降采样、
 * 不归一化、不改字段名。门断言点数 `== len(equity)` 且逐点相等。
 */

import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import type { StrategyResult } from '../types'
import { money } from '../format'

const COLORS = ['#2563eb', '#dc2626', '#16a34a', '#ca8a04', '#9333ea']

export interface EquityChartProps {
  results: StrategyResult[]
}

export default function EquityChart({ results }: EquityChartProps) {
  if (results.length === 0) return null

  // 多条策略共享同一组交易日(同一份 CSV),按 date 合并成一张宽表。
  // 这是**重排**,不是算术:每个 equity 值原样搬运,不参与任何运算。
  const byDate = new Map<string, Record<string, string | number>>()
  for (const r of results) {
    for (const pt of r.equity) {
      let row = byDate.get(pt.date)
      if (!row) {
        row = { date: pt.date }
        byDate.set(pt.date, row)
      }
      row[r.strategy] = pt.equity
    }
  }
  const data = Array.from(byDate.values())

  return (
    // ResponsiveContainer 需要**父容器有确定高度**,否则在真实浏览器里
    // 渲染为零宽白图(jsdom 无布局引擎,看不见这个)—— 故这里写死 height。
    <div className="chart" style={{ width: '100%', height: 420 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 24, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" minTickGap={48} />
          <YAxis tickFormatter={(v: number) => money(v)} width={96} />
          <Tooltip formatter={(v) => money(Number(v))} />
          <Legend />
          {results.map((r, i) => (
            <Line
              key={r.strategy}
              type="monotone"
              dataKey={r.strategy}
              stroke={COLORS[i % COLORS.length]}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
