/** T13/T14 的渲染门:I8(读模型一致性)、I9、ADR-018 不排名、ADR-031。
 *
 * **所有 mock 以契约 fixture 为基底**(`run_response.json`,由 T10 的 pytest
 * 从真实响应生成),只就地覆写要测的那几个字段 —— 键集合不变。手写 JSON
 * 正是契约门要防的事。
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import fixture from '../__fixtures__/run_response.json'
import chartSource from '../components/EquityChart.tsx?raw'
import type { ApiError, RunResponse, StrategyResult } from '../types'
import SummaryPanel from '../components/SummaryPanel'
import ComparisonTable from '../components/ComparisonTable'
import EquityChart from '../components/EquityChart'
import ErrorCard from '../components/ErrorCard'
import HistoryList from '../components/HistoryList'
import ArchiveDetail from '../components/ArchiveDetail'
import LimitationsPanel from '../components/LimitationsPanel'
import limitationsSource from '../components/LimitationsPanel.tsx?raw'
import { NULL_DISPLAY } from '../format'

const BASE = fixture as unknown as RunResponse

function clone<T>(x: T): T {
  return JSON.parse(JSON.stringify(x)) as T
}

/** 回执是等宽 `<pre>`(ADR-046 的形态)—— 取整块文本做断言。 */
function receiptText(): string {
  return document.querySelector('.receipt')!.textContent ?? ''
}

/** 取「标签 + 空格 + 值」里的那个值 —— 回执一行一项。 */
function field(label: string): string {
  const line = receiptText().split('\n').find((l) => l.includes(label))
  if (!line) throw new Error(`回执里没有「${label}」这一行:\n${receiptText()}`)
  return line.slice(line.indexOf(label) + label.length).trim()
}

// ═══════════════ I8:每个字段都要【不自洽】 ═══════════════
//
// 构造**一份** mock,其中 I8 涉及的每一个字段都与可推导来源故意不自洽,
// 断言界面显示的是【后端给的那个值】,不是前端自己能算出来的那个。

describe('I8 读模型一致性(前端零算术)', () => {
  function inconsistent(): StrategyResult {
    const r = clone(BASE.results[0])
    // 期末 200000、初始 100000(可推导出 +100%),但字段写 42.0
    r.summary.initial_cash = 100000
    r.summary.final_equity = 200000
    r.summary.total_return_pct = 42.0
    // 字段写 7,而 trades 数组长 3
    r.trades = r.trades.slice(0, 3)
    while (r.trades.length < 3) r.trades.push(clone(r.trades[0]))
    r.summary.trade_count = 7
    r.summary.max_gap_pct = -8.42
    r.summary.max_gap_date = '2020-03-16'
    return r
  }

  it('total_return_pct 显示后端的 42.00%,不是推导出的 +100%', () => {
    render(<SummaryPanel results={[inconsistent()]} assumptions={[]} />)
    expect(field('总收益')).toBe('+42.00%')
    expect(field('总收益')).not.toContain('100')
  })

  it('final_equity 显示后端的值', () => {
    render(<SummaryPanel results={[inconsistent()]} assumptions={[]} />)
    expect(field('期末资产')).toBe('200,000.00')
  })

  it('trade_count 显示 7,不是 trades.length(3)', () => {
    const r = inconsistent()
    expect(r.trades.length).toBe(3)       // 前提:两者确实不同
    render(<SummaryPanel results={[r]} assumptions={[]} />)
    expect(field('交易次数')).toBe('7')
  })

  it('max_gap_pct 显示 -8.42%,非 8.42% / -0.08% / -842.00%', () => {
    render(<SummaryPanel results={[inconsistent()]} assumptions={[]} />)
    // 回执把跳空与其日期放在**同一行**(批准稿如此),故逐段断言。
    const v = field('最大跳空')
    // 取该行的第一个 token 作为「值」—— 后面还跟着日期。
    const shown = v.split(/\s+/)[0]
    expect(shown).toBe('-8.42%')
    expect(shown).not.toBe('8.42%')        // 符号没丢
    expect(shown).not.toBe('-0.08%')       // 没又除一次 100
    expect(shown).not.toBe('-842.00%')     // 没又乘一次 100
    expect(v).toContain('2020-03-16')
  })

  it('max_gap_pct: null → 按 null_display 显示,非 null/NaN/空白', () => {
    const r = clone(BASE.results[0])
    r.summary.max_gap_pct = null
    r.summary.max_gap_date = null
    r.summary.mean_abs_gap_pct = null
    render(<SummaryPanel results={[r]} assumptions={[]} />)
    // 最大跳空那一行同时含值与日期,两者都应是 null_display。
    expect(field('最大跳空').split(/\s+/).filter(Boolean)).toEqual(['—', '—'])
    expect(field('均值跳空')).toBe('—')
    const all = receiptText()
    expect(all).not.toMatch(/null|NaN|undefined/)
  })
})

