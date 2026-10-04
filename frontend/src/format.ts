/** ADR-025 **穷举**的格式化种类。前端不得做这五类以外的任何算术。
 *
 * 「格式化」与「计算」的界线:把 `816.06` 显示成 `+816.06%` 是格式化;
 * 由 `equity[-1] / initial - 1` 得到 `816.06` 是**计算**,后者属后端。
 */

/** `contracts.yaml` 的 `formats.null_display` —— 无跳空时的显示。 */
export const NULL_DISPLAY = '—'

/** 千分位 + 固定 2 位小数。 */
export function money(n: number): string {
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/** **只追加 `%` 与符号**。后端给的已是百分数(已乘 100)。 */
export function percent(n: number | null): string {
  if (n === null || n === undefined) return NULL_DISPLAY
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

/** 不带符号的百分数(平均绝对跳空本就非负,加 `+` 会误导)。 */
export function percentUnsigned(n: number | null): string {
  if (n === null || n === undefined) return NULL_DISPLAY
  return `${n.toFixed(2)}%`
}

export function integer(n: number): string {
  return n.toLocaleString('zh-CN')
}

export function orNull(s: string | null): string {
  return s === null || s === undefined || s === '' ? NULL_DISPLAY : s
}

/** 归档图像的 URL(ADR-045)。
 *
 * `png_path` 是归档里的**相对路径**(`out/<run_id>/<stem>_equity.png`),
 * 而 `out/` 不是静态目录 —— 图像走专用端点。这里只取文件名再拼端点 URL,
 * 属 ADR-025 允许的路径拼接,不是金融量算术。
 */
export function pngUrl(runId: string, pngPath: string): string {
  const name = pngPath.split('/').pop() ?? ''
  return `/api/runs/${encodeURIComponent(runId)}/png/${encodeURIComponent(name)}`
}
