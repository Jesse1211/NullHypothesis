/** ADR-037b 的布局契约门 —— 否则 ADR-037b 全文无门。
 *
 * ADR-037b 自述「这是三个任务并行写同一个 `src/` 而不互相覆盖的唯一保障」,
 * 却在第 8 轮前没有任何门断言它 —— T12 现有的门只验行为与构建成功,
 * 一个结构完全不同的 `src/` 也能通过。
 *
 * **用 Vite 的 `import.meta.glob` 读源码,不用 `node:fs`**:ADR-037b 钉死了
 * 依赖集合(本文件自己就在断言它),而 `node:fs` / `__dirname` 需要
 * `@types/node` —— 加它就违反了本门断言的那个集合,`npx tsc --noEmit` 要么
 * 报错、要么逼着改契约。
 */

import { describe, expect, it } from 'vitest'
import pkg from '../../package.json'
import viteConfig from '../../vite.config.ts?raw'


// eager + query:'?raw' → { '/src/App.tsx': '<源码字符串>', ... }
const SOURCES = import.meta.glob('../**/*.{ts,tsx}', {
  eager: true, query: '?raw', import: 'default',
}) as Record<string, string>

/** CSS 与 HTML 另外 glob:`import ... from '*.css?raw'` 在 vitest 下返回
 *  **空串**(默认 `css: false`,CSS 被 stub 掉),而 glob 走的是同一个
 *  `?raw` 转换但不经 CSS 插件 —— 实测前者 length 0,后者正常。 */
const ASSETS = {
  ...import.meta.glob('../styles.css', { eager: true, query: '?raw', import: 'default' }),
  ...import.meta.glob('../../index.html', { eager: true, query: '?raw', import: 'default' }),
} as Record<string, string>

const cssSource = ASSETS['../styles.css'] ?? ''
const indexHtml = ASSETS['../../index.html'] ?? ''

/** glob 的键是相对本文件的路径(`../App.tsx`),归一成 `App.tsx` 形式。 */
function source(rel: string): string | undefined {
  const want = `../${rel}`
  return SOURCES[want]
}

const PINNED_FILES = [
  'main.tsx', 'App.tsx', 'types.ts', 'api.ts',
  'components/RunForm.tsx',
  'components/EquityChart.tsx',
  'components/SummaryPanel.tsx',
  'components/ComparisonTable.tsx',
  'components/HistoryList.tsx',
  'components/ErrorCard.tsx',
  // ADR-046 补入
  'components/TradeList.tsx',
  'components/LimitationsPanel.tsx',
]

// T13/T14 的组件:只接 props,不自己取数。
const PROPS_ONLY = [
  'components/EquityChart.tsx',
  'components/SummaryPanel.tsx',
  'components/ComparisonTable.tsx',
  'components/HistoryList.tsx',
  'components/ErrorCard.tsx',
  'components/TradeList.tsx',
  'components/LimitationsPanel.tsx',
]

describe('ADR-037b 布局契约', () => {
  it.each(PINNED_FILES)('%s 存在', (f) => {
    expect(source(f), `ADR-037b 钉死了 src/${f}`).toBeTypeOf('string')
  })

  it('package.json 的依赖键集合等于 ADR-037b 钉死的那一组', () => {
    expect(Object.keys(pkg.dependencies).sort()).toEqual(
      ['react', 'react-dom', 'recharts'],
    )
    expect(Object.keys(pkg.devDependencies).sort()).toEqual([
      '@testing-library/jest-dom', '@testing-library/react',
      '@types/react', '@types/react-dom', '@vitejs/plugin-react',
      'jsdom', 'typescript', 'vite', 'vitest',
    ])
  })

  it.each(PROPS_ONLY)('%s 不含 fetch(', (f) => {
    expect(source(f)!.includes('fetch('), `${f} 自己取数了 —— 它只应接 props`)
      .toBe(false)
  })

  it('App.tsx 是唯一出现 useState<RunResponse 的文件', () => {
    const hits = PINNED_FILES.filter((f) =>
      source(f)!.includes('useState<RunResponse'),
    )
    expect(hits).toEqual(['App.tsx'])
  })

  it('vite.config.ts 把 /api 代理到 127.0.0.1:8000', () => {
    expect(viteConfig).toContain('http://127.0.0.1:8000')
    expect(viteConfig).toContain("'/api'")
  })
})

