/** ADR-025 **穷举**的格式化种类。前端不得做这五类以外的任何算术。
 *
 * 「格式化」与「计算」的界线:把 `816.06` 显示成 `+816.06%` 是格式化;
 * 由 `equity[-1] / initial - 1` 得到 `816.06` 是**计算**,后者属后端。
 */

/** `contracts.yaml` 的 `formats.null_display` 的**逐字镜像**。
 *
 * **必须与该 key 逐字相等。** 此前本常量硬编码为 `'—'`(em dash),而契约写的是
 * `N/A` —— 后端是**运行时读契约**的(`report.py` 的 `format_field`),前端是
 * 硬编码,于是**同一次运行在 CLI 与 Web 上显示两个不同的空值符号**:
 * CLI 打 `夏普比率  N/A`,界面打 `夏普比率  —`。
 *
 * `contracts.yaml` 在文件里自称「契约本体」,表现层偏离它正是该文件存在要防的
 * 漂移。而原先的注释写着「`contracts.yaml` 的 `formats.null_display`」——
 * 那句话**是假的**,它描述的不是实际取值。
 *
 * 前端无法在运行时读 YAML(无该依赖、且 I8 不允许前端解析契约),故这里是
 * 一处**不可避免的镜像**。守护它的是 `layout.test.ts` 的一条门:它读
 * `contracts.yaml` 的文本并断言本常量与之逐字相等 —— 改了契约而没改这里,
 * 门会红。*(手动 QA 发现:`render.test.tsx` 原先断言字面量 `'—'`,
 * 于是测试把漂移**编码**进去了,而不是抓住它。)*
 */
export const NULL_DISPLAY = 'N/A'

/** 千分位 + 固定 2 位小数。 */
export function money(n: number): string {
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/** **只追加 `%` 与符号**。后端给的已是百分数(已乘 100)。 */
export function percent(n: number | null | undefined): string {
  if (n === null || n === undefined) return NULL_DISPLAY
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

/** 不带符号的百分数(平均绝对跳空本就非负,加 `+` 会误导)。 */
export function percentUnsigned(n: number | null | undefined): string {
  if (n === null || n === undefined) return NULL_DISPLAY
  return `${n.toFixed(2)}%`
}

/** 裸比率(`contracts.yaml` 的 `formats.ratio`)—— 夏普用这个。
 *
 * **不追加 `%`、不强加 `+`**:夏普不是百分数,而 `percent` 两者都会做。
 * `null` 表示该值在**数学上未定义**(策略不交易、或收益无波动、或样本不足
 * 2 个收益),**不表示 0** —— 故显示空值标记而非 `0.00`(ADR-058/059)。
 */
export function ratio(n: number | null | undefined): string {
  if (n === null || n === undefined) return NULL_DISPLAY
  return n.toFixed(2)
}

export function integer(n: number): string {
  return n.toLocaleString('zh-CN')
}

/** 字符串的空值渲染。
 *
 * 签名含 `undefined` 是**有意的**:归档的 `ArchivedSummary` 里这些日期键
 * 可能整个不存在(阶段二的归档),而函数体本来就已经在运行时处理 `undefined`
 * —— 签名不写它只会逼调用方加一个 `as`,把类型系统的保护丢掉。
 */
export function orNull(s: string | null | undefined): string {
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

/** 千分位、无小数 —— 图表轴刻度与图例末值用(ADR-030 的豁免 + 截断)。 */
export function money0(n: number): string {
  return Math.round(n).toLocaleString('zh-CN')
}
