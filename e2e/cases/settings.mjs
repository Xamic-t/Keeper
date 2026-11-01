// 设置：三个区块都在，改密码的校验能拦住手误。
// 不真的改密码 —— e2e 账号的密码是固定的，改了下次就跑不了；
// 真正的改密码逻辑由后端的用例覆盖。

import { expect } from '../lib/harness.mjs'

export const meta = { name: 'settings', smoke: false, describe: '设置页区块齐全、改密码校验生效' }

export async function run({ page, config, shot }) {
  await page.goto(`${config.baseURL}/settings`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)

  const body = await page.locator('body').innerText()
  expect(body, '设置页应能打开').toContain('设置')
  expect(body, '应有保留原图的开关').toContain('保留聊天原图')
  expect(body, '应有推送通道区块').toContain('推送通道')
  expect(body, '应有改密码区块').toContain('改密码')
  await shot('page')

  // 两次新密码不一致 → 就地报错，不能静默什么都不做
  await page.fill('input[placeholder="当前密码"]', 'wrong-one')
  await page.fill('input[placeholder="新密码（至少 8 位）"]', 'abcdefgh')
  await page.fill('input[placeholder="再输一次新密码"]', 'abcdefgi')
  await page.click('button:has-text("保存新密码")')
  await page.waitForTimeout(600)
  expect(await page.locator('text=两次输入的新密码不一致').count(), '应提示两次不一致').toBeGreaterThan(0)

  // 太短也要拦
  await page.fill('input[placeholder="新密码（至少 8 位）"]', '短')
  await page.fill('input[placeholder="再输一次新密码"]', '短')
  await page.click('button:has-text("保存新密码")')
  await page.waitForTimeout(600)
  expect(await page.locator('text=新密码至少 8 位').count(), '应提示太短').toBeGreaterThan(0)
}
