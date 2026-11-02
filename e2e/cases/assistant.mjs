// 助理页：主界面一级菜单里的原生对话（2026-11-03 起不再嵌 Harness）。
//
// 这个用例只验"能进来、能打字、不该有的东西没了"——
// **不真的发消息**：发一条就要调模型，既慢又花钱，而且模型输出不稳定，
// 会让 e2e 变成随机失败的来源。发消息 + 动作落库由 pytest
// （tests/api/test_conversation.py）用假模型覆盖。

import { expect } from '../lib/harness.mjs'

export const meta = {
  name: 'assistant',
  smoke: true,
  describe: '助理是主界面一级菜单，页面有可用输入框；不再依赖 Harness 侧车',
}

export async function run({ page, config, shot }) {
  // 先确认导航里有「助理」——它以前只在桌面侧栏、手机上要从设置绕进去
  await page.goto(`${config.baseURL}/today`, { waitUntil: 'domcontentloaded' })
  const navText = await page.locator('body').innerText()
  expect(navText, '导航里应该有助理').toContain('助理')

  await page.goto(`${config.baseURL}/assistant`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(800)

  const body = await page.locator('body').innerText()
  expect(body, '应该是助理页').toContain('助理')

  // 不能有任何 Harness 的残留：这是这次改版最重要的一条
  expect(body.toLowerCase().includes('harness'), '不该再出现 harness 字样').toBe(false)
  expect(body.includes('保存并打开'), '不该再让用户粘 token').toBe(false)
  expect(await page.locator('iframe#harness-frame').count(), '不该再有 iframe').toBe(0)

  // 输入框必须可用
  const box = page.locator('textarea')
  expect(await box.count(), '要有输入框').toBeGreaterThan(0)
  await box.fill('11 月 4 号王姐生日，帮我建个待办')
  expect(await box.inputValue(), '能把字打进去').toContain('11 月 4 号')

  // 发送按钮存在且可用
  const sendBtn = page.getByRole('button', { name: '发送' })
  expect(await sendBtn.count(), '要有发送按钮').toBeGreaterThan(0)
  expect(await sendBtn.isEnabled(), '有文字时发送应可点').toBe(true)

  await shot('page')
}
