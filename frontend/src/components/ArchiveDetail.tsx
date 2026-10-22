/** T14 · 归档详情 —— **主栏**(由 ADR-048 从 `HistoryList` 拆出)。
 *
 * **ADR-032 原文**:归档项**只显示 PNG 快照**,不重建交互图 —— 归档的意义
 * 是「当时跑出来就是这样」,用当时那张图,而不是用今天的代码重画一遍。
 * 图像 URL 走专用端点(ADR-045),不是 `/out/...` 静态路径。
 *
 * **T26 修订(用户决策)**:上面那条**在有 `equity[]` 的归档上已退役**。
 * 原论证的前提是「重画 = 用今天的代码重算」,而 T26 让 `run.json` 存下
 * **当时那次的逐日 `equity` 序列**,于是重画只是把**当时的数**换一种画法 ——
 * 没有任何数字被重算,「当时跑出来就是这样」仍然成立。被推翻的只是
 * 「忠于历史 ⇒ 必须是静态图」这一步推理。
 *
 * 代价已知且被接受:曲线的**渲染方式**会随前端代码演进(今天是双轴 Recharts,
 * 将来可能不同),而 PNG 不会。换来的是历史与当次**同款**的交互图。
 *
 * **没有 `equity[]` 的归档仍走 PNG** —— 旧 checkout 写的归档只有 PNG,而
 * `run.json` 是跨阶段锁定的契约。这不是过渡期的临时分支。
 *
 * **「不重建 Recharts」不等于「不显示汇总」**:ADR-032 把 `GET /api/runs/{id}`
 * 定义为「该次的**汇总** + 归档 PNG」,故汇总字段在范围内,受限的只是交互图。
 * ADR-058 的夏普与最大回撤因此在此渲染(手动 QA 发现它们原先完全缺失)。
 *
 * 本组件对**所有**可选字段(`slippage` / `sharpe` / `max_drawdown_pct`)
 * 一律遵循同一条规则:**键缺失 → 整段不渲染;键存在但为 `null` → 空值标记**。
 * 二者含义截然不同(「当年没记录」vs「数学上未定义」),混为一谈就是替历史编数据。
 */

import type { ArchivedRun } from '../types'
import { integer, money, orNull, percent, percentUnsigned, pngUrl, ratio } from '../format'
import EquityChart from './EquityChart'

export interface ArchiveDetailProps {
  run: ArchivedRun | null
}

export default function ArchiveDetail({ run }: ArchiveDetailProps) {
  if (!run) {
    return (
      <div className="emptybox big">
        <div className="eh">选一次归档</div>
        <div>左边点一条,这里显示当时那次跑出来的曲线与汇总。</div>
      </div>
    )
  }

  return (
    <div className="archive-detail">
      <div className="zhead">
        <span className="ztitle">{run.run_id}</span>
        <span className="zsub">
          {run.request.data_file} · 初始 {money(run.request.cash)} · 费率 {run.request.fee}
          {/* ADR-056 机制 2:生效的滑点值必须**被显示**,与 `费率` 并列 ——
              「诚实来自声明与可见性,不来自默认值的大小」。阶段二的归档没有
              这个键(加性扩展),故 `undefined` 时整段不渲染,而**不是**显示
              `0` —— 把「当年没记录」说成「当年滑点为零」是在替历史编数据。
              直接取 JSON 字段、零算术(I8),与上面的 `费率` 同口径。 */}
          {run.request.slippage !== undefined
            && <> · 滑点 {run.request.slippage}</>}
        </span>
      </div>

      {run.results.map((r) => (
        <figure key={r.strategy}>
          <figcaption>
            {r.strategy} · 期末 {money(r.summary.final_equity)}
            {' · '}{percent(r.summary.total_return_pct)}
            {' · '}{integer(r.summary.trade_count)} 笔
            {/* ADR-032 把本端点定义为「该次的**汇总** + 归档 PNG」—— 受限的是
                「不重建 Recharts」,**汇总本就在范围内**。故 ADR-058 的两个
                风险指标必须在这里可见:否则这个 epic 的头号产出对**全部历史
                运行**在界面上不存在,用户得去调 API 才看得到。

                **`undefined`(阶段二的归档没这两个键)与 `null`(数学上未定义)
                必须区别对待** —— 这正是上面 `滑点` 那段注释的论证:
                「把『当年没记录』说成『当年滑点为零』是在替历史编数据」。
                同理:阶段二的买入持有跑出 `+145%`,它的夏普**完全有定义**,
                只是当年没算;若此处渲染空值标记,就等于在屏幕上断言
                「数学上未定义」—— 而同屏的 ADR-059 声明逐字这么解释空值标记。
                故**键缺失时整段不渲染**(与 `滑点` 同构),只有键存在时才显示,
                此时空值标记的含义才真的是「未定义」。 */}
            {r.summary.sharpe !== undefined && (
              <>{' · '}夏普 {ratio(r.summary.sharpe)}</>
            )}
            {r.summary.max_drawdown_pct !== undefined && (
              <>
                {' · '}回撤 {percentUnsigned(r.summary.max_drawdown_pct)}
                {/* 峰谷日期:两者在无回撤时均为 `null`(ADR-058)——
                    给日期等于断言发生过一次没发生的回撤。单调上涨的归档
                    会显示 `0.00% (N/A ~ N/A)`,那是对的。 */}
                {' '}({orNull(r.summary.max_drawdown_peak_date)}
                {' ~ '}{orNull(r.summary.max_drawdown_trough_date)})
              </>
            )}
          </figcaption>
          {/* ADR-032 修订(T26):有 `equity[]` 就用**与「跑一次」同一个**
              `EquityChart` 重画 —— 双 y 轴(左净值 / 右价格)、hover 十字线、
              图例点击切换、初始资金基准虚线全部到位,因为它**就是同一个组件**,
              不是一个长得像的副本。

              `initialCash` 读 `request.cash`(该次运行的**请求**值),不读
              `summary.initial_cash` —— 后者在归档 schema 里确实也有,但基准
              虚线问的是「当时投了多少钱」,那是请求的性质(与 ADR-056 把
              `fee`/`slippage` 归为请求性质同一条理由)。

              键缺失 → 回退到 PNG 快照。这条回退**不能删**:旧 checkout 写的
              归档只有 PNG,而 `run.json` 是跨阶段锁定的契约。 */}
          {r.equity
            ? (
              <EquityChart
                results={[{
                  strategy: r.strategy,
                  equity: r.equity,
                  summary: { final_equity: r.summary.final_equity },
                }]}
                initialCash={run.request.cash}
              />
            )
            /* 归档快照;URL 见 ADR-045 */
            : <img src={pngUrl(run.run_id, r.png_path)} alt={`${r.strategy} 净值曲线`} />}
        </figure>
      ))}

      {run.comparison_png_path && (
        <figure>
          <figcaption>对比</figcaption>
          <img src={pngUrl(run.run_id, run.comparison_png_path)} alt="多策略对比" />
        </figure>
      )}

      {/* ADR-021:归档也要带着那两条假设 —— 它们是读这些数字的前提 */}
      <ul className="assumptions">
        {run.assumptions.map((a) => <li key={a}>⚠ {a}</li>)}
      </ul>
    </div>
  )
}
