// 会话过期：cookie 没了要回登录页，不能停在空列表上。
// 这个 bug 的形态很糟：每个页面都"能用"，只是数据全空，用户以为丢了数据。

import { expect } from '../lib/harness.mjs'

export const meta = {
  name: 'auth',
  smoke: true,
  describe: 'cookie 失效后自动回登录页',
  // 这个用例就是要制造 401，浏览器必然会打一条资源加载失败的 console error
  allowConsoleErrors: ['401 \\(Unauthorized\\)'],
}

export async function run({ page, config }) {
  await page.goto(`${config.baseURL}/todos`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('form input', { timeout: 15_000 })

  // 把 cookie 清掉，模拟"开着开着会话过期"
  await page.context().clearCookies()

  // 触发一次真实请求：点开人物页，它会拉 /api/persons
  await page.goto(`${config.baseURL}/persons`, { waitUntil: 'domcontentloaded' })
  await page.waitForURL(/\/login/, { timeout: 10_000 })
  expect(new URL(page.url()).pathname, '应落到登录页').toBe('/login')

  // 不能反复刷新（跳转如果和路由守卫互踢，会看到页面一直在 reload）
  const navigations = []
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) navigations.push(frame.url())
  })
  await page.waitForTimeout(1500)
  expect(navigations.length, '到登录页后不该继续跳转').toBe(0)
}
