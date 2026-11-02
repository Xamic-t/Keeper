// 离线演示：先把 /api 全断掉，再进 demo。任何真实 API 调用都会让用例失败。

import { expect } from '../lib/harness.mjs'

export const meta = {
  name: 'demo',
  smoke: false,
  describe: '断网进入离线演示，能看今日页和档案',
}

export async function run({ page, config, shot }) {
  await page.route(`${config.baseURL}/api/**`, (route) => route.abort())

  await page.goto(`${config.baseURL}/login?demo=1`, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: /离线演示/ }).click()
  await page.waitForURL(
    (url) => url.pathname === '/' && url.searchParams.get('demo') === '1',
    { timeout: 10_000 },
  )
  await page.waitForTimeout(500)

  const today = await page.locator('body').innerText()
  expect(today, '今日页应有演示任务').toContain('王姐生日')
  await shot('today')

  await page.goto(`${config.baseURL}/persons`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('a.card', { timeout: 10_000 })
  await page.locator('a.card', { hasText: '王姐' }).first().click()
  await page.waitForURL(/\/persons\/1/, { timeout: 10_000 })
  await page.waitForTimeout(500)

  const detail = await page.locator('body').innerText()
  expect(detail, '档案页应显示预录事实').toContain('白茶')
  await shot('detail')
}
