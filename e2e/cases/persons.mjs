// 人物：列表 → 点进详情 → 三段内容（事实/往来/时间线）都在。
// 这里只验"能打开且不空"，编辑能力由 persons 的另一条用例覆盖（B6 加）。

import { expect } from '../lib/harness.mjs'

export const meta = { name: 'persons', smoke: true, describe: '人物列表与详情页可打开' }

export async function run({ page, config, shot }) {
  await page.goto(`${config.baseURL}/persons`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('a.card', { timeout: 15_000 })

  const count = await page.locator('a.card').count()
  expect(count, '列表里至少应有一个种子人物').toBeGreaterThan(0)
  await shot('list')

  // 标签筛选：种子数据只有「测试甲」带「偏好 · 白茶」
  await page.selectOption('label:has-text("按标签筛") select', { label: '偏好 · 白茶' })
  await page.waitForTimeout(500)
  expect(await page.locator('a.card').count(), '筛标签后应只剩测试甲').toBe(1)
  expect(await page.locator('a.card', { hasText: '测试甲' }).count(), '筛出的应是测试甲').toBe(1)
  await shot('tag-filter')
  await page.selectOption('label:has-text("按标签筛") select', '')
  await page.waitForTimeout(500)
  expect(await page.locator('a.card').count(), '取消筛选后应恢复全部').toBe(count)

  await page.locator('a.card', { hasText: '测试甲' }).first().click()
  await page.waitForURL(/\/persons\/\d+/, { timeout: 10_000 })
  await page.waitForTimeout(1000)

  const body = await page.locator('body').innerText()
  expect(body, '详情页应显示昵称').toContain('测试甲')
  // 阶段：B6 之前页面直接把枚举打出来（core（manual）），之后应是中文标签
  // 这里两种都接受，改成只认中文放在 B6 之后收紧
  const hasStage = body.includes('核心') || body.includes('core')
  expect(hasStage, '详情页应有关系阶段').toBeTruthy()
  await shot('detail')
}
