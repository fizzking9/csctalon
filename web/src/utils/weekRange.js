/**
 * 周区间计算（中国习惯：**周一起始**）
 *
 * 【设计说明（Design Rationale）】
 * - **目的**：消除「同一个"本周"在不同页面算出不同区间」的口径漂移。
 * - **逻辑**：抽成单一来源。此前 `Today.vue` 与 `OperationDashboard.vue`
 *   各写一份周计算，写法还不一致 —— 前者用 `day()` 手算偏移（正确），
 *   后者用 `startOf('week').add(1,'day')`（**周日会算错**）。
 * - **口径**：周一 00:00 ~ 周日 23:59:59（中国习惯）。
 * - **取舍**：不引第三方周插件（dayjs 的 weekOfYear/isoWeek 插件语义不同，
 *   混用反而更易错）；不改成「周日起始」（业务日报以周一为起始周）。
 * - **工单**：#78（顺带修复）；原 bug 现象见下方注释。
 *
 * ⚠️ **修复的存量 bug**（2026-09-13 实测）：
 *   `dayjs().startOf('week')` 的周起始是**周日**（dayjs 默认 locale），
 *   再 `.add(1, 'day')` 会得到**下周一**。
 *   于是「今天恰好是周日」时，「本周」被算成 09-14~09-20（未来一周）→ 指标全为 0。
 *
 *   ```
 *   today = 2026-09-13 (周日)
 *   startOf('week') = 2026-09-13
 *   .add(1,'day')   = 2026-09-14   ← 错！本应是 09-07
 *   本周区间 = 09-14 ~ 09-20（未来）
 *   ```
 */
import dayjs from 'dayjs'

/**
 * 计算某日期所在周（周一~周日）。
 *
 * @param {*} base 基准日期（dayjs 可解析的任意值），默认今天
 * @param {number} offsetWeeks 偏移周数（0=本周，1=上周，-1=下周）
 * @returns {{start: dayjs.Dayjs, end: dayjs.Dayjs}} Monday 00:00 ~ Sunday 23:59:59.999
 */
export function getWeekRange(base = undefined, offsetWeeks = 0) {
  const d = base ? dayjs(base) : dayjs()
  // dayjs.day(): 0=周日, 1=周一, ..., 6=周六
  const dow = d.day()
  const diffToMonday = dow === 0 ? -6 : 1 - dow
  const monday = d.add(diffToMonday - offsetWeeks * 7, 'day').startOf('day')
  return { start: monday, end: monday.add(6, 'day').endOf('day') }
}

/**
 * 周区间字符串（`YYYY-MM-DD`），用于接口的 start/end 参数。
 *
 * @param {*} base 基准日期，默认今天
 * @param {number} offsetWeeks 偏移周数
 * @param {boolean} clampToNow 是否把结束日截到今天（避免拉取未来日期）。
 *   历史周（offsetWeeks>0）无需截断；本周建议截断。
 */
export function getWeekRangeStr(base = undefined, offsetWeeks = 0, clampToNow = false) {
  const { start, end } = getWeekRange(base, offsetWeeks)
  let realEnd = end
  if (clampToNow) {
    const nowEnd = dayjs().endOf('day')
    if (realEnd.isAfter(nowEnd)) realEnd = nowEnd
  }
  return { start: start.format('YYYY-MM-DD'), end: realEnd.format('YYYY-MM-DD') }
}

/** 周标签：`本周` / `上周` / `MM-DD~MM-DD` */
export function weekLabel(offsetWeeks) {
  if (offsetWeeks === 0) return '本周'
  if (offsetWeeks === 1) return '上周'
  const { start, end } = getWeekRange(undefined, offsetWeeks)
  return `${start.format('MM-DD')}~${end.format('MM-DD')}`
}
