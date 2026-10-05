/** T13 · Recharts 净值曲线。只接 props,不自己取数。
 *
 * **I9**:`data` 就是响应里的 `equity` 序列,不降采样、不归一化、不改字段名。
 * 门断言点数 `== len(equity)` 且逐点相等。
 *
 * 交互对齐已批准的预览稿(ADR-046):图例可点击切换显隐并显示当前值、
 * hover 十字线 + 读数框、末点圆点、初始资金虚线基准。
 */

import { useState } from 'react'
import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import type { StrategyResult } from '../types'
import { money, money0 } from '../format'
import { cssVar } from '../theme'

/** 与预览稿一致:第一条用 --accent,第二条用 --series-2。 */
export const SERIES_VARS = ['--accent', '--series-2', '--ok', '--warn']

/** 第 i 条策略的颜色 —— 超过 SERIES_VARS 长度后循环复用。 */
export function seriesColor(i: number): string {
  return cssVar(SERIES_VARS[i % SERIES_VARS.length])
}

export interface EquityChartProps {
  results: StrategyResult[]
  /** 初始资金 —— 画基准虚线用。读 summary.initial_cash,不自己推导。 */
  initialCash: number
}

interface TipPayloadItem {
  dataKey?: string | number
  value?: number | string
}

function Tip(props: {
  active?: boolean
  label?: string | number
  payload?: TipPayloadItem[]
}) {
  if (!props.active || !props.payload?.length) return null
  return (
    <div className="nh-tip">
      <div className="d">{String(props.label ?? '')}</div>
      {props.payload.map((p) => (
        <div className="r" key={String(p.dataKey)}>
          <span>{String(p.dataKey)}</span>
          <b>{money(Number(p.value))}</b>
        </div>
      ))}
    </div>
  )
}

export default function EquityChart({ results, initialCash }: EquityChartProps) {
  const [hidden, setHidden] = useState<Record<string, boolean>>({})

  if (results.length === 0) return null

  const visible = results
    .map((result, index) => ({ result, index }))
    .filter(({ result }) => !hidden[result.strategy])

  // 多条策略共享同一组交易日(同一份 CSV),按 date 合并成一张宽表。
  // 这是**重排**,不是算术:每个 equity 值原样搬运。
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

  function toggle(name: string) {
    // 至少留一条 —— 全部隐藏会得到一张空图,那不是有用的状态。
    if (!hidden[name] && visible.length === 1) return
    setHidden((prev) => ({ ...prev, [name]: !prev[name] }))
  }

  return (
    <div className="chartbox">
      <div className="legend">
        {results.map((r, i) => (
          <button
            type="button"
            key={r.strategy}
            className={`lg${hidden[r.strategy] ? ' off' : ''}`}
            data-series={r.strategy}
            onClick={() => toggle(r.strategy)}
          >
            <span className="k" style={{ background: seriesColor(i) }} />
            {r.strategy}
            {/* 末值读 summary.final_equity,不是 equity[len-1](ADR-025)。 */}
            <span className="val">{money0(r.summary.final_equity)}</span>
          </button>
        ))}
      </div>

      {/* ResponsiveContainer 需要**父容器有确定高度**,否则在真实浏览器里
          渲染为零宽白图(jsdom 无布局引擎,看不见这个)—— 故写死 height。 */}
      <div className="plot" style={{ width: '100%', height: 300 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 10, right: 14, bottom: 4, left: 8 }}>
            <CartesianGrid stroke={cssVar('--line-soft')} vertical={false} />
            <XAxis
              dataKey="date" minTickGap={64} tickLine={false}
              stroke={cssVar('--ink-3')}
              tick={{ fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
            />
            <YAxis
              width={66} tickLine={false} stroke={cssVar('--ink-3')}
              tick={{ fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
              tickFormatter={money0}
            />
            {/* 初始资金基准:在它之上是赚,之下是亏 */}
            <ReferenceLine
              y={initialCash} stroke={cssVar('--ink-3')}
              strokeDasharray="3 3" strokeOpacity={0.6}
            />
            <Tooltip content={<Tip />} cursor={{ stroke: cssVar('--ink-2'), strokeOpacity: 0.55 }} />
            <Legend content={() => null} />
            {/* 颜色按**原始下标**取(filter 前就记下)—— 隐藏一条不会
                让其余曲线换色,这也是不用 results.indexOf 的原因。 */}
            {visible.map(({ result, index }) => (
              <Line
                key={result.strategy}
                type="monotone"
                dataKey={result.strategy}
                stroke={seriesColor(index)}
                strokeWidth={1.6}
                dot={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
