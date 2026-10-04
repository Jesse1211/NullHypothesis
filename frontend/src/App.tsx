/** T12 · **唯一的状态持有者**(关闭 OQ-10)。
 *
 * 三栏骨架取自已批准的界面预览(ADR-046):左 `rail` 表单 / 中 `main` 结果 /
 * 右 `histo` 历史,顶上一条 `topbar`。
 *
 * T13/T14 的组件只接 props,不自己 fetch —— 这是三个任务并行写同一个 `src/`
 * 而不互相覆盖的唯一保障(ADR-037b)。
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
import ErrorCard from './components/ErrorCard'

function toApiError(e: unknown): ApiError {
  if (e instanceof ApiFailure) return e.payload
  return { code: 'INVALID_REQUEST', message: String(e), detail: {} }
}

export default function App() {
  const [runResult, setRunResult] = useState<RunResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(false)

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
      setError(null)
    } catch (e) {
      setError(toApiError(e))
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
    setError(null)
    setArchived(null)
    try {
      const res = await apiClient.run({
        strategies: selectedStrategies,
        data_file: dataFile as string,
        cash: Number(cash),
        fee: Number(fee),
      })
      setRunResult(res)
      setRanFee(Number(fee))
      setRuns(await apiClient.listRuns())
    } catch (e) {
      setError(toApiError(e))
    } finally {
      setLoading(false)
    }
  }

  async function onSelectRun(runId: string) {
    try {
      setArchived(await apiClient.getRun(runId))
      setError(null)
    } catch (e) {
      setError(toApiError(e))
    }
  }

  function toggleStrategy(name: string) {
    setSelectedStrategies((prev) =>
      prev.includes(name) ? prev.filter((x) => x !== name) : [...prev, name],
    )
  }

  // 交易清单显示哪条策略:多选时取第一条(与 ADR-038 的 results[0] 口径一致)
  const tradeFocus = runResult?.results[0] ?? null

  return (
    <>
      <div className="topbar">
        <span className="brand">
          NullHypothesis<span className="v">v0 · 阶段二</span>
        </span>
        <span className="spacer" />
        <button className="themebtn" id="theme-toggle" type="button" onClick={toggleTheme}>
          切换主题
        </button>
      </div>

      <div className="shell">
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

        <main className="main">
          {error && <ErrorCard error={error} />}

          {/* 空态:请求成功但尚未运行。**不是白屏**。 */}
          {!runResult && !error && resourcesLoaded && (
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

                {/* 紧跟曲线 —— 位置本身是契约(ADR-046) */}
                <LimitationsPanel
                  assumptions={runResult.assumptions}
                  summary={runResult.results[0].summary}
                />

                <div className="pngnote">
                  <span className="dot" />
                  <span>
                    归档 PNG:{runResult.results[0].png_path} · 与上图同源(I9)
                  </span>
                </div>
              </section>

              <section className="zone">
                <div className="zhead">
                  <span className="ztitle">回测汇总</span>
                  <span className="zsub">后端算好的字段,前端零计算(I8)</span>
                </div>
                <SummaryPanel
                  results={runResult.results}
                  assumptions={runResult.assumptions}
                  fee={ranFee}
                />
              </section>

              <ComparisonTable results={runResult.results} />

              {tradeFocus && <TradeList result={tradeFocus} />}
            </>
          )}
        </main>

        <HistoryList
          runs={runs}
          selected={archived}
          onSelect={(id) => void onSelectRun(id)}
        />
      </div>
    </>
  )
}
