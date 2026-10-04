/** T12 的行为门:按钮启用的充要条件、空态、OQ-08 阈值、后端连不上。 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import RunForm, { canRun, FILTER_THRESHOLD } from '../components/RunForm'
import App from '../App'

const OK = {
  selectedStrategies: ['a.py'],
  dataFile: 'x.csv',
  cash: '100000',
  fee: '0',
}

describe('「跑」按钮启用的充要条件(逐条)', () => {
  it('四项齐全 → 启用', () => {
    expect(canRun(OK)).toBe(true)
  })

  it('没选策略 → 禁用', () => {
    expect(canRun({ ...OK, selectedStrategies: [] })).toBe(false)
  })

  it('没选数据 → 禁用', () => {
    expect(canRun({ ...OK, dataFile: null })).toBe(false)
  })

  it.each(['0', '-1', '', '   ', 'abc'])('cash=%s → 禁用', (cash) => {
    expect(canRun({ ...OK, cash })).toBe(false)
  })

  it.each(['-0.01', '0.1', '0.5', '1', '', 'abc'])('fee=%s → 禁用', (fee) => {
    expect(canRun({ ...OK, fee })).toBe(false)
  })

  it.each(['0', '0.0013', '0.0999'])('fee=%s → 启用', (fee) => {
    expect(canRun({ ...OK, fee })).toBe(true)
  })
})

function renderForm(over: Partial<React.ComponentProps<typeof RunForm>> = {}) {
  const props = {
    strategies: ['a.py', 'b.py'],
    dataFiles: ['x.csv'],
    selectedStrategies: ['a.py'],
    dataFile: 'x.csv',
    cash: '100000',
    fee: '0',
    running: false,
    onToggleStrategy: vi.fn(),
    onDataFile: vi.fn(),
    onCash: vi.fn(),
    onFee: vi.fn(),
    onRun: vi.fn(),
    ...over,
  }
  render(<RunForm {...props} />)
  // 按 id 取 —— 运行中按钮文案变成「计算中…」,按名字找会失败
  return document.getElementById('run-button') as HTMLButtonElement
}

describe('ADR-028:运行中按钮禁用', () => {
  it('running=true → 禁用', () => {
    expect(renderForm({ running: true }).disabled).toBe(true)
  })
  it('running=false 且条件齐全 → 启用', () => {
    expect(renderForm({ running: false }).disabled).toBe(false)
  })
})

describe('空态两种各一测(都不是白屏)', () => {
  it('strategies/data 返回 [] → 显示「目录为空」文案', () => {
    render(
      <RunForm
        strategies={[]} dataFiles={[]} selectedStrategies={[]} dataFile={null}
        cash="100000" fee="0" running={false}
        onToggleStrategy={vi.fn()} onDataFile={vi.fn()} onCash={vi.fn()}
        onFee={vi.fn()} onRun={vi.fn()}
      />,
    )
    expect(screen.getByText(/strategies\/ 目录为空/)).toBeInTheDocument()
    expect(screen.getByText(/data\/ 目录为空/)).toBeInTheDocument()
  })
})

describe('OQ-08 阈值双向可证伪(> 20 才渲染过滤框)', () => {
  const many = (n: number) => Array.from({ length: n }, (_, i) => `d${i}.csv`)

  it(`${FILTER_THRESHOLD + 1} 项 → 渲染过滤框`, () => {
    renderForm({ dataFiles: many(FILTER_THRESHOLD + 1) })
    expect(screen.getByLabelText('过滤数据文件')).toBeInTheDocument()
  })

  it(`${FILTER_THRESHOLD} 项 → 不渲染过滤框`, () => {
    renderForm({ dataFiles: many(FILTER_THRESHOLD) })
    expect(screen.queryByLabelText('过滤数据文件')).toBeNull()
  })
})

// ───────── 后端连不上三种各一测(「白屏化」反过来最该防的事)─────────

describe('后端连不上', () => {
  beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })
  afterEach(() => { vi.unstubAllGlobals() })

  it('fetch reject(网络层失败)→ 显示明确错误', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'))
    render(<App />)
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
    expect(screen.getByText(/连不上后端/)).toBeInTheDocument()
  })

  it('200 + text/html(vite 代理的 502 页)→ 明确错误而非崩溃', async () => {
    // 真实的不可达后端走的是这条路径 —— `response.json()` 在这里抛解析错,
    // 只 mock reject 的 catch 往往处理不了它。
    //
    // 必须用 mockImplementation 每次**新建** Response:App 启动时
    // `Promise.all` 并发打三个端点,而一个 Response 的 body 只能读一次 ——
    // 复用同一个对象会让后两个调用抛「body already read」,于是门断言到的
    // 是那条无关的错误,而不是本例要测的那条。
    vi.mocked(fetch).mockImplementation(async () =>
      new Response('<html>502</html>', {
        status: 200, headers: { 'content-type': 'text/html' },
      }))
    render(<App />)
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
    expect(screen.getByText(/不是 JSON/)).toBeInTheDocument()
  })

  it('500 + JSON 错误体 → 显示 ErrorCard', async () => {
    vi.mocked(fetch).mockImplementation(async () => new Response(
      JSON.stringify({ code: 'DATA_VALIDATION', message: '坏了', detail: {} }),
      { status: 500, headers: { 'content-type': 'application/json' } },
    ))
    render(<App />)
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
    expect(screen.getByText('坏了')).toBeInTheDocument()
  })
})
