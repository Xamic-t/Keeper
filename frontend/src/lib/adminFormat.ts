/**
 * 管理后台的展示格式化。
 *
 * 抽出来的原因：后台是"一眼看数据"的页面，时间/数量写得含糊就等于没写。
 * 这些规则放模板里没法测，放这里 Vitest 直接钉。
 */

/** 数字：1000 → 1,000；空值 → 0。后台不做缩写，管理员要的是准确值。 */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '0'
  return value.toLocaleString('zh-CN')
}

/**
 * 相对时间：刚刚 / 12 分钟前 / 3 小时前 / 2 天前 / 2026-10-01。
 *
 * 为什么不做"xx 周前"：超过 30 天直接给日期，管理员要看的是"什么时候"，
 * 不是"多久以前"——3 周前不如 10-01 直观。
 */
export function formatRelative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '还没用过'
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return '时间坏了'

  const diffMs = now.getTime() - then.getTime()
  if (diffMs < 0) return '刚刚'
  const minutes = Math.floor(diffMs / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days <= 30) return `${days} 天前`

  const month = String(then.getMonth() + 1).padStart(2, '0')
  const day = String(then.getDate()).padStart(2, '0')
  return `${then.getFullYear()}-${month}-${day}`
}

/** 角色/状态的展示文案。库里存英文，界面说人话。 */
export function roleLabel(role: string): string {
  return role === 'owner' ? '管理员' : '成员'
}

export function statusLabel(isActive: boolean): string {
  return isActive ? '正常' : '已停用'
}

/**
 * 行为量的"有没有用过"判断：全 0 说明这个账号还没开始用，值得单独标出来。
 * 后台最怕的就是"看起来 8 个账号，其实 7 个是空的"。
 */
export function isEmptyAccount(counts: Record<string, number>): boolean {
  return Object.values(counts).every((value) => !value)
}
