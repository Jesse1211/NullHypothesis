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
import contractsYaml from '../../../contracts.yaml?raw'
import { NULL_DISPLAY } from '../format'


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
  // ADR-048 从 HistoryList 拆出
  'components/ArchiveDetail.tsx',
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
  'components/ArchiveDetail.tsx',
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

  it('两栏骨架:rail / main(ADR-048 取代 ADR-046 的三栏)', () => {
    for (const cls of ['rail', 'main']) {
      expect(css, `styles.css 缺 .${cls}`).toContain(`.${cls}`)
    }
    // 两栏 grid,不是单栏堆叠、也不再是三栏
    expect(css).toMatch(/grid-template-columns:\s*268px\s+minmax\(0,\s*1fr\);/)
    // 只看**声明**,不看注释 —— 解释「232px 历史栏已移除」的那句注释
    // 本身含 232px。按行首字符过滤不够(CSS 块注释的续行不以 * 开头),
    // 所以整段剥掉 /* … */。
    const decls = css.replace(/\/\*[\s\S]*?\*\//g, '')
    expect(decls, '右侧 232px 历史栏应已被 ADR-048 移除').not.toContain('232px')
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

// ═══════════════ ADR-048 · 顶栏导航 ═══════════════

describe('ADR-048 nav bar:跑一次 / 历史', () => {
  const app = source('App.tsx')!

  it('顶栏有 tablist,两个 tab', () => {
    expect(app).toContain('role="tablist"')
    expect(app).toContain("{ id: 'run', label: '跑一次' }")
    expect(app).toContain("id: 'history', label: '历史'")
    expect(app).toContain('aria-selected')
  })

  it('tab 状态归 App(OQ-10),类型是二选一', () => {
    expect(app).toMatch(/useState<Tab>\('run'\)/)
    expect(app).toContain("export type Tab = 'run' | 'history'")
  })

  it('左栏槽位按 tab 二选一:RunForm 或 HistoryList', () => {
    expect(app).toMatch(/tab === 'run' \?[\s\S]{0,80}<RunForm/)
    expect(app).toContain('<HistoryList')
  })

  it('历史 tab 带条数角标', () => {
    expect(app).toContain('badge: runs.length')
  })

  it('跑完【不】自动切 tab —— 把人弹走是劫持', () => {
    const onRun = app.slice(app.indexOf('async function onRun'),
                            app.indexOf('async function onSelectRun'))
    expect(onRun, 'onRun 里不应出现 setTab').not.toContain('setTab')
  })

  it('错误按来源分开 —— 历史页的错不跑到运行页', () => {
    expect(app).toContain('runError')
    expect(app).toContain('historyError')
    // onSelectRun 只写 historyError,不碰 runError
    const sel = app.slice(app.indexOf('async function onSelectRun'),
                          app.indexOf('function toggleStrategy'))
    expect(sel).toContain('setHistoryError')
    expect(sel).not.toContain('setRunError')
  })

  it('归档详情已从 HistoryList 拆出到 ArchiveDetail', () => {
    const hl = source('components/HistoryList.tsx')!
    // 列表不再渲染 PNG —— 那是 ArchiveDetail 的事
    expect(hl).not.toContain('<img')
    expect(source('components/ArchiveDetail.tsx')!).toContain('<img')
  })

  it('切 tab 不清状态 —— 切走再切回结果还在', () => {
    // setTab 只出现在 nav 的 onClick 里,不与任何 setXxx(null) 同行
    const setTabCalls = app.split('\n').filter((l) => l.includes('setTab('))
    expect(setTabCalls.length).toBeGreaterThan(0)
    for (const l of setTabCalls) {
      expect(l, `切 tab 时清了状态:${l.trim()}`).not.toMatch(/set\w+\(null\)/)
    }
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// `contracts.yaml` ↔ 前端常量的逐字绑定
//
// 手动 QA 发现的缺陷:`format.ts` 把 `NULL_DISPLAY` 硬编码成 `'—'`,而契约写
// 的是 `N/A`。后端**运行时读契约**,前端硬编码 —— 同一次运行在 CLI 与 Web 上
// 显示两个不同的空值符号,而 115 条门全绿(`render.test.tsx` 断言的是字面量
// `'—'`,把漂移编码进去了)。
//
// 前端无法在运行时读 YAML(无该依赖,且 I8 不允许前端解析契约),故那个常量是
// 一处**不可避免的镜像**。不可避免的镜像必须有门 —— 否则它就是一个静默漂移点。
// ─────────────────────────────────────────────────────────────────────────────

describe('contracts.yaml ↔ 前端常量', () => {
  /** 从 YAML 文本里取一个标量 key 的值(不引入 YAML 解析依赖 ——
   *  那会违反 ADR-037b 的依赖集合,而本文件自己在断言那个集合)。 */
  function yamlScalar(key: string): string {
    const m = contractsYaml.match(
      new RegExp(`^\\s*${key}:\\s*"([^"]*)"`, 'm'),
    )
    expect(m, `contracts.yaml 里找不到 ${key}`).toBeTruthy()
    return m![1]
  }

  it('NULL_DISPLAY 与 formats.null_display 逐字相等', () => {
    expect(NULL_DISPLAY).toBe(yamlScalar('null_display'))
  })

  it('没有任何源文件把空值标记写成字面量', () => {
    // 查的是**代码里**的 `'—'` / `'N/A'` 字面量,不是注释里的字符。
    // 先剥掉注释 —— 否则这条门会把「解释这个 bug 的注释」本身判为违规
    // (实测:我写的那两段说明文字就命中了,那是假阳性)。
    const stripComments = (src: string) =>
      src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')

    const offenders = Object.entries(SOURCES)
      // `format.ts` 是 NULL_DISPLAY 的**定义处**,它必须含那个字面量
      .filter(([f]) => !f.includes('format.ts'))
      .filter(([, src]) => /(['"`])(?:—|N\/A)\1/.test(stripComments(src)))
      .map(([f]) => f)

    expect(
      offenders,
      '空值标记必须引用 NULL_DISPLAY(它绑定 contracts.yaml),不得写死符号 ——'
      + '写死会让「契约改了而界面没改」静默通过,正是本组门要防的漂移',
    ).toEqual([])
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 暗主题的文字对比度(WCAG)
//
// 2026-10-22:原配色在深色底上实测偏暗 —— `--ink-3` 只有 4.03:1,**低于 AA 的
// 4.5 正文门槛**,而它是使用最多的文字 token(19 处)。于是「大部分文字都不是
// --ink」,整屏读起来发灰。本组门把对比度变成**可执行的断言**,而不是靠眼看。
//
// 另一条动机:这组 token 在 `@media (prefers-color-scheme: dark)` 与
// `:root[data-theme="dark"]` 里**重复两份**。只改一处会让「手动切到暗色」
// 仍是旧值 —— 正是本仓库反复踩的那个「同一个值多处复述,改一处漏一处」。
// ─────────────────────────────────────────────────────────────────────────────

describe('暗主题文字对比度', () => {
  const css = cssSource

  /** 取某个暗主题块里的 token 值。 */
  function darkTokens(selector: string): Record<string, string> {
    const i = css.indexOf(selector)
    expect(i, `styles.css 里找不到 ${selector}`).toBeGreaterThan(-1)
    const block = css.slice(i, css.indexOf('}', i))
    const out: Record<string, string> = {}
    for (const m of block.matchAll(/(--[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})/g)) {
      out[m[1]] = m[2].toUpperCase()
    }
    return out
  }

  function luminance(hex: string): number {
    const v = [1, 3, 5].map((i) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
  }

  function ratio(a: string, b: string): number {
    const [x, y] = [luminance(a), luminance(b)]
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
  }

  const MEDIA = '@media (prefers-color-scheme: dark)'
  const TOGGLE = ':root[data-theme="dark"]'

  it('两个暗主题块的 token 完全同值(改一处漏一处会红)', () => {
    expect(darkTokens(MEDIA)).toEqual(darkTokens(TOGGLE))
  })

  it.each([MEDIA, TOGGLE])('%s:三级文字在两种底色上均达 AAA(≥7:1)', (sel) => {
    const t = darkTokens(sel)
    for (const bg of ['--ground', '--surface'] as const) {
      for (const fg of ['--ink', '--ink-2', '--ink-3'] as const) {
        const r = ratio(t[fg], t[bg])
        expect(r, `${fg} on ${bg} = ${r.toFixed(2)}:1 —— 低于 AAA(7:1)。`
          + '最初的缺陷正是 --ink-3 只有 4.03:1(连 AA 都不到)').toBeGreaterThanOrEqual(7)
      }
    }
  })

  it.each([MEDIA, TOGGLE])('%s:主要文字是纯白', (sel) => {
    expect(darkTokens(sel)['--ink']).toBe('#FFFFFF')
  })

  it.each([MEDIA, TOGGLE])('%s:最暗一级也必须「很白」(使用者的实际诉求)', (sel) => {
    // 第一轮提亮(18.91 / 13.67 / 8.98)全部达 AAA,但使用者仍反馈发灰 ——
    // 因为屏幕上大部分文字是 --ink-2/--ink-3。**WCAG 合格不等于看着够白**。
    // 故本门守的是「最暗一级的下限」,而不是层级差:
    // AAA 的 7:1 在这里不够,要求 ≥ 12:1。
    const t = darkTokens(sel)
    for (const bg of ['--ground', '--surface'] as const) {
      const r = ratio(t['--ink-3'], t[bg])
      expect(r, `--ink-3 on ${bg} = ${r.toFixed(2)}:1 —— 使用者要求「全部字体更白」,`
        + '最暗一级不得低于 12:1(AAA 的 7:1 实测仍被感知为灰)')
        .toBeGreaterThanOrEqual(12)
    }
  })

  it.each([MEDIA, TOGGLE])('%s:三级仍严格递减 —— 不是同一个值', (sel) => {
    // 压缩层级 ≠ 取消层级。仍要求严格单调,否则 token 名失去意义
    // (三者同值时 `--ink-2` / `--ink-3` 就该被删掉而不是留着骗人)。
    const t = darkTokens(sel)
    const r = (fg: string) => ratio(t[fg], t['--ground'])
    expect(r('--ink')).toBeGreaterThan(r('--ink-2'))
    expect(r('--ink-2')).toBeGreaterThan(r('--ink-3'))
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 左栏策略选择器:整组满铺(使用者要求「像左侧 nav bar 一样,左侧铺满」)
// ─────────────────────────────────────────────────────────────────────────────

describe('策略选择器满铺左栏', () => {
  const css = cssSource

  it('--rail-pad 是 token —— 满铺靠它与 .rail 的 padding 同值', () => {
    expect(css).toContain('--rail-pad:')
    expect(css).toMatch(/\.rail\s*\{[^}]*padding:[^;]*var\(--rail-pad\)/s)
  })

  it('⭐ 负 margin 只有【一处】—— 两处会双重偏移,行越出左栏', () => {
    // 这是本轮真实踩到的坑:`.pick` 原本自带负 margin 实现满铺,
    // 加上父级 `.nav-group` 的负 margin 后,行会向左越界 18px。
    const n = (css.match(/calc\(-1 \* var\(--rail-pad\)\)/g) ?? []).length
    expect(n, `负 margin 出现 ${n} 次 —— 满铺只能由一层承担(.nav-group)`).toBe(1)
  })

  it('组标题横贯左栏(有自己的上下分隔线),不是内缩小标题', () => {
    const i = css.indexOf('.fgroup.nav-group .lbl {')
    expect(i).toBeGreaterThan(-1)
    const block = css.slice(i, css.indexOf('}', i))
    expect(block).toContain('border-top')
    expect(block).toContain('border-bottom')
    expect(block, '标题的左右内边距须用 --rail-pad,与行对齐')
      .toContain('var(--rail-pad)')
  })

  it('策略组用了 nav-group(否则只有行满铺、标题仍内缩)', () => {
    expect(source('components/RunForm.tsx')!).toContain('fgroup nav-group')
  })

  it('.pick 不再是卡片 —— 无圆角、无四边框,选中靠左侧条', () => {
    const i = css.indexOf('.pick {')
    const block = css.slice(i, css.indexOf('}', i))
    expect(block).not.toContain('border-radius')
    expect(block).toContain('border-left')
    const on = css.slice(css.indexOf('.pick.on {'), css.indexOf('}', css.indexOf('.pick.on {')))
    expect(on, '选中态须用 border-left-color 标识').toContain('border-left-color')
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 左栏纵向占满(使用者:「让这个卡片占满左侧,高度和屏幕一样」)
// ─────────────────────────────────────────────────────────────────────────────

describe('左栏占满屏高', () => {
  const css = cssSource

  /** 取某选择器**基础规则**的声明体,并剥掉注释。
   *  必须剥注释:块里的说明文字会提到 `align-items: start` 等字串,
   *  不剥会把「解释为什么不用它」误判成「用了它」。
   *  必须取基础规则:`indexOf` 会先命中媒体查询里的覆盖版本。 */
  function decls(sel: string, from = 0): string {
    const i = css.indexOf(sel + ' {', from)
    expect(i, `styles.css 里找不到 ${sel}`).toBeGreaterThan(-1)
    return css.slice(i, css.indexOf('}', i)).replace(/\/\*[\s\S]*?\*\//g, '')
  }

  it('⭐ 高度链不得断在 #root —— React 挂载点必须有高度', () => {
    // 原缺陷:`#root` 无任何高度规则,于是 `body{height:100%}` 到此为止,
    // `.shell` 的百分比高度对一个 auto 高度的父级求值(无效),左栏塌成内容高。
    const i = css.indexOf('#root {')
    expect(i, 'styles.css 缺 #root 规则 —— 高度链会断在这里').toBeGreaterThan(-1)
    expect(css.slice(i, css.indexOf('}', i))).toContain('height')
  })

  it('.shell 不得用 align-items: start(它把左栏压成内容高度)', () => {
    expect(decls('.shell')).not.toContain('align-items: start')
  })

  it('.rail 是 flex 列,策略组吸收剩余高度', () => {
    expect(decls('.rail')).toContain('flex-direction: column')
    expect(decls('.fgroup.nav-group'), '策略组须 flex:1 才能长满左栏')
      .toContain('flex: 1 1 auto')
  })

  it('⭐ 窄屏(≤760px)必须撤销占满,且覆盖须在基础规则【之后】', () => {
    // 两者特异性相同,**靠源码顺序决胜**。初版把覆盖写在一个更早的
    // `@media (max-width: 760px)` 块里,而基础规则在后面 ——
    // 于是覆盖永不生效,窄屏左栏仍会拉满。本门同时守「存在」与「顺序」。
    const base = css.indexOf('.fgroup.nav-group {')
    expect(base).toBeGreaterThan(-1)

    // 在基础规则之后找撤销占满的那个媒体查询块
    const mq = css.indexOf('@media (max-width: 760px)', base)
    expect(mq, '基础规则之后必须有一个 ≤760px 的覆盖块').toBeGreaterThan(base)

    const block = css.slice(mq, css.indexOf('\n}', css.indexOf('picklist', mq)))
    expect(block, '窄屏左栏是上方横条,拉满屏高会把结果顶出首屏')
      .toContain('flex: 0 0 auto')
    expect(block).toContain('overflow-y: visible')
  })
})
