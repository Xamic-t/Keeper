// 用例共用的动作：登录、断言、截图。
// 只放"每个用例都要做"的事，具体业务流程留在 cases/ 里。

import { mkdir } from 'node:fs/promises'

import { config } from '../config.mjs'

/** 断言失败时抛这个，runner 会把它记成"用例失败"而不是"脚本崩了"。 */
export class AssertionError extends Error {
  constructor(message) {
    super(message)
    this.name = 'AssertionError'
  }
}

export function expect(actual, message) {
  return {
    toBe(expected) {
      if (actual !== expected) {
        throw new AssertionError(`${message}：期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`)
      }
    },
    toContain(needle) {
      const ok = typeof actual === 'string' ? actual.includes(needle) : Array.isArray(actual) && actual.includes(needle)
      if (!ok) {
        throw new AssertionError(`${message}：期望包含 ${JSON.stringify(needle)}，实际 ${JSON.stringify(actual)?.slice(0, 300)}`)
      }
    },
    toBeTruthy() {
      if (!actual) throw new AssertionError(`${message}：期望为真，实际 ${JSON.stringify(actual)}`)
    },
    toBeGreaterThan(min) {
      if (!(actual > min)) {
        throw new AssertionError(`${message}：期望大于 ${min}，实际 ${actual}`)
      }
    },
  }
}

/** 走登录页拿会话。先看是不是已经登录过，避免重复提交。 */
export async function login(page) {
  await page.goto(`${config.baseURL}/login`, { waitUntil: 'domcontentloaded' })
  await page.fill('#username', config.username)
  await page.fill('#password', config.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 })
}

/** 截一张图存到 artifacts/。失败时 runner 也会调它，所以要能容忍目录不存在。 */
export async function shot(page, name) {
  await mkdir(config.artifactsDir, { recursive: true })
  const path = `${config.artifactsDir}${name}.png`
  await page.screenshot({ path, fullPage: true })
  return path
}

/** 收集控制台报错。页面报错是最容易被忽略的"红"，必须当断言使。 */
export function collectErrors(page) {
  const errors = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(`console: ${msg.text()}`)
  })
  page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`))
  return errors
}
