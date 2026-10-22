/** T13 · 回执式汇总(等宽、框线对齐)—— 形态取自批准稿(ADR-046)。
 *
 * **I8**:每个数字都**原样**取自后端的 `summary` 字段,只做 ADR-025 允许的
 * 五类格式化。例如交易次数读 `summary.trade_count`,**不是** `trades.length`
 * —— 后者是前端自行推导,而且不含算术,连 grep 都抓不到。
 *
 * 为什么是 `<pre>` 而不是 `<dl>`:这是「一本账」的产物,回执是账的固有形态
 * —— 等宽对齐让数字可以上下对读。批准稿如此,非装饰选择。
 */

import type { StrategyResult } from '../types'
import { integer, money, orNull, percent, percentUnsigned, ratio } from '../format'

const BAR = '═'.repeat(43)
const THIN = '─'.repeat(43)

/** 回执里的一小段文字。`cls` 决定配色(`.receipt .n` 等),无 `cls` 即正文。 */
interface Span {
  cls?: string
  text: string
}

/** 一行 = 若干段。 */
type Line = Span[]

/** 右对齐到固定宽度 —— 纯字符串排版,不是算术。 */
function pad(s: string, w: number): string {
  return s.length >= w ? s : ' '.repeat(w - s.length) + s
}

/** 「标签 + 值」行。标签占固定宽度,值的样式默认是 `n`(高亮数字),
 *  故等宽字体下所有值天然上下对齐 —— 回执形态的要点(ADR-046)。 */
function row(label: string, value: string, cls = 'n', ...rest: Line): Line {
  return [{ text: label }, { cls, text: value }, ...rest]
}

/** 分隔线、提示语等整行同色的内容。 */
function rule(text: string, cls = 'hr'): Line {
  return [{ cls, text }]
}

export interface SummaryPanelProps {
  results: StrategyResult[]
  assumptions: string[]
  /** ADR-056 机制 2「报告里显示生效值」—— 每张图、每个 JSON 都自带
   *  「这条曲线假设了多少成本」。该机制是 ADR-056 用来**替代非零默认值**
   *  的诚实性论证本身:后端有、界面无,等于这条论证在 Web 上断掉。
   *  `undefined` = 本次没跑(空态),不渲染该行。 */
  slippage?: number
  /** 本次运行的费率 —— 来自请求,后端原样回传。 */
  fee?: number
  /** 多策略时按策略的数字交给对比表,回执只留**整次运行**的属性
   *  (区间/交易日数/初始资金/费率/对账行/假设)——
   *  两处相邻列出同一组数字会让人以为自己漏看了某个差别。
   *
   *  **对账行与假设文案仍然留在回执里**:它们是整次运行的属性,
   *  且是 ADR-021/I3 在界面上的落点,不随策略数量消失。 */
  perStrategy?: boolean
}

export default function SummaryPanel({
  results, assumptions, fee, slippage, perStrategy = true,
}: SummaryPanelProps) {
  if (results.length === 0) return null
  // 区间/交易日数/初始资金对同一次运行的所有策略都相同,取第一条。
  const head = results[0].summary

  const lines: Line[] = []

  // ── 表头:整次运行的属性 ──
  lines.push(
    rule(BAR),
    [{ text: ' ' }, { cls: 'ttl', text: '回 测 结 果' }],
    rule(BAR),
    row(' 区间        ', `${head.start} ~ ${head.end}`),
    row(' 交易日数    ', integer(head.bars)),
    row(' 初始资金    ', money(head.initial_cash)),
  )
  if (fee !== undefined) {
    // 零费率要标出来 —— 否则「赚了 8 倍」会被当成含成本的结果读。
    const note: Line = fee === 0 ? [{ cls: 'hr', text: '  (零成本基准)' }] : []
    lines.push(row(' 手续费率    ', fee.toFixed(4), 'n', ...note))
  }
  if (slippage !== undefined) {
    // 与费率**并列且同口径**(4 位小数)。零滑点同样要标出来,
    // 理由比零费率更强:零佣金在今天的美股零售是**现实的**,
    // 零滑点**永远不现实**(ADR-059 的声明逐字这么写)。
    // 故这里的注记不是「零成本基准」而是「不可实现」。
    const note: Line = slippage === 0
      ? [{ cls: 'hr', text: '  (零滑点 · 不可实现)' }]
      : []
    lines.push(row(' 滑点率      ', slippage.toFixed(4), 'n', ...note))
  }
  lines.push(rule(THIN))

  // ── 按策略的数字:单策略时在回执里,多策略时交给对比表 ──
  if (perStrategy) {
    results.forEach((r, i) => {
      const s = r.summary
      if (i) lines.push([{ text: '' }])     // 策略之间空一行
      lines.push(
        [{ text: ' ' }, { cls: 'n', text: r.strategy }],
        row('   期末资产  ', money(s.final_equity)),
        // 正负配色是纯判断,不是算术(ADR-025 第 5 条)
        row('   总收益    ', percent(s.total_return_pct),
            s.total_return_pct >= 0 ? 'pos' : 'neg'),
        row('   交易次数  ', pad(integer(s.trade_count), 6)),
        // ── ADR-058 的两个风险指标 ── 按【策略】显示:它们从 equity 算,
        // 故是策略的性质,不是数据的性质(与跳空相反)。
        //
        // 夏普用 `ratio` —— **裸比率,不追加 `%`**。空值标记表示该值在
        // 数学上**未定义**,不表示 0(ADR-059 的声明逐字覆盖这一点)。
        row('   夏普比率  ', pad(ratio(s.sharpe), 6)),
        // 回撤是**正数幅度**,用 percentUnsigned —— 带符号格式化器会引入
        // 双重负号的显示 bug。前端**不取绝对值**:后端给的就是最终值。
        row('   最大回撤  ', pad(percentUnsigned(s.max_drawdown_pct), 7), 'n',
            { cls: 'hr',
              text: `  ${orNull(s.max_drawdown_peak_date)} ~ ${orNull(s.max_drawdown_trough_date)}` }),
      )
    })
  } else {
    lines.push(
      row(' 策略        ', results.map((r) => r.strategy).join(', ')),
      rule(' 按策略的数字见下方「并排对比」'),
    )
  }

  // ── 跳空、对账与假设:整次运行的属性,不随策略数量消失 ──
  lines.push(
    rule(THIN),
    row(' 最大跳空    ', percent(head.max_gap_pct), 'n',
        { cls: 'hr', text: `  ${orNull(head.max_gap_date)}` }),
    row(' 均值跳空    ', pad(percentUnsigned(head.mean_abs_gap_pct), 7)),
    rule(THIN),
    // I3/ADR-021 在界面上的落点 —— 显式写成 span,门按这行字面量定位它。
    [{ cls: 'chk', text: ' ✓ 对账:每日 现金+持股市值 == 总资产' }],
  )
  // ADR-021:后端逐字给的两条假设,不得改写
  assumptions.forEach((a) => lines.push(rule(` ⚠ ${a}`, 'wn')))
  lines.push(rule(BAR))

  return (
    <pre className="receipt" aria-label="回测汇总">
      {lines.map((spans, i) => (
        <span key={i}>
          {spans.map((s, j) => <span key={j} className={s.cls}>{s.text}</span>)}
          {i < lines.length - 1 ? '\n' : ''}
        </span>
      ))}
    </pre>
  )
}
