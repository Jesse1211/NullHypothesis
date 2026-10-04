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
]

// T13/T14 的五个组件:只接 props,不自己取数。
const PROPS_ONLY = [
  'components/EquityChart.tsx',
  'components/SummaryPanel.tsx',
  'components/ComparisonTable.tsx',
  'components/HistoryList.tsx',
  'components/ErrorCard.tsx',
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
