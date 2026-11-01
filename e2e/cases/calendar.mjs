// 日历：网格有没有条目、拖拽改期是不是真的落库。
// 拖拽最容易假成功 —— 界面上动了，刷新就弹回来。所以必须 reload 再确认一次。

import { day } from '../config.mjs'
import { expect } from '../lib/harness.mjs'

export const meta = { name: 'calendar', smoke: true, describe: '月网格渲染 + 拖拽改期落库' }

const MOVING = '约测试丙吃饭'

export async function run({ page, config, shot }) {
  await page.goto(`${config.baseURL}/calendar`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.fc-daygrid', { timeout: 15_000 })
  await page.waitForTimeout(1200)

  // 1. 三种日期状态各在正确的格子里（种子数据：逾期 2 天 / 今天 / 3 天后）
  const at = async (title) =>
    page
      .locator('.fc-event', { hasText: title })
      .first()
      .evaluate((el) => el.closest('td[data-date]')?.getAttribute('data-date'))

  expect(await at('回访测试甲'), '逾期待办应落在 2 天前').toBe(day(-2))
  expect(await at('给测试乙寄样品'), '今天的待办应落在今天').toBe(day(0))
  expect(await at(MOVING), '未来的待办应落在 3 天后').toBe(day(3))
  // 事件类条目也该在（实心胶囊）
  expect(await page.locator('.fc-event', { hasText: '测试结款日' }).count(), '自定义事件应出现在日历上')
    .toBeGreaterThan(0)

  await shot('grid')

  // 2. 拖到后一天
  const target = day(4)
  const pill = page.locator('.fc-event', { hasText: MOVING }).first()
  const cell = page.locator(`td[data-date="${target}"]`).first()

  let patched = false
  page.on('request', (req) => {
    if (req.method() === 'PATCH' && req.url().includes('/api/todos/')) patched = true
  })

  const from = await pill.boundingBox()
  const to = await cell.boundingBox()
  expect(Boolean(from && to), '应能拿到拖拽坐标').toBeTruthy()

  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2)
  await page.mouse.down()
  await page.mouse.move(from.x + from.width / 2 + 10, from.y + from.height / 2 + 10, { steps: 6 })
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 24 })
  await page.mouse.up()
  await page.waitForTimeout(1500)

  expect(patched, '拖拽应发出 PATCH /api/todos/{id}').toBeTruthy()
  await shot('after-drop')

  // 3. 刷新后还在新位置 —— 这一步才是"真落库"
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.fc-daygrid', { timeout: 15_000 })
  await page.waitForTimeout(1500)
  expect(await at(MOVING), '刷新后应停在新日期（否则是假成功）').toBe(target)
  await shot('after-reload')

  // 4. 把种子数据拖回去，避免第二次跑时目标格已被占
  const back = page.locator('.fc-event', { hasText: MOVING }).first()
  const home = page.locator(`td[data-date="${day(3)}"]`).first()
  const from2 = await back.boundingBox()
  const to2 = await home.boundingBox()
  await page.mouse.move(from2.x + from2.width / 2, from2.y + from2.height / 2)
  await page.mouse.down()
  await page.mouse.move(to2.x + to2.width / 2, to2.y + to2.height / 2, { steps: 20 })
  await page.mouse.up()
  await page.waitForTimeout(1200)
}
