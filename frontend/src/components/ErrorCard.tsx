/** T14 · 错误卡片。ADR-031 的每个 `code` 各有自己的可读形态。
 *
 * 命令行文本直接贴到网页上很难读 —— 结构化后能突出显示行号/日期。
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

export default function ErrorCard({ error }: ErrorCardProps) {
  const d = error.detail ?? {}

  return (
    <section className="error-card" role="alert" data-code={error.code}>
      <h3>{TITLES[error.code] ?? '出错了'}</h3>
      <p className="message">{error.message}</p>

      {error.code === 'DATA_VALIDATION' && (
        <dl className="detail">
          {d.file && (<><dt>文件</dt><dd>{d.file}</dd></>)}
          {d.row !== null && d.row !== undefined && (<><dt>行号</dt><dd>{d.row}</dd></>)}
          {d.column && (<><dt>列</dt><dd>{d.column}</dd></>)}
        </dl>
      )}

      {error.code === 'STRATEGY_ERROR' && (
        <>
          <dl className="detail">
            {d.strategy && (<><dt>策略</dt><dd>{d.strategy}</dd></>)}
            {d.bar_index !== null && d.bar_index !== undefined && (
              <><dt>交易日序号</dt><dd>{d.bar_index + 1}</dd></>
            )}
            {d.date && (<><dt>日期</dt><dd>{d.date}</dd></>)}
          </dl>
          {d.traceback && <pre className="traceback">{d.traceback}</pre>}
        </>
      )}

      {(error.code === 'NOT_FOUND' || error.code === 'INVALID_REQUEST') && (
        <p className="code">错误码:<code>{error.code}</code></p>
      )}
    </section>
  )
}
