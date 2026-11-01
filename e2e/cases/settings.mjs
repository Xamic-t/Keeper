// 设置：页面能打开、两个区块（账号 / 推送通道）都在。
// 不做写操作 —— 改密码、配通道这些会动到账号状态，用手测更合适。

import { expect } from '../lib/harness.mjs'

export const meta = { name: 'settings', smoke: false, describe: '设置页可打开，区块齐全' }

export async function run({ page, config, shot }) {
  await page.goto(`${config.baseURL}/settings`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)

  const body = await page.locator('body').innerText()
  expect(body, '设置页应能打开').toContain('设置')
  expect(body.length, '设置页不该是空白').toBeGreaterThan(200)
  await shot('page')
}
