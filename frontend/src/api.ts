/** `fetch` 封装。**唯一**与后端通信的地方(ADR-037b)。
 *
 * T13/T14 的组件只接 props,不自己取数 —— 这是三个任务并行写同一个 `src/`
 * 而不互相覆盖的保障。
 */

import type { ApiError, ArchivedRun, RunListItem, RunResponse } from './types'

export class ApiFailure extends Error {
  readonly payload: ApiError
  constructor(payload: ApiError) {
    super(payload.message)
    this.payload = payload
  }
}

/** 后端连不上时最该防的事:**白屏**。
 *
 * 三条失败路径各自不同,必须分别处理:
 *   1. `fetch` reject —— 网络层失败(后端没起)
 *   2. `200` + `text/html` —— vite 代理返回 502 HTML 页。**真实的不可达
 *      后端走的正是这条**,而 `response.json()` 在这里抛的是解析错,
 *      只 mock reject 的 `catch` 往往处理不了它
 *   3. 非 2xx + JSON 错误体 —— 后端的结构化错误(ADR-031)
 */
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, init)
  } catch (e) {
    throw new ApiFailure({
      code: 'NOT_FOUND',
      message: `连不上后端(${url})——它起来了吗?`,
      detail: { value: String(e) },
    })
  }

  const ctype = res.headers.get('content-type') ?? ''
  if (!ctype.includes('application/json')) {
    throw new ApiFailure({
      code: 'NOT_FOUND',
      message:
        `后端返回的不是 JSON(HTTP ${res.status}, content-type: ${ctype || '空'})`
        + ' —— 多半是代理指向了一个没在跑的后端。',
      detail: { value: ctype },
    })
  }

  let body: unknown
  try {
    body = await res.json()
  } catch (e) {
    throw new ApiFailure({
      code: 'NOT_FOUND',
      message: `后端返回的 JSON 解析失败(HTTP ${res.status})`,
      detail: { value: String(e) },
    })
  }

  if (!res.ok) {
    const p = body as Partial<ApiError>
    throw new ApiFailure({
      code: p.code ?? 'INVALID_REQUEST',
      message: p.message ?? `HTTP ${res.status}`,
      detail: p.detail ?? {},
    })
  }
  return body as T
}

export const listStrategies = () => request<string[]>('/api/strategies')
export const listData = () => request<string[]>('/api/data')
export const listRuns = () => request<RunListItem[]>('/api/runs')
export const getRun = (id: string) => request<ArchivedRun>(`/api/runs/${id}`)

export const run = (body: {
  strategies: string[]
  data_file: string
  cash: number
  fee: number
}) =>
  request<RunResponse>('/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
