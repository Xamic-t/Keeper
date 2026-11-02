#!/usr/bin/env node
// 从后端代码直接生成 OpenAPI 类型，不需要先手动起 8000。
// 这样 gen:types 可以进 CI / 提交前检查，而不是只在本地后端刚好运行时可用。

import { spawnSync } from 'node:child_process'
import { rmSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const backendRoot = resolve(frontendRoot, '../backend')
const tmpOpenapi = resolve(frontendRoot, '.openapi.tmp.json')
const output = resolve(frontendRoot, 'src/api/schema.d.ts')

const dump = spawnSync(
  'uv',
  [
    'run',
    '--directory',
    backendRoot,
    'python',
    '-c',
    'import json; from app.main import app; print(json.dumps(app.openapi(), ensure_ascii=False))',
  ],
  { encoding: 'utf8' },
)

if (dump.status !== 0) {
  console.error(dump.stderr || dump.stdout || '导出 OpenAPI 失败')
  process.exit(dump.status ?? 1)
}

writeFileSync(tmpOpenapi, dump.stdout, 'utf8')
const generate = spawnSync('npx', ['openapi-typescript', tmpOpenapi, '-o', output], {
  stdio: 'inherit',
})
rmSync(tmpOpenapi, { force: true })

if (generate.status !== 0) {
  process.exit(generate.status ?? 1)
}

console.log(`已生成 ${output}`)
