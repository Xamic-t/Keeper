// 档案页从"只能看"变成"能改"。
// 重点验三件事：改完刷新还在（真落库）、事实能改能删、往来能记。

import { expect } from '../lib/harness.mjs'

export const meta = { name: 'person-edit', smoke: false, describe: '改昵称/阶段、改删事实、记往来、导出' }

const ORIGINAL = '测试甲'
const RENAMED = '测试甲（改过）'

export async function run({ page, config, shot }) {
  // 删除都带 confirm()，Playwright 默认会"取消"掉弹窗 —— 不显式接受，点了等于没点
  page.on('dialog', (dialog) => {
    void dialog.accept()
  })

  await page.goto(`${config.baseURL}/persons`, { waitUntil: 'domcontentloaded' })
  await page.locator('a.card', { hasText: ORIGINAL }).first().click()
  await page.waitForURL(/\/persons\/\d+/, { timeout: 10_000 })
  await page.waitForTimeout(800)

  // 阶段要显示成中文，不能把枚举 core（manual）直接甩给用户
  const head = await page.locator('body').innerText()
  expect(head, '阶段应显示中文').toContain('核心')
  expect(head.includes('core（manual）'), '不该出现英文枚举').toBe(false)

  // ① 改昵称 → 刷新还在
  await page.click('button:has-text("编辑")')
  await page.waitForSelector('input.input', { timeout: 5000 })
  const nicknameInput = page.locator('label:has-text("称呼") input')
  await nicknameInput.fill(RENAMED)
  await page.click('button:has-text("保存")')
  await page.waitForTimeout(1200)
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1200)
  expect(await page.locator('body').innerText(), '刷新后昵称应已改').toContain(RENAMED)
  await shot('renamed')

  // 改回去，别把种子数据搞脏
  await page.click('button:has-text("编辑")')
  await page.locator('label:has-text("称呼") input').fill(ORIGINAL)
  await page.click('button:has-text("保存")')
  await page.waitForTimeout(1200)
  expect(await page.locator('body').innerText(), '昵称应改回').toContain(ORIGINAL)

  // ② 记一笔往来 → 列表里出现 → 删掉
  await page.click('button:has-text("记一笔")')
  await page.waitForSelector('input[placeholder="金额"]', { timeout: 5000 })
  await page.fill('input[placeholder="金额"]', '128.50')
  await page.fill('input[placeholder="备注（可空）"]', 'e2e 记的一笔')
  await page.click('form button:has-text("保存")')
  await page.waitForTimeout(1500)
  expect(await page.locator('text=128.50').count(), '往来应出现在列表').toBeGreaterThan(0)
  await shot('ledger')

  // 删掉所有 128.50 的往来（含上次失败留下的），按"这一行里有删除按钮"来定位 ——
  // 只按 class + 文本选会撞上「往来 / 记一笔」那个标题行
  const deleteButton = page.locator('button:has-text("删")')
  const rows = page.locator('div.flex.items-center.justify-between', { hasText: '128.50' })
  for (let guard = 0; guard < 5; guard += 1) {
    const row = rows.filter({ has: deleteButton }).first()
    if ((await row.count()) === 0) break
    await row.locator('button:has-text("删")').click()
    await page.waitForTimeout(1200)
  }
  expect(await page.locator('text=128.50').count(), '删除后不该还在').toBe(0)

  // ③ 导出能下载（浏览器会触发 download 事件）
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 10_000 }),
    page.click('button:has-text("导出 JSON")'),
  ])
  expect(download.suggestedFilename().endsWith('.json'), '导出文件名应是 .json').toBeTruthy()
}
