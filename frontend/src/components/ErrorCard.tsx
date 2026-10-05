/** T14 · 错误卡片。ADR-031 的每个 `code` 各有自己的可读形态。
 *
 * 命令行文本直接贴到网页上很难读 —— 结构化后能突出显示行号/日期。
 *
 * 类名用 `.errcard .eh .code .msg .eb`,与 ADR-046 的批准稿同名 ——
 * `styles.css` 只为这一组写了样式,改名等于让卡片回到无样式状态。
 */

import type { ApiError } from '../types'

export interface ErrorCardProps {
  error: ApiError
}

const TITLES: Record<ApiError['code'], string> = {
  DATA_VALIDATION: '数据校验失败',
  STRATEGY_ERROR: '策略抛异常',
  NOT_FOUND: '找不到',
  INVALID_REQUEST: '请求不合法',
}

/** `0` 与空串都是有意义的值,只有 `null`/`undefined` 才算「没有」——
 *  `{d.row && …}` 会把 `row: 0`(第一行)整行吞掉。 */
function has(v: unknown): boolean {
  return v !== null && v !== undefined && v !== ''
}

/** `<dt>/<dd>` 成对出现,故不能包在 `<div>` 里 —— `.errcard dl` 是
 *  两列 grid,中间插一层 div 会让列对齐失效。 */
function Rows(props: { items: Array<[string, unknown]> }) {
  return (
    <dl>
      {props.items
        .filter(([, value]) => has(value))
        .map(([label, value]) => (
          <span key={label} style={{ display: 'contents' }}>
            <dt>{label}</dt>
            <dd>{String(value)}</dd>
          </span>
        ))}
    </dl>
  )
}

export default function ErrorCard({ error }: ErrorCardProps) {
  const d = error.detail ?? {}

  return (
    <section className="errcard" role="alert" data-code={error.code}>
      {/* 头部:机器码 + 人话标题。`.msg` 是批准稿里加粗的那一行,
          放**后端那句 message** —— 它才是「到底怎么了」。 */}
      <div className="eh">
        <span className="code">{error.code}</span>
        <span className="msg">{error.message}</span>
      </div>

      <div className="eb">
        <p className="title">{TITLES[error.code] ?? '出错了'}</p>

        {error.code === 'DATA_VALIDATION' && (
          <Rows items={[['文件', d.file], ['行号', d.row], ['列', d.column]]} />
        )}

        {error.code === 'STRATEGY_ERROR' && (
          <>
            <Rows
              items={[
                ['策略', d.strategy],
                // 序号对人显示从 1 起 —— 这是**序号重基**,不是金融量算术
                ['交易日序号', has(d.bar_index) ? d.bar_index! + 1 : null],
                ['日期', d.date],
              ]}
            />
            {d.traceback && <pre>{d.traceback}</pre>}
          </>
        )}
      </div>
    </section>
  )
}
