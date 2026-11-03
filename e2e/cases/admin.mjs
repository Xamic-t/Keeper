// 管理后台：只有 owner 能进，而且它**不在业务菜单里**（2026-11-03 改版的重点）。
//
// 这里用 member 账号跑，验的是"进不去、也看不见入口"——
// 后台本身长什么样由 pytest（tests/api/test_admin_overview.py）和人工截图覆盖。

import { expect } from '../lib/harness.mjs'

export const meta = {
  name: 'admin',
  smoke: true,
  describe: '普通成员看不到管理后台入口，直接访问会被送回业务首页',
}

export async function run({ page, config, shot }) {
  // 业务页的侧栏/底部区不该出现"管理后台"
  await page.goto(`${config.baseURL}/today`, { waitUntil: 'domcontentloaded' })
  const body = await page.locator('body').innerText()
  expect(body.includes('管理后台'), 'member 不该看到管理后台入口').toBe(false)
  expect(body.includes('账号管理'), '业务菜单里不该再有账号管理').toBe(false)

  // 直接敲地址也进不去
  await page.goto(`${config.baseURL}/admin`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(600)
  const url = new URL(page.url())
  expect(url.pathname, 'member 访问 /admin 应被送回首页').toBe('/')
  const after = await page.locator('body').innerText()
  expect(after.includes('今日'), '回到的是业务首页').toBe(true)

  await shot('page')
}
