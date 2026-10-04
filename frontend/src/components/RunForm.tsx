/** T12 · 运行表单。只接 props,不自己取数。 */

export interface RunFormProps {
  strategies: string[]
  dataFiles: string[]
  selectedStrategies: string[]
  dataFile: string | null
  cash: string
  fee: string
  running: boolean
  onToggleStrategy: (name: string) => void
  onDataFile: (name: string) => void
  onCash: (v: string) => void
  onFee: (v: string) => void
  onRun: () => void
}

/** 「跑」按钮启用的**充要条件**(T12 的门逐条测):
 *    `selectedStrategies.length >= 1`
 *  且 `dataFile != null`
 *  且 `cash` 为 `> 0` 的数字
 *  且 `fee` 在 `[0, 0.1)`
 */
export function canRun(p: {
  selectedStrategies: string[]
  dataFile: string | null
  cash: string
  fee: string
}): boolean {
  if (p.selectedStrategies.length < 1) return false
  if (p.dataFile === null) return false
  const cash = Number(p.cash)
  if (!Number.isFinite(cash) || cash <= 0 || p.cash.trim() === '') return false
  const fee = Number(p.fee)
  if (!Number.isFinite(fee) || fee < 0 || fee >= 0.1 || p.fee.trim() === '') {
    return false
  }
  return true
}

// OQ-08:数据文件**超过 20 项**才渲染过滤框。这是个**双边**约束 ——
// 恒加和恒不加都违反它,故 T12 的门用 21 项与 20 项双向断言。
export const FILTER_THRESHOLD = 20

export default function RunForm(props: RunFormProps) {
  const {
    strategies, dataFiles, selectedStrategies, dataFile, cash, fee,
    running, onToggleStrategy, onDataFile, onCash, onFee, onRun,
  } = props

  const enabled = canRun(props) && !running

  return (
    <section className="run-form">
      <h2>跑一次回测</h2>

      <fieldset>
        <legend>策略(可多选,对应 CLI 的 --strategy 可重复)</legend>
        {strategies.length === 0 ? (
          <p className="empty">strategies/ 目录为空 —— 放一个 .py 进去再刷新。</p>
        ) : (
          <ul className="picker">
            {strategies.map((s) => (
              <li key={s}>
                <label>
                  <input
                    type="checkbox"
                    checked={selectedStrategies.includes(s)}
                    onChange={() => onToggleStrategy(s)}
                  />
                  {s}
                </label>
              </li>
            ))}
          </ul>
        )}
      </fieldset>

      <fieldset>
        <legend>数据(只能从服务器 data/ 目录选,ADR-027)</legend>
        {dataFiles.length === 0 ? (
          <p className="empty">data/ 目录为空 —— 放一个 .csv 进去再刷新。</p>
        ) : (
          <>
            {dataFiles.length > FILTER_THRESHOLD && (
              <input
                id="data-filter"
                type="search"
                placeholder="过滤数据文件…"
                aria-label="过滤数据文件"
              />
            )}
          <select
            id="data-file"
            value={dataFile ?? ''}
            onChange={(e) => onDataFile(e.target.value)}
            aria-label="数据文件"
          >
            <option value="" disabled>请选择…</option>
            {dataFiles.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
          </>
        )}
      </fieldset>

      <fieldset className="numbers">
        <label htmlFor="cash">
          初始资金
          <input
            id="cash" type="number" value={cash} min="0.01" step="1000"
            onChange={(e) => onCash(e.target.value)}
          />
        </label>
        <label htmlFor="fee">
          费率(0 ≤ fee &lt; 0.1)
          <input
            id="fee" type="number" value={fee} min="0" max="0.0999" step="0.0001"
            onChange={(e) => onFee(e.target.value)}
          />
        </label>
      </fieldset>

      <button id="run-button" onClick={onRun} disabled={!enabled}>
        {running ? '跑着呢…' : '跑'}
      </button>
    </section>
  )
}