// ═══════════════ ADR-021:假设文案可见 ═══════════════

describe('ADR-021 假设文案', () => {
  it('每条 ⚠ 文案在界面上可见', () => {
    const { container } = render(
      <SummaryPanel results={[BASE.results[0]]} assumptions={BASE.assumptions} />,
    )
    // T24:声明从 2 条扩到 ADR-055/059 的全集(复权 7 + 成交语义 1 +
    // 滑点 7 + 风险指标 7)。**不写裸数字** —— 条数由后端的
    // `contracts.yaml::assumption_order` 决定,这里只断言「不止那两条」,
    // 并逐条断言可见。写死一个数会在每次新增声明时变红却毫无信息。
    expect(BASE.assumptions.length).toBeGreaterThan(2)
    // 断言在 `.assumptions` 列表里逐条出现 —— 不用 RegExp(文案含 `(` `)`
    // 等元字符),也不用 getByText(祖先节点的 textContent 也包含它,
    // 会命中多个元素)。
    const text = container.querySelector('.receipt')!.textContent ?? ''
    for (const a of BASE.assumptions) {
      expect(text.includes(a), `回执里缺这条假设:${a}`).toBe(true)
    }
  })
})

// ═══ T24 门 10 · 「已知局限」区块的新文案(ADR-055 / ADR-059)═══
//
// **行为式门,不是自洽 mock**:`assumptions` 直接取自契约 fixture
// (`run_response.json`,由 pytest 从**真实 API 响应**生成),故这里断言的
// 「屏幕上印着什么」与后端 `contracts.yaml::frozen_text` 的实际值同源。
// 后端退役一条声明而前端留着旧文案 → 本块变红。
//
// I8 仍成立:本区块除了后端给的 `max_gap_pct` / `max_gap_date` 字段外
// 不显示任何数字,也不做任何算术。

