/** T12 · **唯一的状态持有者**(关闭 OQ-10)。
 *
 * T13/T14 的组件只接 props,不自己 fetch —— 这是三个任务并行写同一个
 * `src/` 而不互相覆盖的唯一保障(ADR-037b)。
 */

import { useEffect, useState } from 'react'
import * as apiClient from './api'
import { ApiFailure } from './api'
import type { ApiError, ArchivedRun, RunListItem, RunResponse } from './types'
import RunForm from './components/RunForm'
import EquityChart from './components/EquityChart'
import SummaryPanel from './components/SummaryPanel'
import ComparisonTable from './components/ComparisonTable'
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

  return (
    <main className="app">
      <h1>NullHypothesis</h1>
      <p className="tagline">
        假设过去某段时间真的按这条策略操作,账户最后会变成什么样。
      </p>

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

      {error && <ErrorCard error={error} />}

      {/* 空态:请求成功但尚未运行。**不是白屏**。 */}
      {!runResult && !error && resourcesLoaded && (
        <p className="empty">还没有结果 —— 选好策略和数据,点上面的「跑」。</p>
      )}

      {runResult && (
        <section className="results">
          <EquityChart results={runResult.results} />
          <ComparisonTable results={runResult.results} />
          {runResult.results.map((r) => (
            <SummaryPanel
              key={r.strategy} result={r} assumptions={runResult.assumptions}
            />
          ))}
        </section>
      )}

      <HistoryList runs={runs} selected={archived} onSelect={(id) => void onSelectRun(id)} />
    </main>
  )
}
