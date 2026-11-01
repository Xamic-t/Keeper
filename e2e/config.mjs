// 验收配置。所有可变项都从环境变量来，脚本里不许出现写死的账号和日期。
//
// 为什么日期要相对算：写死 "2026-11-05" 的脚本，过一个月就是红的，
// 而且红得莫名其妙（数据没错，是脚本过期）。这里一律相对"今天"算。

import { fileURLToPath } from 'node:url'

const TZ = 'Asia/Shanghai'

/** 把某个时刻格式化成业务时区的 YYYY-MM-DD。 */
function ymd(date) {
  // en-CA 的日期格式就是 YYYY-MM-DD，省掉自己拼 padStart
  return new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(date)
}

/**
 * 相对今天偏移若干天的日期串。
 * 只做日期加减，不经过本地时区，跨夏令时也不会差一天。
 */
export function day(offset = 0) {
  const [y, m, d] = ymd(new Date()).split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d) + offset * 86_400_000).toISOString().slice(0, 10)
}

export const config = {
  baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173',
  // 只能用 seed_e2e.py 建的隔离账号。换成真实账号会改到用户的数据。
  username: process.env.E2E_USERNAME ?? 'keeper-e2e',
  password: process.env.E2E_PASSWORD ?? 'keeper-e2e-pass',
  headless: process.env.E2E_HEADFUL !== '1',
  artifactsDir: fileURLToPath(new URL('./artifacts/', import.meta.url)),
  summaryPath: fileURLToPath(new URL('./last-summary.json', import.meta.url)),
  today: day(0),
}