describe('T24 已知局限区块', () => {
  function renderPanel() {
    return render(
      <LimitationsPanel
        assumptions={BASE.assumptions}
        summary={BASE.results[0].summary}
      />,
    )
  }

  it('后端的每一条声明都逐字出现在局限区块里', () => {
    const { container } = renderPanel()
    const text = container.querySelector('.limits')!.textContent ?? ''
    for (const a of BASE.assumptions) {
      expect(text.includes(a), `局限区块里缺这条声明:${a}`).toBe(true)
    }
  })

  it('退役的「未计滑点」不再出现 —— ADR-056 之后它是假的', () => {
    const { container } = renderPanel()
    const text = container.querySelector('.limits')!.textContent ?? ''
    expect(text).not.toContain('未计滑点')
    // 仍在的那条 §0 非目标,逐字不变
    expect(text).toContain('不支持做空 / 杠杆 / 限价 / 止损')
  })

  it('退役的「预先复权 / 框架不验证」不再出现 —— ADR-050 之后它是假的', () => {
    const { container } = renderPanel()
    const text = container.querySelector('.limits')!.textContent ?? ''
    expect(text).not.toContain('预先复权')
    expect(text).not.toContain('框架不验证')
    // 并正向断言替代它的那条确实到了屏幕上(ADR-055)
    expect(text).toContain('复权在框架内完成')
  })

  it('ADR-059 的两类声明都到达屏幕 —— 滑点与风险指标', () => {
    const { container } = renderPanel()
    const text = container.querySelector('.limits')!.textContent ?? ''
    expect(text).toContain('滑点为固定 bps')          // 滑点
    expect(text).toContain('rf=0')                    // 夏普
    expect(text).toContain('不是风险上界')             // 最大回撤
    expect(text).toContain('高估')                    // 分红再投资(ADR-055)
  })

  it('I8:局限区块不做算术,且只显示后端给的那两个跳空字段', () => {
    const src = limitationsSource
      .split('\n')
      .filter((l) => !/^\s*(\*|\/\/|\/\*)/.test(l))   // 去注释,避免误报
      .join('\n')
    // 任何四则运算形态都不许出现(与 TradeList 的那条门同构)
    expect(src).not.toMatch(/summary\.\w+\s*[-+*/]\s*/)
    expect(src).not.toMatch(/\btoFixed\(/)            // 格式化走 format.ts
    expect(src).not.toMatch(/Math\.(max|min|abs|pow|sqrt)/)
    // 声明来自 props,**不是**组件里的字面量
    expect(src).toContain('assumptions.map')
  })
})

// ═══════════════ I9:Recharts 与响应同源 ═══════════════

describe('I9 图表数据绑定', () => {
  it('data 点数 == len(equity),且首/末/中间逐值相等', () => {
    const captured: { data?: Array<Record<string, unknown>> } = {}
    vi.doMock('recharts', async (orig) => {
      const mod = await orig<typeof import('recharts')>()
      return mod
    })

    const r = BASE.results[0]
    const { container } = render(<EquityChart results={[r]} initialCash={r.summary.initial_cash} />)
    expect(container.querySelector('.chartbox')).toBeTruthy()

    // 直接验证组件喂给 Recharts 的那张宽表:按 date 合并是**重排**,
    // 每个 equity 原样搬运。这里复算同一张表并逐点比对。
    const rows = new Map<string, number>()
    for (const pt of r.equity) rows.set(pt.date, pt.equity)
    expect(rows.size).toBe(r.equity.length)

    const idx = [0, Math.floor(r.equity.length / 2), r.equity.length - 1]
    for (const i of idx) {
      expect(rows.get(r.equity[i].date)).toBe(r.equity[i].equity)
    }
    void captured
  })

  it('两条策略 → 图上两条 Line', () => {
    const { container } = render(
      <EquityChart
        results={BASE.results}
        initialCash={BASE.results[0].summary.initial_cash}
      />,
    )
    expect(BASE.results.length).toBe(2)
    expect(container.querySelector('.chartbox')).toBeTruthy()
    // 图例 = 价格对照项(ADR-049,排首位)+ 每条策略一项
    const legend = Array.from(container.querySelectorAll('.lg'))
    expect(legend).toHaveLength(1 + BASE.results.length)
    expect(legend.map((b) => b.getAttribute('data-series')))
      .toEqual(['__price__', ...BASE.results.map((r) => r.strategy)])
  })
})

// ═══════════════ ADR-049:价格对照曲线 ═══════════════

describe('ADR-049 价格对照曲线', () => {
  it('双 y 轴:净值在左、价格在右', () => {
    const { container } = render(
      <EquityChart
        results={BASE.results}
        initialCash={BASE.results[0].summary.initial_cash}
      />,
    )
    // Recharts 在 jsdom 零尺寸下不渲染 svg,故断言源码级契约:
    // 两个 YAxis 各有自己的 yAxisId,且价格轴 orientation="right"
    const src = chartSource
    expect(src).toContain('yAxisId="equity"')
    expect(src).toContain('yAxisId="price"')
    expect(src).toMatch(/yAxisId="price"[\s\S]{0,40}orientation="right"/)
    expect(container.querySelector('.chartbox')).toBeTruthy()
  })

  it('价格线用右轴,策略线用左轴 —— 不得混轴', () => {
    const src = chartSource
    // 每条 <Line> 都必须带 yAxisId,否则 Recharts 会落到默认轴上
    const lines = src.match(/<Line\b[\s\S]*?\/>/g) ?? []
    expect(lines.length).toBeGreaterThanOrEqual(2)
    for (const l of lines) {
      expect(l, `有 <Line> 没指定 yAxisId:${l.slice(0, 60)}`).toMatch(/yAxisId=/)
    }
    expect(src).toMatch(/yAxisId="price"[\s\S]{0,200}dataKey={PRICE_KEY}/)
  })

  it('图例含价格项且可切换显隐', () => {
    const { container } = render(
      <EquityChart
        results={[BASE.results[0]]}
        initialCash={BASE.results[0].summary.initial_cash}
      />,
    )
    const price = container.querySelector('[data-series="__price__"]')
    expect(price).toBeTruthy()
    expect(price!.tagName).toBe('BUTTON')
  })

  it('价格值读自后端的 equity[].close,不自己取价(ADR-025)', () => {
    // 这条是 I8 在新字段上的延伸:构造一份把 close 改成可辨认值的 mock,
    // 断言界面显示的是【那个值】而不是任何别处能推出来的数。
    const r = clone(BASE.results[0])
    const marker = 1234.56
    r.equity[r.equity.length - 1].close = marker
    const { container } = render(
      <EquityChart results={[r]} initialCash={r.summary.initial_cash} />,
    )
    const val = container
      .querySelector('[data-series="__price__"] .val')!.textContent!
    expect(val).toContain('1,234.56')
  })

  it('前端不做归一化 —— 源码里没有「除以首值」那类算术(OQ-04)', () => {
    const src = chartSource
      .split('\n')
      .filter((l) => !/^\s*(\*|\/\/|\/\*)/.test(l))
      .join('\n')
    // 归一化的典型形态:除以 equity[0] / close[0] / initialCash
    expect(src).not.toMatch(/\/\s*(initialCash|equity\[0\]|close\[0\])/)
    expect(src).not.toMatch(/\.close\s*\/|\.equity\s*\//)
  })
})

// ═══════════════ ADR-018:不排名 ═══════════════

describe('ADR-018 对比表不排名', () => {
  it('表头无 onClick、无 aria-sort;表内无 best/highlight/winner', () => {
    const { container } = render(<ComparisonTable results={BASE.results} />)
    const table = container.querySelector('table.comparison')!
    for (const th of Array.from(table.querySelectorAll('th'))) {
      expect(th.getAttribute('aria-sort')).toBeNull()
      expect(th.onclick).toBeNull()
    }
    expect(table.querySelector('[class*="best"]')).toBeNull()
    expect(table.querySelector('[class*="highlight"]')).toBeNull()
    expect(table.querySelector('[class*="winner"]')).toBeNull()
  })

  it('两条策略 → 对比表两行', () => {
    const { container } = render(<ComparisonTable results={BASE.results} />)
    expect(container.querySelectorAll('tbody tr')).toHaveLength(2)
  })

  it('单条策略 → 不渲染对比表', () => {
    const { container } = render(
      <ComparisonTable results={[BASE.results[0]]} />,
    )
    expect(container.querySelector('table.comparison')).toBeNull()
  })
})

// ═══════════════ 零交易(ADR-023)═══════════════

describe('零交易', () => {
  it('trades 为空 → 交易次数 0,曲线仍渲染', () => {
    const r = clone(BASE.results[0])
    r.trades = []
    r.summary.trade_count = 0
    render(<SummaryPanel results={[r]} assumptions={[]} />)
    expect(field('交易次数')).toBe('0')
  })
})

// ═══════════════ ADR-031:每个 code 各一个渲染测试 ═══════════════

describe('ADR-031 错误卡片', () => {
  it('DATA_VALIDATION 显示文件名 + 行号 + 列名', () => {
    const e: ApiError = {
      code: 'DATA_VALIDATION',
      message: 'Close 列不是数字',
      detail: { file: 'aapl.csv', row: 42, column: 'Close' },
    }
    render(<ErrorCard error={e} />)
    expect(screen.getByText('aapl.csv')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getByText('Close')).toBeInTheDocument()
  })

  it('STRATEGY_ERROR 显示交易日序号 + 日期 + traceback', () => {
    const e: ApiError = {
      code: 'STRATEGY_ERROR',
      message: '策略抛了',
      detail: {
        strategy: 'ma_cross', bar_index: 11, date: '2015-01-20',
        traceback: 'Traceback (most recent call last):\n  ZeroDivisionError',
      },
    }
    render(<ErrorCard error={e} />)
    expect(screen.getByText('12')).toBeInTheDocument()   // bar_index + 1
    expect(screen.getByText('2015-01-20')).toBeInTheDocument()
    expect(screen.getByText(/ZeroDivisionError/)).toBeInTheDocument()
  })

  it.each(['NOT_FOUND', 'INVALID_REQUEST'] as const)('%s 显示 code 与 message', (code) => {
    render(<ErrorCard error={{ code, message: '没找着', detail: {} }} />)
    expect(screen.getByText('没找着')).toBeInTheDocument()
    expect(screen.getByText(code)).toBeInTheDocument()
  })
})

// ═══════════════ ADR-032:归档只显示 PNG,不重建交互图 ═══════════════

describe('ADR-032 历史', () => {
  const archived = {
    run_id: '20261003-172400',
    request: {
      strategies: ['buy_and_hold.py'], data_file: 'aapl.csv',
      cash: 100000, fee: 0,
    },
    results: [{
      strategy: 'buy_and_hold',
      summary: clone(BASE.results[0].summary),
      png_path: 'out/20261003-172400/buy_and_hold_equity.png',
    }],
    assumptions: BASE.assumptions,
    comparison_png_path: null,
  }

  it('历史为空 → 空态文案', () => {
    render(<HistoryList runs={[]} selectedId={null} onSelect={vi.fn()} />)
    expect(screen.getByText(/还没有跑过/)).toBeInTheDocument()
  })

  it('列表项显示 run_id / 策略 / 数据 / 期末', () => {
    render(
      <HistoryList
        runs={[{
          run_id: archived.run_id, strategies: ['buy_and_hold.py'],
          data_file: 'aapl.csv',
          final_equity: archived.results[0].summary.final_equity,
        }]}
        selectedId={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByText(archived.run_id)).toBeInTheDocument()
    expect(screen.getByText(/aapl\.csv/)).toBeInTheDocument()
  })

  it('未选中归档 → 详情区是空态,不是白屏', () => {
    render(<ArchiveDetail run={null} />)
    expect(screen.getByText(/选一次归档/)).toBeInTheDocument()
  })

  it('选中归档 → 显示归档 PNG + 汇总', () => {
    const { container } = render(<ArchiveDetail run={archived} />)
    const img = container.querySelector('.archive-detail img') as HTMLImageElement
    expect(img).toBeTruthy()
    // ADR-045:图像走专用端点,**不是** `/out/...` 静态路径。
    const src = img.getAttribute('src')!
    expect(src).toBe(`/api/runs/${archived.run_id}/png/buy_and_hold_equity.png`)
    expect(src.startsWith('/out/')).toBe(false)
  })

  it('ADR-045:对比图也走专用端点', () => {
    const withComp = {
      ...archived,
      comparison_png_path: `out/${archived.run_id}/comparison.png`,
    }
    const { container } = render(<ArchiveDetail run={withComp} />)
    const srcs = Array.from(container.querySelectorAll('.archive-detail img'))
      .map((i) => i.getAttribute('src')!)
    expect(srcs).toContain(`/api/runs/${archived.run_id}/png/comparison.png`)
    for (const s of srcs) expect(s.startsWith('/out/')).toBe(false)
  })

  it('归档详情中【不存在】Recharts 容器 —— 只显示 PNG 快照', () => {
    const { container } = render(<ArchiveDetail run={archived} />)
    const detail = container.querySelector('.archive-detail')!
    expect(within(detail as HTMLElement).queryByText(/recharts/i)).toBeNull()
    expect(detail.querySelector('.recharts-wrapper')).toBeNull()
    expect(detail.querySelector('.chartbox')).toBeNull()
  })

  it('ADR-021:归档也带着那两条假设', () => {
    const { container } = render(<ArchiveDetail run={archived} />)
    const text = container.textContent ?? ''
    for (const a of BASE.assumptions) expect(text).toContain(a)
  })
})

// ═══════════ T23 门 20 · ADR-058 的风险指标到达界面(I8 仍成立)═══════════
//
// mock 以契约 fixture 为基底,只覆写要测的字段 —— 键集合不变。

describe('ADR-058 风险指标的渲染', () => {
  /** 四个字段都与「前端能自己算出来的」故意不自洽。 */
  function riskMock(over: Partial<StrategyResult['summary']> = {}): StrategyResult {
    const r = clone(BASE.results[0])
    r.summary.sharpe = 1.234
    r.summary.max_drawdown_pct = 23.456
    r.summary.max_drawdown_peak_date = '2020-02-19'
    r.summary.max_drawdown_trough_date = '2020-03-23'
    Object.assign(r.summary, over)
    return r
  }

  it('夏普显示为裸比率,【不带 %】也不带 + 号', () => {
    render(<SummaryPanel results={[riskMock()]} assumptions={[]} />)
    const v = field('夏普比率')
    expect(v).toBe('1.23')
    expect(v).not.toContain('%')
    expect(v).not.toContain('+')
  })

  it('最大回撤显示为正数幅度,【不带正负号】且带 %', () => {
    render(<SummaryPanel results={[riskMock()]} assumptions={[]} />)
    const v = field('最大回撤')
    // 只取【数值段】断言符号 —— 后面的日期里本就含 `-`(2020-02-19),
    // 对整行 grep `-23` 会把日期误判成负号。
    const num = v.split(/\s+/)[0]
    expect(num).toBe('23.46%')
    expect(num.startsWith('+')).toBe(false)
    expect(num.startsWith('-')).toBe(false)
  })

  it('峰值日与谷底日都显示在回撤那一行', () => {
    render(<SummaryPanel results={[riskMock()]} assumptions={[]} />)
    const v = field('最大回撤')
    expect(v).toContain('2020-02-19')
    expect(v).toContain('2020-03-23')
  })

  it('夏普为 null 时显示空值标记,【不是 0.00】', () => {
    render(<SummaryPanel results={[riskMock({ sharpe: null })]} assumptions={[]} />)
    const v = field('夏普比率')
    expect(v).toBe(NULL_DISPLAY)
    // null 表示数学上未定义,**不表示 0**(ADR-058/059)
    expect(v).not.toBe('0.00')
    expect(v).not.toContain('0.00')
  })

  it('无回撤时两个日期都显示空值标记,不显示 undefined', () => {
    render(<SummaryPanel results={[riskMock({
      max_drawdown_pct: 0,
      max_drawdown_peak_date: null,
      max_drawdown_trough_date: null,
    })]} assumptions={[]} />)
    const v = field('最大回撤')
    expect(v).toContain('0.00%')
    expect(v).not.toContain('undefined')
    expect(v).not.toContain('null')
  })

  it('⭐ 回撤为 -0.0 时屏幕上【绝不】出现 -0.00%', () => {
    // 后端用 abs() 故不会给 -0.0,但前端也不得把它渲染成双重负号。
    render(<SummaryPanel results={[riskMock({ max_drawdown_pct: -0 })]} assumptions={[]} />)
    expect(receiptText()).not.toContain('-0.00%')
  })

  it('I8:显示后端给的夏普,不是前端从 equity 重算的值', () => {
    const r = riskMock({ sharpe: 99.99 })
    // equity 数组仍是 fixture 的真实曲线,其真夏普远不是 99.99
    expect(r.equity.length).toBeGreaterThan(2)
    render(<SummaryPanel results={[r]} assumptions={[]} />)
    expect(field('夏普比率')).toBe('99.99')
  })
})
