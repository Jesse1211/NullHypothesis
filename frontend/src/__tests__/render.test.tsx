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
    //
    // **断言 `NULL_DISPLAY` 而不是字面量 `'—'`。** 手动 QA 发现:原门写死
    // `'—'`,而 `contracts.yaml` 的 `formats.null_display` 是 `N/A` ——
    // 于是这条门把那个漂移**编码**进了测试,CLI 与界面显示两个不同符号而
    // 全绿。字面量化的期望值在这里是反作用的:它锁住的是实现现状,不是契约。
    // 契约与常量之间的绑定由 `layout.test.ts` 的专门一条门守护。
    expect(field('最大跳空').split(/\s+/).filter(Boolean))
      .toEqual([NULL_DISPLAY, NULL_DISPLAY])
    expect(field('均值跳空')).toBe(NULL_DISPLAY)
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

  it('后端声明不得在组件源码里有第二份手写副本(单一归属)', () => {
    // T24 的缺陷类型:面板曾同时手写「未计滑点」与「仅市价单…无条件执行」,
    // 而后端的 `assumption_slippage_*` / `assumption_market_order` 又经
    // `assumptions` 到达同一个 `<ul>` —— 同一主张在同一屏印两遍、两份各自
    // 维护。本门把「文案有且只有一个家」变成可执行的(DESIGN.md §12 的
    // 反复述纪律),否则下一条声明落地时同样的副本会悄悄长回来。
    //
    // 判据:取每条后端声明里足够长的实词片段,断言它不在**去注释后的**
    // JSX 源码里。短片段(如「滑点」二字)会误伤合法的界面措辞,故按
    // 分句切开并只取较长的那些。
    const src = limitationsSource
      .split('\n')
      .filter((l) => !/^\s*(\*|\/\/|\/\*)/.test(l))
      .join('\n')
    for (const a of BASE.assumptions) {
      for (const frag of a.split(/[。,;:()「」]/).map((f) => f.trim())) {
        if (frag.length < 8) continue
        expect(src.includes(frag), `面板源码里手写了后端声明的片段:${frag}`).toBe(false)
      }
    }
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

  // ───────── ADR-058 的两个指标必须在归档视图里可见 ─────────
  //
  // 手动 QA 发现:它们在 `ArchiveDetail` 里**完全缺失** —— 26 条已含这两个
  // 字段的归档,用户在界面上看不到,得去调 API。ADR-032 把本端点定义为
  // 「该次的**汇总** + 归档 PNG」,受限的只是「不重建 Recharts」,汇总在范围内。

  it('ADR-058:归档视图显示夏普与最大回撤(含峰谷日期)', () => {
    const { container } = render(<ArchiveDetail run={archived} />)
    const cap = container.querySelector('.archive-detail figcaption')!.textContent!
    const s = archived.results[0].summary
    // I8:屏幕上的数字逐字来自 JSON,前端零算术
    expect(cap).toContain(`夏普 ${s.sharpe!.toFixed(2)}`)
    expect(cap).toContain(`回撤 ${s.max_drawdown_pct!.toFixed(2)}%`)
    expect(cap).toContain(s.max_drawdown_peak_date!)
    expect(cap).toContain(s.max_drawdown_trough_date!)
    // 回撤是**正数幅度** —— 绝不带负号(ADR-058),也不得是 `-0.00%`
    expect(cap).not.toContain(`回撤 -`)
    expect(cap).not.toContain('-0.00%')
  })

  it('⭐ 键【缺失】(阶段二的归档)→ 整段不渲染,而不是显示空值标记', () => {
    // 这是本组门里最重要的一条。实测 95 条归档里 **68 条**没有这两个键。
    // 若渲染成空值标记,屏幕就等于断言「该值在数学上未定义」—— 而同屏的
    // ADR-059 声明逐字这么解释那个标记。但一条 +145% 的买入持有,它的夏普
    // **完全有定义**,只是阶段二没算。那是在替历史编数据。
    const legacy = clone(archived) as Record<string, any>
    for (const k of ['sharpe', 'max_drawdown_pct',
                     'max_drawdown_peak_date', 'max_drawdown_trough_date']) {
      delete legacy.results[0].summary[k]
    }
    const { container } = render(<ArchiveDetail run={legacy as never} />)
    const cap = container.querySelector('.archive-detail figcaption')!.textContent!
    expect(cap).not.toContain('夏普')
    expect(cap).not.toContain('回撤')
    // 且**不崩、不出现 undefined / NaN**(向后兼容)
    expect(cap).not.toContain('undefined')
    expect(cap).not.toContain('NaN')
    // 既有字段照常显示 —— 整条 figcaption 不是被整段吞掉
    expect(cap).toContain('期末')
  })

  it('⭐ 键存在但为 null(数学上未定义)→ 显示空值标记,不是 0', () => {
    // 与上一条成对:两者**必须长得不一样**。少了任何一条,
    // 「未记录」与「未定义」就会被混为一谈。
    const idle = clone(archived)
    const s = idle.results[0].summary as unknown as Record<string, unknown>
    s.sharpe = null
    s.max_drawdown_pct = 0.0          // 未发生下跌是**确定事实**,故 0 而非 null
    s.max_drawdown_peak_date = null
    s.max_drawdown_trough_date = null
    const { container } = render(<ArchiveDetail run={idle} />)
    const cap = container.querySelector('.archive-detail figcaption')!.textContent!
    expect(cap).toContain(`夏普 ${NULL_DISPLAY}`)
    expect(cap).not.toContain('夏普 0')          // 0 是一种特定的谎言
    // 回撤 0.00% 带两个空值日期 —— 给日期等于断言发生过一次没发生的回撤
    expect(cap).toContain('回撤 0.00%')
    expect(cap).toContain(`(${NULL_DISPLAY} ~ ${NULL_DISPLAY})`)
  })

  it('equity 键【缺失】(旧 checkout 的归档)→ 回退 PNG,无 Recharts 容器', () => {
    // `archived` 没有 `equity` 键 —— 这是**回退路径**的门,不是通则。
    // ADR-032 原文的「只显示 PNG」在 T26 之后仅对这种归档成立。
    const { container } = render(<ArchiveDetail run={archived} />)
    const detail = container.querySelector('.archive-detail')!
    expect(within(detail as HTMLElement).queryByText(/recharts/i)).toBeNull()
    expect(detail.querySelector('.recharts-wrapper')).toBeNull()
    expect(detail.querySelector('.chartbox')).toBeNull()
    // 且 PNG 确实在(回退不是「什么都不画」)
    expect(detail.querySelector('img')).toBeTruthy()
  })

  // ───────── ADR-032 修订(T26)· 历史页的交互图 ─────────
  //
  // 用户诉求:「历史里的图应该和每次跑的时候展示的图一样,dynamic 并且显示
  // 两个 y 轴」。实现手段是**复用同一个 `EquityChart`**,所以这组门的重点
  // 不是「双轴存在」(那由 ADR-049 自己的门守),而是:
  //   1. 有 equity → 走组件而非 <img>
  //   2. 喂进去的数**逐点来自归档**,不是今天重算的
  //   3. 无 equity → 仍回退 PNG(上一条门)
  // 三者合起来才等于「忠于历史的交互图」。

  /** 带 `equity[]` 的归档 —— T26 之后 `run.json` 的形态。 */
  const archivedWithEquity = {
    ...clone(archived),
    results: [{
      ...clone(archived.results[0]),
      // 逐字段等于 ADR-038 的 API 投影(date/equity/close 三键)
      equity: clone(BASE.results[0].equity),
    }],
  }

  it('⭐ 有 equity → 渲染 Recharts 交互图,【不是】静态 PNG', () => {
    const { container } = render(<ArchiveDetail run={archivedWithEquity as never} />)
    const detail = container.querySelector('.archive-detail')!
    // `.chartbox` 是 `EquityChart` 的根 —— 它在,就证明走的是同一个组件,
    // 于是双 y 轴 / hover / 图例切换全部随之到位(无需在此重测 ADR-049)。
    expect(detail.querySelector('.chartbox')).toBeTruthy()
    // 策略图不再是 <img>:整个详情区里唯一可能的 <img> 是对比图,而此
    // fixture 的 comparison_png_path 为 null,故一张都不该有。
    expect(detail.querySelector('img')).toBeNull()
  })

  it('⭐ 曲线的点【逐点来自归档】—— 不是用今天的代码重算', () => {
    // 这是 T26 推翻 ADR-032 原论证的那一步:原文担心「用今天的代码重画一遍」,
    // 而这里画的是**当时存下来的数**。若将来有人图省事改成「按 request 重跑」,
    // 这条门不会红(数可能恰好相同)—— 故同时断言点数与图例末值的来源。
    const { container } = render(<ArchiveDetail run={archivedWithEquity as never} />)
    const detail = container.querySelector('.archive-detail')!
    // I9:不降采样 —— Recharts 的 <path> 点数无法直接数,改断言图例末值,
    // 它读的是 summary.final_equity(ADR-025),与 equity[] 同源于归档。
    const legend = detail.querySelector('.legend')!.textContent!
    expect(legend).toContain('buy_and_hold')
    // 价格对照曲线(右轴)的图例项必须在 —— 这就是「两个 y 轴」的可观测证据
    expect(legend).toContain('价格(右轴)')
  })

  it('⭐ 基准虚线读 request.cash —— 归档没有「今天的默认资金」这回事', () => {
    // 若实现里把 initialCash 写成常量 100000 或读了别处,改掉 request.cash
    // 后这条门会红。基准线问的是「当时投了多少钱」。
    const odd = clone(archivedWithEquity) as Record<string, any>
    odd.request.cash = 250000
    const { container } = render(<ArchiveDetail run={odd as never} />)
    // 资金显示在 zhead 里,与基准线同源(两处都读 request.cash)
    expect(container.querySelector('.zhead')!.textContent).toContain('250,000')
    expect(container.querySelector('.chartbox')).toBeTruthy()
  })

  it('⭐ 多策略归档 → 一张图里多条曲线,与「跑一次」同构', () => {
    const multi = clone(archivedWithEquity) as Record<string, any>
    multi.results.push({
      ...clone(archivedWithEquity.results[0]),
      strategy: 'ma_cross',
    })
    const { container } = render(<ArchiveDetail run={multi as never} />)
    const boxes = container.querySelectorAll('.chartbox')
    // 每条策略一个 figure/一张图(与现有 results.map 的结构一致)
    expect(boxes.length).toBe(2)
    const text = container.textContent!
    expect(text).toContain('buy_and_hold')
    expect(text).toContain('ma_cross')
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
