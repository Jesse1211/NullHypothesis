/** 读 CSS 自定义属性 —— 曲线、图例色块、对比表色标都要取同一组颜色。
 *
 * 三个组件各抄一份 `cssVar` 时,「jsdom 里 getComputedStyle 缺失就退回
 * 兜底色」这个判断也被抄了三份 —— 改一处等于漏两处。
 */

/** `getComputedStyle` 不可用(或 token 读不到)时的兜底 —— 即 `--accent`
 *  的亮色值。图表在无布局引擎的环境里也得有个能画的颜色。 */
const FALLBACK = '#2C7BCE'

export function cssVar(name: string): string {
  if (typeof getComputedStyle !== 'function') return FALLBACK
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || FALLBACK
}
