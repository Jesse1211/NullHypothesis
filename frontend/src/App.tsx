/** T12 · **唯一的跨组件状态持有者**(OQ-10)。
 *
 * 两栏骨架 + 顶栏两个 tab(ADR-048):「跑一次」与「历史」共用同一个左栏
 * 槽位,内容随 tab 变。右侧 232px 的历史栏因此消失,主栏宽出 232px ——
 * 曲线与七列交易表都更舒展。
 *
 * 主栏内部顺序见 ADR-047(结论 → 证据 → 明细)。
 * T13/T14 的组件只接 props,不自己 fetch(ADR-037b)。
 */

import { useEffect, useState } from 'react'
import * as apiClient from './api'
import { ApiFailure } from './api'
import type { ApiError, ArchivedRun, RunListItem, RunResponse } from './types'
import RunForm from './components/RunForm'
import EquityChart from './components/EquityChart'
import LimitationsPanel from './components/LimitationsPanel'
import SummaryPanel from './components/SummaryPanel'
import ComparisonTable from './components/ComparisonTable'
import TradeList from './components/TradeList'
import HistoryList from './components/HistoryList'
import ArchiveDetail from './components/ArchiveDetail'
import ErrorCard from './components/ErrorCard'

/** ADR-048:顶栏两个 tab,二选一。 */
export type Tab = 'run' | 'history'

function toApiError(e: unknown): ApiError {
  if (e instanceof ApiFailure) return e.payload
  return { code: 'INVALID_REQUEST', message: String(e), detail: {} }
}

