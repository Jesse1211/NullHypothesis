/** T12 · 左栏表单(`rail`)。只接 props,不自己取数。 */

import { seriesColor } from './EquityChart'

export interface RunFormProps {
  strategies: string[]
  dataFiles: string[]
  selectedStrategies: string[]
  dataFile: string | null
  cash: string
  fee: string
  /** ADR-056 的滑点率,与 `fee` **同口径**(小数,`0.0005` = 5bps)。
   *  机制 1「参数存在即是声明」—— 它必须出现在表单里,
   *  否则「滑点 = 0」是个无人提及的隐含假设而不是被显示的选择。 */
  slippage: string
  running: boolean
  onToggleStrategy: (name: string) => void
  onDataFile: (name: string) => void
  onCash: (v: string) => void
  onFee: (v: string) => void
  onSlippage: (v: string) => void
  onRun: () => void
}

/** 「跑」按钮启用的**充要条件**(T12 的门逐条测):
 *    `selectedStrategies.length >= 1`
 *  且 `dataFile != null`
 *  且 `cash` 为 `> 0` 的数字
 *  且 `fee` 在 `[0, 0.1)`
 *  且 `slippage` 在 `[0, 0.1)`(与 `fee` **逐字对称** —— ADR-056/ADR-039)
 */
export function canRun(p: {
  selectedStrategies: string[]
  dataFile: string | null
  cash: string
  fee: string
  slippage: string
}): boolean {
  if (p.selectedStrategies.length < 1) return false
  if (p.dataFile === null) return false
  if (p.cash.trim() === '') return false
  const cash = Number(p.cash)
  if (!Number.isFinite(cash) || cash <= 0) return false
  if (p.fee.trim() === '') return false
  const fee = Number(p.fee)
  if (!Number.isFinite(fee) || fee < 0 || fee >= 0.1) return false
  // 滑点的校验与费率**逐字对称**(ADR-056:「与 --fee 的校验式逐字对称」)
  if (p.slippage.trim() === '') return false
  const slippage = Number(p.slippage)
  if (!Number.isFinite(slippage) || slippage < 0 || slippage >= 0.1) return false
  return true
}

// OQ-08:数据文件**超过 20 项**才渲染过滤框。这是个**双边**约束 ——
// 恒加和恒不加都违反它,故 T12 的门用 21 项与 20 项双向断言。
export const FILTER_THRESHOLD = 20

export default function RunForm(props: RunFormProps) {
  const {
    strategies, dataFiles, selectedStrategies, dataFile, cash, fee, slippage,
    running, onToggleStrategy, onDataFile, onCash, onFee, onSlippage, onRun,
  } = props

  const ok = canRun(props)
  const enabled = ok && !running

  return (
    <aside className="rail">
      {/* `nav-group`:整组**满铺**左栏(标题 + 列表),读起来是侧边导航而非
          表单里的一个小 box。只让 `.pick` 满铺不够 —— 标题与提示若仍内缩,
          列表会夹在两段缩进文字之间,整体依旧是个盒子。 */}
      <div className="fgroup nav-group">
        <fieldset>
          <legend className="lbl">策略 · strategies/</legend>
          {strategies.length === 0 ? (
            <p className="hint">strategies/ 目录为空 —— 放一个 .py 进去再刷新。</p>
          ) : (
            <div className="picklist">
              {strategies.map((s) => {
                const idx = selectedStrategies.indexOf(s)
                const on = idx >= 0
                // 色块与曲线同色 —— 第二条用 series-2,与预览稿一致
                const cls = `pick${on ? ' on' : ''}${on && idx === 1 ? ' s2' : ''}`
                return (
                  <label className={cls} key={s} data-k={s}>
                    <span
                      className="swatch"
                      style={on ? { background: seriesColor(idx) } : undefined}
                    />
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={() => onToggleStrategy(s)}
                    />
                    <span className="nm">{s}</span>
                  </label>
                )
              })}
            </div>
          )}
          <p className="hint">
            多选 2+ 条即叠加对比(ADR-029)。策略只能按文件名引用 ——
            界面不提供在线编辑(ADR-026)。
          </p>
        </fieldset>
      </div>

      <div className="fgroup">
        <label className="lbl" htmlFor="data-file">数据 · data/</label>
        {dataFiles.length === 0 ? (
          <p className="hint">data/ 目录为空 —— 放一个 .csv 进去再刷新。</p>
        ) : (
          <>
            {dataFiles.length > FILTER_THRESHOLD && (
              <input
                id="data-filter" className="filter" type="search"
                placeholder="过滤数据文件…" aria-label="过滤数据文件"
              />
            )}
            <div className="selwrap">
              <select
                className="sel" id="data-file" value={dataFile ?? ''}
                onChange={(e) => onDataFile(e.target.value)}
                aria-label="数据文件"
              >
                <option value="" disabled>请选择…</option>
                {dataFiles.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
          </>
        )}
      </div>

      <div className="fgroup vstack">
        <div>
          <label className="lbl" htmlFor="cash">初始资金</label>
          <div className="field">
            <input
              id="cash" value={cash} inputMode="numeric"
              onChange={(e) => onCash(e.target.value)}
            />
            <span className="unit">USD</span>
          </div>
        </div>
        <div>
          <label className="lbl" htmlFor="fee">手续费率</label>
          <div className="field">
            <input
              id="fee" value={fee} inputMode="decimal"
              onChange={(e) => onFee(e.target.value)}
            />
            <span className="unit">×额</span>
          </div>
        </div>
        {/* ADR-056 机制 1:参数存在即是声明。与 `手续费率` 并列、同口径
            (小数而非 bps —— 同一张表里两种单位正是本阶段开篇指出的
            那类单位不一致隐患)。缺了它,界面只能跑后端默认的 0
            且不告知,而同屏声明却写着「零滑点永远不现实」。 */}
        <div>
          <label className="lbl" htmlFor="slippage">滑点率</label>
          <div className="field">
            <input
              id="slippage" value={slippage} inputMode="decimal"
              onChange={(e) => onSlippage(e.target.value)}
            />
            <span className="unit">×价</span>
          </div>
        </div>
      </div>

      <button
        className={`runbtn${running ? ' busy' : ''}`} id="run-button"
        type="button" onClick={onRun} disabled={!enabled}
      >
        {running ? <><span className="spin" />计算中…</> : '跑'}
      </button>
      <p className="hint" id="run-hint">
        {selectedStrategies.length === 0
          ? '至少选一条策略才能跑。'
          : !ok
            ? '资金须 > 0,费率与滑点率须在 [0, 0.1) 内。'
            : '同步请求,运行期间按钮禁用(ADR-028)。'}
      </p>
    </aside>
  )
}
