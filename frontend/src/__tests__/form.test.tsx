/** T12 的行为门:按钮启用的充要条件、空态、OQ-08 阈值、后端连不上。 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import RunForm, { canRun, FILTER_THRESHOLD } from '../components/RunForm'
import App from '../App'
import fixture from '../__fixtures__/run_response.json'

const OK = {
  selectedStrategies: ['a.py'],
  dataFile: 'x.csv',
  cash: '100000',
  fee: '0',
  slippage: '0',
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

  // ADR-056:滑点的校验与费率**逐字对称**。用例集刻意与 fee 的**完全相同**
  // —— 若哪天两者的区间分叉,这组门会立刻暴露不对称。
  it.each(['-0.01', '0.1', '0.5', '1', '', 'abc'])('slippage=%s → 禁用', (slippage) => {
    expect(canRun({ ...OK, slippage })).toBe(false)
  })

  it.each(['0', '0.0005', '0.0999'])('slippage=%s → 启用', (slippage) => {
    expect(canRun({ ...OK, slippage })).toBe(true)
  })

  it('滑点率输入框存在(ADR-056 机制 1:参数存在即是声明)', () => {
    renderForm()
    const el = document.getElementById('slippage') as HTMLInputElement
    expect(el, '表单缺滑点输入框 → 界面只能跑后端默认的 0 且不告知,'
      + '而同屏声明写着「零滑点永远不现实」').toBeTruthy()
    expect(el.value).toBe('0')
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
    slippage: '0',
    running: false,
    onToggleStrategy: vi.fn(),
    onDataFile: vi.fn(),
    onCash: vi.fn(),
    onFee: vi.fn(),
    onSlippage: vi.fn(),
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
        cash="100000" fee="0" slippage="0" running={false}
        onToggleStrategy={vi.fn()} onDataFile={vi.fn()} onCash={vi.fn()}
        onFee={vi.fn()} onSlippage={vi.fn()} onRun={vi.fn()}
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

// ─────────────────────────────────────────────────────────────────────────────
// ADR-056 机制 1 + 2 的端到端门:滑点必须**真的发出去**、且**真的显示出来**
//
// 手动 QA 发现的缺陷:`slippage` 在 `api.ts` / `types.ts` 里都声明了,
// CLI 与归档视图也都有,**唯独运行表单与实时报告头没有** —— 于是界面
// 只能跑后端默认的 `0`,且不告知。而同屏的 23 条声明里逐字写着
// 「零滑点永远不现实」:界面一边说零滑点不现实,一边不给设置入口。
//
// 光有「输入框存在」那条门是不够的 —— 字段可以存在于表单却永不进提交体。
// 故这里断言**真实的请求体**(mock 的是 `fetch`,不是 api 客户端)。
// ─────────────────────────────────────────────────────────────────────────────

describe('ADR-056:滑点进提交体并显示生效值', () => {
  beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })
  afterEach(() => { vi.unstubAllGlobals() })

  it('POST /api/run 的请求体含 slippage(机制 1)', async () => {
    const bodies: string[] = []
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      const u = String(url)
      if (u.endsWith('/api/run')) {
        bodies.push(String(init?.body))
        // **以契约 fixture 为响应体**(项目规则:手写 JSON 正是契约门要防的事)。
        // `results: []` 会让 `App` 在 `results[0].summary` 上崩 —— 那不是产品
        // 缺陷,是 mock 不真实:真实的 200 响应永远至少有一条结果。
        return new Response(JSON.stringify(fixture),
          { status: 200, headers: { 'content-type': 'application/json' } })
      }
      // 启动时的三个 GET
      const payload = u.endsWith('/api/strategies') ? ['a.py']
        : u.endsWith('/api/data') ? ['x.csv'] : []
      return new Response(JSON.stringify(payload),
        { status: 200, headers: { 'content-type': 'application/json' } })
    })

    render(<App />)
    // 等资源加载完(策略列表出现)
    await waitFor(() => expect(screen.getByText('a.py')).toBeInTheDocument())
    // 选策略 + 数据,再改滑点,然后跑
    // 用 fireEvent —— 直接赋 `.value` 绕过了 React 的合成事件,
    // state 不会更新(实测:请求体里 slippage 仍是 0)。
    fireEvent.click(document.querySelector('.pick input') as HTMLInputElement)
    fireEvent.change(document.getElementById('data-file') as HTMLSelectElement,
      { target: { value: 'x.csv' } })
    const sl = document.getElementById('slippage') as HTMLInputElement
    expect(sl, '表单必须有滑点输入框').toBeTruthy()
    fireEvent.change(sl, { target: { value: '0.0005' } })
    await waitFor(() => {
      const btn = document.getElementById('run-button') as HTMLButtonElement
      expect(btn.disabled).toBe(false)
    })
    fireEvent.click(document.getElementById('run-button') as HTMLButtonElement)

    await waitFor(() => expect(bodies.length).toBe(1))
    const sent = JSON.parse(bodies[0]) as Record<string, unknown>
    expect(Object.keys(sent), '请求体必须含 slippage —— 否则界面只能跑后端默认的 0')
      .toContain('slippage')
    expect(sent.slippage).toBe(0.0005)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 左栏分上下两段(使用者:「上半部分可以 scroll,如果策略选择非常多的情况下,
// 但是下面的选择是不支持 scroll 的」)
// ─────────────────────────────────────────────────────────────────────────────

describe('策略很多时下段仍然可达', () => {
  it('⭐ 30 条策略:数据/资金/费率/滑点/按钮全部仍在 DOM', () => {
    // 这是使用者描述的场景。`.rail` 若整体滚动,下段会被挤出视野;
    // 本门守的是「下段存在且排在策略组之后」—— 滚动归上段。
    renderForm({ strategies: Array.from({ length: 30 }, (_, i) => `s${i}.py`) })
    for (const id of ['data-file', 'cash', 'fee', 'slippage', 'run-button']) {
      expect(document.getElementById(id), `策略多时丢了 #${id}`).toBeTruthy()
    }
    expect(document.querySelectorAll('.pick')).toHaveLength(30)
  })

  it('下段必须排在策略组【之后】(顺序决定谁吃剩余空间)', () => {
    renderForm({ strategies: ['a.py', 'b.py'] })
    const kids = Array.from(document.querySelector('.rail')!.children)
      .map((e) => e.className)
    const nav = kids.findIndex((k) => k.includes('nav-group'))
    const vstack = kids.findIndex((k) => k.includes('vstack'))
    expect(nav, '找不到策略组').toBeGreaterThan(-1)
    expect(vstack, '数字输入组须在策略组之后').toBeGreaterThan(nav)
  })
})
