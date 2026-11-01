// 验收跑批器。
//
//   node run.mjs --list     列出用例
//   node run.mjs --smoke    只跑冒烟（改动大的夜里用这个）
//   node run.mjs            全跑
//   node run.mjs --only calendar,todos
//
// 结果写 last-summary.json，并且用退出码表达成败 —— 这样才能挂进 CI 或
// 早上起来先看一条命令的输出。

import { mkdir, readdir, writeFile } from 'node:fs/promises'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'

import { config } from './config.mjs'
import { collectErrors, login, shot } from './lib/harness.mjs'

const CASES_DIR = fileURLToPath(new URL('./cases/', import.meta.url))

async function loadCases() {
  const files = (await readdir(CASES_DIR)).filter((f) => f.endsWith('.mjs')).sort()
  const cases = []
  for (const file of files) {
    const mod = await import(new URL(file, `file://${CASES_DIR}`).href)
    if (!mod.meta || typeof mod.run !== 'function') {
      throw new Error(`用例 ${file} 缺 meta 或 run 导出`)
    }
    cases.push({ ...mod.meta, run: mod.run, file })
  }
  return cases
}

function parseArgs(argv) {
  const args = { smoke: false, list: false, only: null }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--list') args.list = true
    else if (arg === '--smoke') args.smoke = true
    else if (arg === '--only') args.only = argv[++i].split(',').map((s) => s.trim())
  }
  return args
}

async function runCase(browser, testCase) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors = collectErrors(page)
  const started = Date.now()
  const artifacts = []

  try {
    await login(page)
    await testCase.run({ page, config, shot: (name) => shot(page, `${testCase.name}-${name}`) })
    // 空指针、未捕获的 promise 这类错误会以 console error 形式出现，不能放过
    if (errors.length) {
      throw new Error(`页面有 ${errors.length} 条报错：\n${errors.slice(0, 5).join('\n')}`)
    }
    return { name: testCase.name, ok: true, ms: Date.now() - started, artifacts }
  } catch (err) {
    try {
      artifacts.push(await shot(page, `${testCase.name}-FAILED`))
    } catch {
      // 页面都打不开时截图也会失败，忽略：错误信息本身更有用
    }
    return {
      name: testCase.name,
      ok: false,
      ms: Date.now() - started,
      error: err instanceof Error ? `${err.name}: ${err.message}` : String(err),
      artifacts,
    }
  } finally {
    await page.close()
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const all = await loadCases()

  if (args.list) {
    for (const c of all) {
      console.log(`${c.smoke ? '[smoke]' : '[full] '} ${c.name.padEnd(12)} ${c.describe ?? ''}`)
    }
    return 0
  }

  let selected = all
  if (args.only) selected = selected.filter((c) => args.only.includes(c.name))
  if (args.smoke) selected = selected.filter((c) => c.smoke)

  if (!selected.length) {
    console.error('没有匹配的用例')
    return 1
  }

  console.log(`目标：${config.baseURL}｜账号：${config.username}｜今天：${config.today}`)
  const browser = await chromium.launch({ headless: config.headless })
  const results = []
  try {
    for (const testCase of selected) {
      process.stdout.write(`→ ${testCase.name} ... `)
      const result = await runCase(browser, testCase)
      results.push(result)
      console.log(result.ok ? `OK (${result.ms}ms)` : `FAIL (${result.ms}ms)\n  ${result.error}`)
    }
  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.ok)
  const summary = {
    at: new Date().toISOString(),
    baseURL: config.baseURL,
    username: config.username,
    total: results.length,
    passed: results.length - failed.length,
    failed: failed.length,
    results,
  }
  await mkdir(dirname(config.summaryPath), { recursive: true })
  await writeFile(config.summaryPath, `${JSON.stringify(summary, null, 2)}\n`, 'utf8')

  console.log(`\n${summary.passed}/${summary.total} 通过，详情见 e2e/last-summary.json`)
  return failed.length ? 1 : 0
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error('跑批器自身崩了：', err)
    process.exit(2)
  },
)