export default function App() {
  const [tab, setTab] = useState<Tab>('run')

  const [runResult, setRunResult] = useState<RunResponse | null>(null)
  const [loading, setLoading] = useState(false)

  /** 错误**按来源分开**(ADR-048):共享一个 `error` 会让在历史页读归档
   *  报的错跑到运行页去显示 —— 用户在 A 页做的事,错误出现在 B 页。 */
  const [runError, setRunError] = useState<ApiError | null>(null)
  const [historyError, setHistoryError] = useState<ApiError | null>(null)

  const [strategies, setStrategies] = useState<string[]>([])
  const [dataFiles, setDataFiles] = useState<string[]>([])
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [archived, setArchived] = useState<ArchivedRun | null>(null)
  const [resourcesLoaded, setResourcesLoaded] = useState(false)

  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([])
  const [dataFile, setDataFile] = useState<string | null>(null)
  const [cash, setCash] = useState('100000')
  const [fee, setFee] = useState('0')
  /** 本次结果对应的费率 —— 回执里显示的必须是**跑的时候**那个值,
   *  不是输入框的当前值(用户可能跑完又改了它)。 */
  const [ranFee, setRanFee] = useState<number | undefined>(undefined)

  async function refresh() {
    try {
      const [s, d, r] = await Promise.all([
        apiClient.listStrategies(), apiClient.listData(), apiClient.listRuns(),
      ])
      setStrategies(s)
      setDataFiles(d)
      setRuns(r)
      setRunError(null)
    } catch (e) {
      setRunError(toApiError(e))
    } finally {
      setResourcesLoaded(true)
    }
  }

  useEffect(() => { void refresh() }, [])

  function toggleTheme() {
    const root = document.documentElement
    let cur = root.getAttribute('data-theme')
    if (!cur) {
      cur = window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    }
    root.setAttribute('data-theme', cur === 'dark' ? 'light' : 'dark')
  }

  async function onRun() {
    setLoading(true)
    setRunError(null)
    try {
      const res = await apiClient.run({
        strategies: selectedStrategies,
        data_file: dataFile as string,
        cash: Number(cash),
        fee: Number(fee),
      })
      setRunResult(res)
      setRanFee(Number(fee))
      // 历史列表随之更新(角标 +1)—— 但**不切 tab**:你点「跑」是想看
      // 结果,结果就在当前页;把人弹走是劫持。
      setRuns(await apiClient.listRuns())
    } catch (e) {
      setRunError(toApiError(e))
    } finally {
      setLoading(false)
    }
  }

  async function onSelectRun(runId: string) {
    try {
      setArchived(await apiClient.getRun(runId))
      setHistoryError(null)
    } catch (e) {
      setHistoryError(toApiError(e))
    }
  }

  function toggleStrategy(name: string) {
    setSelectedStrategies((prev) =>
      prev.includes(name) ? prev.filter((x) => x !== name) : [...prev, name],
    )
  }

  const TABS: Array<{ id: Tab; label: string; badge?: number }> = [
    { id: 'run', label: '跑一次' },
    { id: 'history', label: '历史', badge: runs.length },
  ]

  return (
    <>
      <div className="topbar">
        <span className="brand">
          NullHypothesis<span className="v">v0 · 阶段二</span>
        </span>

        <nav className="nav" role="tablist" aria-label="主导航">
          {TABS.map((t) => (
            <button
              type="button"
              key={t.id}
              role="tab"
              id={`nav-${t.id}`}
              aria-selected={tab === t.id}
              className={`navtab${tab === t.id ? ' on' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {t.badge !== undefined && t.badge > 0 && (
                <span className="badge">{t.badge}</span>
              )}
            </button>
          ))}
        </nav>

        <span className="spacer" />
        <button className="themebtn" id="theme-toggle" type="button" onClick={toggleTheme}>
          切换主题
        </button>
      </div>

      {/* 两栏(ADR-048)。切 tab 只换内容,不清任何状态 ——
          切走再切回,跑出来的结果还在。 */}
      <div className="shell">
        {tab === 'run' ? (
          <RunForm
            strategies={strategies}
            dataFiles={dataFiles}
            selectedStrategies={selectedStrategies}
            dataFile={dataFile}
            cash={cash}
            fee={fee}
            running={loading}
            onToggleStrategy={toggleStrategy}
            onDataFile={setDataFile}
            onCash={setCash}
            onFee={setFee}
            onRun={() => void onRun()}
          />
        ) : (
          <HistoryList
            runs={runs}
            selectedId={archived?.run_id ?? null}
            onSelect={(id) => void onSelectRun(id)}
          />
        )}

        <main className="main" role="tabpanel" aria-labelledby={`nav-${tab}`}>
          {tab === 'run' ? (
            <>
              {runError && <ErrorCard error={runError} />}

              {/* 空态:请求成功但尚未运行。**不是白屏**。 */}
              {!runResult && !runError && resourcesLoaded && (
                <section className="zone">
                  <div className="emptybox" style={{ padding: '44px 20px' }}>
                    <div style={{ fontSize: 13, color: 'var(--ink-2)', marginBottom: 6 }}>
                      还没有结果
                    </div>
                    <div>
                      选一条策略和一份数据,然后点「跑」。<br />
                      策略读自 <code>strategies/</code>,数据读自 <code>data/</code>
                    </div>
                  </div>
                </section>
              )}

              {runResult && (
                <>
                  {/* ── 1 · 结论 ──────────────────────────────────────
                      你问的是「账户最后会变成什么样」—— 答案排第一。
                      多策略时回执只留整次运行的属性,按策略的数字交给对比表
                      (两处相邻列同一组数字会让人以为漏看了某个差别)。 */}
                  <section className="zone">
                    <div className="zhead">
                      <span className="ztitle">回测汇总</span>
                      <span className="zsub">后端算好的字段,前端零计算(I8)</span>
                    </div>
                    <SummaryPanel
                      results={runResult.results}
                      assumptions={runResult.assumptions}
                      fee={ranFee}
                      perStrategy={runResult.results.length === 1}
                    />
                  </section>

                  {/* ── 2 · 多策略的按策略数字 ────────────────────────── */}
                  <ComparisonTable results={runResult.results} />

                  {/* ── 3 · 证据:曲线,以及【紧跟它】的来源与局限 ──────
                      PNG 备注是这张图的出处,局限是读这张图的前提 —— 两者都
                      必须贴着曲线,中间不插入任何数字区块(ADR-046/047)。 */}
                  <section className="zone">
                    <div className="zhead">
                      <span className="ztitle">账户净值</span>
                      <span className="zsub">
                        {runResult.results[0].summary.start} → {runResult.results[0].summary.end}
                        {' · '}{runResult.results[0].summary.bars.toLocaleString('zh-CN')} 个交易日
                      </span>
                    </div>

                    <EquityChart
                      results={runResult.results}
                      initialCash={runResult.results[0].summary.initial_cash}
                    />

                    <div className="pngnote">
                      <span className="dot" />
                      <span>
                        归档 PNG:{runResult.results[0].png_path} · 与上图同源(I9)
                      </span>
                    </div>

                    <LimitationsPanel
                      assumptions={runResult.assumptions}
                      summary={runResult.results[0].summary}
                    />
                  </section>

                  {/* ── 4 · 明细 ───────────────────────────────────────── */}
                  <TradeList results={runResult.results} />
                </>
              )}
            </>
          ) : (
            <>
              {historyError && <ErrorCard error={historyError} />}
              <ArchiveDetail run={archived} />
            </>
          )}
        </main>
      </div>
    </>
  )
}
