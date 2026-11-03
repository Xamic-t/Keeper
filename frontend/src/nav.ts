/**
 * 导航与路由的**单一事实源**（2026-11-03 新增）。
 *
 * 为什么抽出来：连续两次出问题都是同一类——
 *   「助理」加了一级菜单，但设置页里那张旧卡片没删；
 *   「账号管理」进了后台，但业务菜单里那个「账号」还留着。
 * 页面加了、旧入口没清，用户看到的就是"层级乱"。
 *
 * 所以把"哪些页在一级菜单里、哪些页只从别处进"写成数据，
 * 再用 `router/ia.test.ts` 断言**每条路由都必须被明确归类**：
 * 新加页面忘了归类，测试直接红。
 */

export interface NavItem {
  /** vue-router 的 route name */
  name: string
  label: string
  icon: string
}

/** 业务区一级菜单。桌面侧栏与手机底部 tab 共用这一份。 */
export const BUSINESS_NAV: NavItem[] = [
  { name: 'today', label: '今日', icon: '◎' },
  { name: 'ingest', label: '投喂', icon: '↥' },
  { name: 'assistant', label: '助理', icon: '✦' },
  { name: 'persons', label: '人', icon: '☺' },
  { name: 'confirm', label: '确认', icon: '✓' },
  { name: 'calendar', label: '日历', icon: '▦' },
  { name: 'todos', label: '待办', icon: '☑' },
]

/**
 * 不在一级菜单、但**有明确入口**的页面。
 * 加一条必须写清"从哪进"，否则下一个人（或三个月后的我）会以为是孤儿。
 */
export const SECONDARY_ROUTES: Record<string, string> = {
  settings: '左下角账号区「设置」',
  person: '「人」列表点进详情',
  admin: '左下角账号区「管理后台」（仅 owner）',
}

/**
 * 已知的孤儿路由：**从界面上到不了**，只能手输地址。
 * 放在这里不是"允许它存在"，而是让它显形——`ia.test.ts` 会把它当作"待清理项"报出来。
 * 目前只有对话式投喂 `/chat`（与「投喂」「助理」语义重叠，见 docs/36 D-4）。
 */
export const ORPHAN_ROUTES: string[] = ['chat']

/** 没有外壳的页面：登录页、管理后台（有自己的侧栏）。 */
export const BARE_LAYOUT_ROUTES: string[] = ['login', 'admin']

export function isNavActive(current: string, name: string): boolean {
  return current === name
}
