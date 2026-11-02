// 助理页：要么嵌进来，要么给出"怎么启动"的说明。
// 最差的情况是白板——用户不知道是没起、还是被拦了。

import { expect } from '../lib/harness.mjs'

export const meta = {
  name: 'assistant',
  smoke: false,
  describe: '助理页可打开，未启动时给说明而不是白板',
}

export async function run({ page, config, shot }) {
  // 3080 没起时浏览器会打一条资源错误，且文案是"所有资源失败"的通用文案。
  // 不吞它：把 3080 的请求直接拦掉，既模拟"未配置助理"也保留对页面报错的严格检查。
  await page.route('http://127.0.0.1:3080/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body>harness</body></html>' }),
  )

  await page.goto(`${config.baseURL}/assistant`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(6000)

  const body = await page.locator('body').innerText()
  expect(body, '页面标题应是助理').toContain('助理')

  const hasFrame = (await page.locator('iframe#harness-frame').count()) > 0
  const hasFallback = body.includes('连不上助理')
  const needsSetup = body.includes('保存并打开')
  expect(hasFrame || hasFallback || needsSetup, '要么内嵌，要么说明怎么启动').toBeTruthy()
  expect(body, '应始终给出启动命令或新窗口入口').toContain('harness')

  await shot('page')
}