// ═══════════════ ADR-046 · 界面布局契约 ═══════════════
//
// **这一组门的缺失就是那次返工的原因。** T12–T14 第一版把 ADR-037b 的
// 组件清单当成了界面设计,产出单栏堆叠界面 —— 而 63 条前端门全绿,因为
// 它们只断言数据正确性,没有一条断言布局。

describe('ADR-046 布局契约(批准稿 8QUBMStfEymniHPnkUFYiP)', () => {
  const app = source('App.tsx')!
  const css = cssSource

  it('三栏骨架:rail / main / histo', () => {
    for (const cls of ['rail', 'main', 'histo']) {
      expect(css, `styles.css 缺 .${cls}`).toContain(`.${cls}`)
    }
    // 三栏 grid,不是单栏堆叠
    expect(css).toMatch(/grid-template-columns:\s*268px\s+minmax\(0,\s*1fr\)\s+232px/)
    expect(app).toContain('className="shell"')
  })

  it('顶栏:品牌 + 版本 + 主题切换', () => {
    expect(app).toContain('className="topbar"')
    expect(app).toContain('className="brand"')
    expect(app).toContain('id="theme-toggle"')
  })

  it('亮/暗双主题:三种状态都定义了 token', () => {
    // 未标记(系统) / 显式 light 不被系统 dark 覆盖 / 显式 dark
    expect(css).toContain('@media (prefers-color-scheme: dark)')
    expect(css).toContain(':root:not([data-theme="light"])')
    expect(css).toContain(':root[data-theme="dark"]')
    // body 必须有显式背景,否则会借宿主的底色
    expect(css).toMatch(/body\s*\{[^}]*background:\s*var\(--ground\)/)
  })

  it('回执式汇总是等宽 pre,不是 dl', () => {
    const sp = source('components/SummaryPanel.tsx')!
    expect(sp).toContain('className="receipt"')
    expect(sp).toContain('<pre')
    // 排除注释行再找 —— 本文件的 docstring 里就有一句「为什么是 `<pre>`
    // 而不是 `<dl>`」,按裸子串匹配会命中它(与 ADR-036 的 re.compile
    // 误报同类)。
    const code = sp.split('\n')
      .filter((l) => !/^\s*(\*|\/\/|\/\*)/.test(l))
      .join('\n')
    expect(/<dl[\s>]/.test(code), '汇总退回成了 dl —— 批准稿是回执形态').toBe(false)
    expect(css).toMatch(/\.receipt\s*\{[^}]*font-family:\s*var\(--mono\)/)
  })

  it('「已知局限」是独立区块,不是脚注', () => {
    const lp = source('components/LimitationsPanel.tsx')!
    expect(lp).toContain('className="limits"')
    expect(lp).toContain('由设计决定,非遗漏')
  })

  it('交易清单七列,列序与批准稿一致', async () => {
    const mod = await import('../components/TradeList')
    expect(mod.TRADE_COLUMNS).toEqual([
      '日期', '动作', '股数', '价格', '金额', '成交后现金', '成交后持股',
    ])
  })

  it('金额列读 trade.amount,不自己算 shares × price(ADR-025/046)', () => {
    const tl = source('components/TradeList.tsx')!
    expect(tl).toContain('x.amount')
    // 任何 shares 与 price 相乘的形态都不许出现
    expect(tl).not.toMatch(/shares\s*\*\s*price|price\s*\*\s*shares/)
  })

  it('对比表含最大跳空列(批准稿五列)', () => {
    const ct = source('components/ComparisonTable.tsx')!
    for (const h of ['策略', '期末资产', '总收益', '交易次数', '最大跳空']) {
      expect(ct, `对比表缺「${h}」列`).toContain(h)
    }
  })

  it('图表图例可点击切换显隐(批准稿的交互)', () => {
    const ec = source('components/EquityChart.tsx')!
    expect(ec).toContain('className={`lg')
    expect(ec).toContain('onClick')
    expect(ec).toContain('data-series')
  })

  it('index.html 引入 Inter Tight 与 JetBrains Mono', () => {
    expect(indexHtml).toContain('Inter+Tight')
    expect(indexHtml).toContain('JetBrains+Mono')
    expect(indexHtml).toContain('fonts.googleapis.com')
  })
})

