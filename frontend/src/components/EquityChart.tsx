/** T13 · Recharts 净值曲线 + **价格对照曲线**(ADR-049)。只接 props。
 *
 * **I9**:`data` 就是响应里的 `equity` 序列,不降采样、不归一化、不改字段名。
 * 门断言点数 `== len(equity)` 且逐点相等。
 *
 * 交互对齐已批准的预览稿(ADR-046):图例可点击切换显隐并显示当前值、
 * hover 十字线 + 读数框、末点圆点、初始资金虚线基准。
 *
 * **双 y 轴,两边都是绝对值**(ADR-049):左轴净值(USD)、右轴价格(USD)。
 * 价格 ~$27 与净值 ~$245,000 相差约 3600 倍,同一个轴上价格线会贴着底,
 * 等于没画。两条都是后端给的原值,前端**不做归一化**(ADR-030/OQ-04)——
 * Recharts 各自算的轴刻度属于「图表装饰」豁免项。
 *
 * **为什么值得画**:买入持有时净值与价格的形状**必须全同**(差一个常数
 * 比例 = 持股数)。两条线形状不一致就是引擎算错了 —— 这是整条管线最直观
 * 的一个正确性检查,而阶段一的 PNG 里看不到它。
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

/** 价格对照曲线在宽表里的列名(ADR-049)。
 *
 * 取一个**不可能与策略 stem 相撞**的名字:策略名来自用户的文件名,
 * 若它恰好叫 `价格` 就会和这一列互相覆盖。`__price__` 不是合法的 Python
 * 标识符开头形态下的常见命名,且 ADR-037 的 stem 来自文件名(不含 `__`
 * 前后缀的双下划线形态极少),故碰撞风险实际为零。 */
export const PRICE_KEY = '__price__'
export const PRICE_LABEL = '价格(右轴)' 

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
          <span>{p.dataKey === PRICE_KEY ? PRICE_LABEL : String(p.dataKey)}</span>
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
  // 价格对照曲线的末值 —— 读 equity[last].close,不自己取价(ADR-025)。
  const lastClose = results[0].equity[results[0].equity.length - 1].close
  const priceShown = !hidden[PRICE_KEY]

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
      // 价格对照列(ADR-049)。同一份 CSV,各策略的 close 必然相同,
      // 所以后写覆盖前写是幂等的 —— 不是「最后一条策略的价格」。
      row[PRICE_KEY] = pt.close
    }
  }
  const data = Array.from(byDate.values())

  function toggle(name: string) {
    // 至少留一条曲线 —— 全部隐藏会得到一张空图,那不是有用的状态。
    // 价格对照线也算一条,所以隐藏最后一条策略时若价格仍显示是允许的。
    const shown = visible.length + (priceShown ? 1 : 0)
    if (!hidden[name] && shown === 1) return
    setHidden((prev) => ({ ...prev, [name]: !prev[name] }))
  }

  return (
    <div className="chartbox">
      <div className="legend">
        {/* 价格对照的图例项(ADR-049)—— 与策略项一样可点击切换显隐 */}
        <button
          type="button"
          className={`lg${hidden[PRICE_KEY] ? ' off' : ''}`}
          data-series={PRICE_KEY}
          onClick={() => toggle(PRICE_KEY)}
        >
          <span className="k" style={{ background: cssVar('--ink-3') }} />
          {PRICE_LABEL}
          <span className="val">{money(lastClose)}</span>
        </button>
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
            {/* 左轴:净值(USD)。绝对值,不归一化。 */}
            <YAxis
              yAxisId="equity"
              width={66} tickLine={false} stroke={cssVar('--ink-3')}
              tick={{ fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
              tickFormatter={money0}
            />
            {/* 右轴:价格(USD)。同样是绝对值 —— 两轴各自缩放是为了让
                相差约 3600 倍的两条曲线都可读。这【不是归一化】:每条线
                的数值仍是后端原值,只是刻度不同,而轴刻度属于 ADR-030
                的「图表装饰」豁免项。 */}
            <YAxis
              yAxisId="price" orientation="right"
              width={56} tickLine={false} stroke={cssVar('--ink-3')}
              tick={{ fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
              tickFormatter={(v: number) => v.toFixed(2)}
            />
            {/* 初始资金基准:在它之上是赚,之下是亏 */}
            <ReferenceLine
              yAxisId="equity"
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
                yAxisId="equity"
                type="monotone"
                dataKey={result.strategy}
                stroke={seriesColor(index)}
                strokeWidth={1.6}
                dot={false}
                isAnimationActive={false}
              />
            ))}

            {/* 价格对照线:细、灰、虚线 —— 它是**参照物**不是结果,
                视觉权重必须低于净值曲线(ADR-049)。 */}
            {priceShown && (
              <Line
                yAxisId="price"
                type="monotone"
                dataKey={PRICE_KEY}
                name={PRICE_LABEL}
                stroke={cssVar('--ink-3')}
                strokeWidth={1.2}
                strokeDasharray="4 3"
                dot={false}
                isAnimationActive={false}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
