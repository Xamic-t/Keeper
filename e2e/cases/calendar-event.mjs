// 日历上自己建的事件：能建、能删。
// 以前只能看，建错了没有任何办法撤掉。

import { expect } from '../lib/harness.mjs'

export const meta = { name: 'calendar-event', smoke: false, describe: '新建事件并删除' }

const TITLE = `e2e 事件 ${Date.now() % 100000}`

export async function run({ page, config, shot }) {
  page.on('dialog', (dialog) => {
    void dialog.accept()
  })

  await page.goto(`${config.baseURL}/calendar`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.fc-daygrid', { timeout: 15_000 })
  await page.waitForTimeout(1200)

  // ① 新建。日期默认是今天，直接保存
  await page.click('button:has-text("新建")')
  await page.waitForSelector('input[placeholder*="给王姐送茶"]', { timeout: 5000 })
  await page.fill('input[placeholder*="给王姐送茶"]', TITLE)
  await page.click('button:has-text("保存")')
  await page.waitForTimeout(2000)

  const pill = page.locator('.fc-event', { hasText: TITLE }).first()
  expect(await pill.count(), '新建的事件应出现在网格上').toBeGreaterThan(0)
  await shot('created')

  // ② 点它 → 删除
  await pill.click()
  await page.waitForSelector('button:has-text("删除")', { timeout: 5000 })
  await shot('sheet')
  await page.click('button:has-text("删除")')
  await page.waitForTimeout(2000)

  expect(await page.locator('.fc-event', { hasText: TITLE }).count(), '删除后不该还在').toBe(0)
}