// ═══════════════ ADR-047 · 主栏阅读顺序 ═══════════════

describe('ADR-047 主栏顺序:结论 → 证据 → 明细', () => {
  const app = source('App.tsx')!
  const at = (tag: string) => {
    const i = app.indexOf(`<${tag}`)
    expect(i, `App.tsx 里找不到 <${tag}`).toBeGreaterThan(-1)
    return i
  }

  it('结论(汇总)在证据(曲线)之前', () => {
    expect(at('SummaryPanel')).toBeLessThan(at('EquityChart'))
  })

  it('对比表紧随汇总,在曲线之前', () => {
    expect(at('ComparisonTable')).toBeGreaterThan(at('SummaryPanel'))
    expect(at('ComparisonTable')).toBeLessThan(at('EquityChart'))
  })

  it('PNG 备注与局限区块都【紧跟曲线】,中间不插数字区块', () => {
    const chart = at('EquityChart')
    const png = app.indexOf('className="pngnote"')
    const limits = at('LimitationsPanel')
    expect(png).toBeGreaterThan(chart)
    expect(limits).toBeGreaterThan(chart)
    // 曲线与局限之间除了 PNG 备注不得再有别的区块
    const between = app.slice(chart, limits)
    for (const forbidden of ['<SummaryPanel', '<ComparisonTable', '<TradeList']) {
      expect(between, `${forbidden} 横在曲线与局限之间`).not.toContain(forbidden)
    }
  })

  it('交易清单排最后', () => {
    const trade = at('TradeList')
    for (const earlier of ['SummaryPanel', 'ComparisonTable', 'EquityChart']) {
      expect(trade).toBeGreaterThan(at(earlier))
    }
  })

  it('单策略给回执、多策略给对比:perStrategy 按策略数量切换', () => {
    expect(app).toContain('perStrategy={runResult.results.length === 1}')
  })

  it('回执【始终】渲染 —— 对账行与假设文案不随策略数量消失', () => {
    // 先剥注释 —— docstring 里也提到「对账」,按裸子串找会命中说明文字
    // 而不是那行代码(与 `<dl>` 那条门同类的误报)。
    const sp = source('components/SummaryPanel.tsx')!
      .split('\n')
      .filter((l) => !/^\s*(\*|\/\/|\/\*)/.test(l))
      .join('\n')
    // 对账行与假设在 perStrategy 分支【之外】(在它之后、且不在 if 块里)
    const branch = sp.indexOf('if (perStrategy)')
    expect(branch).toBeGreaterThan(-1)
    expect(sp.indexOf("text: ' ✓ 对账")).toBeGreaterThan(branch)
    expect(sp.indexOf('assumptions.forEach')).toBeGreaterThan(branch)
    // 且 App 无条件渲染它(没有 results.length 的条件包裹)
    expect(app).not.toMatch(/results\.length\s*===\s*1\s*&&\s*<SummaryPanel/)
  })

  it('交易清单多策略时渲染可切换的策略名', () => {
    const tl = source('components/TradeList.tsx')!
    expect(tl).toContain('role="tablist"')
    expect(tl).toContain('results.length > 1')   // 单策略不渲染
    expect(tl).toContain('aria-selected')
  })
})
