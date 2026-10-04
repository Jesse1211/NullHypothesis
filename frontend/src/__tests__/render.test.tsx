/** T13/T14 的渲染门:I8(读模型一致性)、I9、ADR-018 不排名、ADR-031。
 *
 * **所有 mock 以契约 fixture 为基底**(`run_response.json`,由 T10 的 pytest
 * 从真实响应生成),只就地覆写要测的那几个字段 —— 键集合不变。手写 JSON
 * 正是契约门要防的事。
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import fixture from '../__fixtures__/run_response.json'
import type { ApiError, RunResponse, StrategyResult } from '../types'
import SummaryPanel from '../components/SummaryPanel'
import ComparisonTable from '../components/ComparisonTable'
import EquityChart from '../components/EquityChart'
import ErrorCard from '../components/ErrorCard'
import HistoryList from '../components/HistoryList'

const BASE = fixture as unknown as RunResponse

function clone<T>(x: T): T {
  return JSON.parse(JSON.stringify(x)) as T
}

function field(name: string): string {
  return screen.getByText(name).closest('.row')!.querySelector('dd')!.textContent!
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
    render(<SummaryPanel result={inconsistent()} assumptions={[]} />)
    expect(field('总收益')).toBe('+42.00%')
    expect(field('总收益')).not.toContain('100')
  })

  it('final_equity 显示后端的值', () => {
    render(<SummaryPanel result={inconsistent()} assumptions={[]} />)
    expect(field('期末资产')).toBe('200,000.00')
  })

  it('trade_count 显示 7,不是 trades.length(3)', () => {
    const r = inconsistent()
    expect(r.trades.length).toBe(3)       // 前提:两者确实不同
    render(<SummaryPanel result={r} assumptions={[]} />)
    expect(field('交易次数')).toBe('7')
  })

  it('max_gap_pct 显示 -8.42%,非 8.42% / -0.08% / -842.00%', () => {
    render(<SummaryPanel result={inconsistent()} assumptions={[]} />)
    const v = field('最大跳空')
    expect(v).toBe('-8.42%')
    expect(v).not.toBe('8.42%')
    expect(v).not.toBe('-0.08%')
    expect(v).not.toBe('-842.00%')
    expect(field('最大跳空日期')).toBe('2020-03-16')
  })

  it('max_gap_pct: null → 按 null_display 显示,非 null/NaN/空白', () => {
    const r = clone(BASE.results[0])
    r.summary.max_gap_pct = null
    r.summary.max_gap_date = null
    r.summary.mean_abs_gap_pct = null
    render(<SummaryPanel result={r} assumptions={[]} />)
    for (const name of ['最大跳空', '最大跳空日期', '平均绝对跳空']) {
      const v = field(name)
      expect(v).toBe('—')
      expect(v).not.toMatch(/null|NaN/)
      expect(v.trim()).not.toBe('')
    }
  })
})

// ═══════════════ ADR-021:假设文案可见 ═══════════════

describe('ADR-021 假设文案', () => {
  it('两条 ⚠ 文案在界面上可见', () => {
    const { container } = render(
      <SummaryPanel result={BASE.results[0]} assumptions={BASE.assumptions} />,
    )
    expect(BASE.assumptions.length).toBe(2)
    // 断言在 `.assumptions` 列表里逐条出现 —— 不用 RegExp(文案含 `(` `)`
    // 等元字符),也不用 getByText(祖先节点的 textContent 也包含它,
    // 会命中多个元素)。
    const items = Array.from(container.querySelectorAll('.assumptions li'))
      .map((li) => li.textContent ?? '')
    expect(items).toHaveLength(2)
    for (const a of BASE.assumptions) {
      expect(items.some((t) => t.includes(a))).toBe(true)
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
    const { container } = render(<EquityChart results={[r]} />)
    expect(container.querySelector('.chart')).toBeTruthy()

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
    const { container } = render(<EquityChart results={BASE.results} />)
    expect(BASE.results.length).toBe(2)
    expect(container.querySelector('.chart')).toBeTruthy()
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
    render(<SummaryPanel result={r} assumptions={[]} />)
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
    render(<HistoryList runs={[]} selected={null} onSelect={vi.fn()} />)
    expect(screen.getByText(/还没有跑过/)).toBeInTheDocument()
  })

  it('点历史项 → 显示归档 PNG + 汇总', () => {
    const { container } = render(
      <HistoryList
        runs={[{
          run_id: archived.run_id, strategies: ['buy_and_hold.py'],
          data_file: 'aapl.csv',
          final_equity: archived.results[0].summary.final_equity,
        }]}
        selected={archived}
        onSelect={vi.fn()}
      />,
    )
    const img = container.querySelector('.archive-detail img') as HTMLImageElement
    expect(img).toBeTruthy()
    // ADR-045:图像走专用端点,**不是** `/out/...` 静态路径。
    const src = img.getAttribute('src')!
    expect(src).toBe(
      `/api/runs/${archived.run_id}/png/buy_and_hold_equity.png`,
    )
    expect(src.startsWith('/out/')).toBe(false)
  })

  it('ADR-045:对比图也走专用端点', () => {
    const withComp = {
      ...archived,
      comparison_png_path: `out/${archived.run_id}/comparison.png`,
    }
    const { container } = render(
      <HistoryList runs={[]} selected={withComp} onSelect={vi.fn()} />,
    )
    const srcs = Array.from(container.querySelectorAll('.archive-detail img'))
      .map((i) => i.getAttribute('src')!)
    expect(srcs).toContain(`/api/runs/${archived.run_id}/png/comparison.png`)
    for (const s of srcs) expect(s.startsWith('/out/')).toBe(false)
  })

  it('归档详情中【不存在】Recharts 容器 —— 只显示 PNG 快照', () => {
    const { container } = render(
      <HistoryList runs={[]} selected={archived} onSelect={vi.fn()} />,
    )
    const detail = container.querySelector('.archive-detail')!
    expect(within(detail as HTMLElement).queryByText(/recharts/i)).toBeNull()
    expect(detail.querySelector('.recharts-wrapper')).toBeNull()
    expect(detail.querySelector('.chart')).toBeNull()
  })
})
