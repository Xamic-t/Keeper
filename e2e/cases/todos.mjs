// 待办：新建 → 出现在列表 → 删除 → 消失。
// 用带时间戳的标题，并且自己删干净，不给下次跑留垃圾。

import { expect } from '../lib/harness.mjs'

export const meta = { name: 'todos', smoke: true, describe: '新建、勾选、删除待办' }

export async function run({ page, config, shot }) {
  await page.goto(`${config.baseURL}/todos`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('form input', { timeout: 15_000 })

  // 种子数据在不在（这三条是日历用例的依赖，坏了要能看出来是哪边坏的）
  // 必须等列表真的渲染完：form 一出现就断言，会撞在"加载中…"上
  await page.waitForSelector('text=回访测试甲', { timeout: 15_000 })
  await shot('list')

  const title = `e2e 临时待办 ${Date.now()}`
  await page.fill('form input', title)
  await page.click('button:has-text("添加")')
  await page.waitForSelector(`text=${title}`, { timeout: 10_000 })
  await shot('created')

  // 删除自己刚建的那条
  const row = page.locator('div.flex.items-center', { hasText: title }).first()
  await row.locator('button:has-text("删除")').click()
  await page.waitForTimeout(1200)
  expect(await page.locator(`text=${title}`).count(), '删除后不该还在列表里').toBe(0)
}
