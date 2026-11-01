/**
 * Keeper 工具插件：把主服务的 5 个只读接口暴露给 Harness 的模型。
 *
 * 设计依据（`docs/ARCHITECTURE.md` 19.7）：
 *  · 只读。写操作要先想清楚"要不要进确认队列"，在那之前一条都不开
 *  · 不缓存。每次调用都真的去问主服务 —— Postgres 是唯一业务真相源，
 *    模型不许把"王姐喜欢普洱"记进自己的记忆里（19.7.5）
 *  · 侧车挂了不影响主流程。这里抛错只是这一次回答失败
 *
 * 配置走环境变量（`harness/.env`，不进 git）：
 *  · KEEPER_API              主服务地址，默认 http://127.0.0.1:8000
 *  · KEEPER_ASSISTANT_TOKEN  只读接口的 service token
 */

import { defineTool } from '@deepseek-ai/dsh-tools'

const API = (process.env.KEEPER_API || 'http://127.0.0.1:8000').replace(/\/$/, '')
const TOKEN = process.env.KEEPER_ASSISTANT_TOKEN || ''

export const name = 'keeper-tools'
export const inject = ['tools']

/**
 * 调一次只读接口。
 * 主服务统一返回 {data, meta?}，这里把 data 剥出来给模型 —— 外层信封对它没意义。
 */
async function get(path, query) {
  if (!TOKEN) {
    throw new Error('KEEPER_ASSISTANT_TOKEN 没配，助理接口调不通')
  }
  const url = new URL(API + path)
  Object.entries(query || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, String(value))
    }
  })

  const response = await fetch(url, {
    headers: { 'X-Assistant-Token': TOKEN, Accept: 'application/json' },
  })

  if (!response.ok) {
    // 把状态码和原因交给模型，让它能如实说"查不到"，而不是编一个
    const text = await response.text()
    throw new Error(`Keeper API ${response.status} ${path}: ${text.slice(0, 200)}`)
  }

  const payload = await response.json()
  return payload.data
}

/** 统一的输出投影：原样 JSON 给模型看。 */
function jsonOutput(description) {
  return {
    schema: { type: 'json', description },
    render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
  }
}

export function apply(ctx) {
  ctx.tools.register(
    defineTool({
      name: 'keeper_today',
      description:
        '查今天该做什么：今天要联系谁、哪条待办到期、有什么提醒。用户问"我今天该干什么""今天该联系谁"时先调它。',
      parameters: {},
      output: jsonOutput('今日任务列表'),
      async execute() {
        return await get('/api/assistant/today')
      },
    }),
  )

  ctx.tools.register(
    defineTool({
      name: 'keeper_find_person',
      description:
        '按昵称或别名找人，返回 id、阶段、标签。用户只说了个名字、但你需要 person_id 时先调它。不传关键词就是最近更新的人。',
      parameters: {
        keyword: { type: 'string', description: '昵称或别名的一部分，比如"王姐"' },
        limit: { type: 'integer', description: '最多返回几个，默认 20' },
      },
      output: jsonOutput('匹配到的人'),
      async execute(args) {
        return await get('/api/assistant/persons', {
          keyword: args.keyword,
          limit: args.limit,
        })
      },
    }),
  )

  ctx.tools.register(
    defineTool({
      name: 'keeper_person',
      description:
        '看一个人的档案：阶段、关系角色、生日、禁忌、标签、往来收支合计、各类条目数量。回答"这个人什么情况"之前先调它。',
      parameters: {
        person_id: {
          type: 'integer',
          description: '人的 id，从 keeper_find_person 拿',
          required: true,
        },
      },
      output: jsonOutput('档案'),
      async execute(args) {
        return await get(`/api/assistant/persons/${args.person_id}`)
      },
    }),
  )

  ctx.tools.register(
    defineTool({
      name: 'keeper_facts',
      description:
        '看一个人所有已确认的事实（喜好、忌讳、家庭、处境），带权重和出处。回答"她喜欢什么""有什么要注意的"必须调它，不许凭记忆答。',
      parameters: {
        person_id: { type: 'integer', description: '人的 id', required: true },
      },
      output: jsonOutput('已确认的事实'),
      async execute(args) {
        return await get(`/api/assistant/persons/${args.person_id}/facts`)
      },
    }),
  )

  ctx.tools.register(
    defineTool({
      name: 'keeper_ledger',
      description:
        '看跟一个人的往来账：收了多少、给了多少、还有几笔没结清、最近几笔明细。回答"我跟他还有账吗""上次给过什么"时用。',
      parameters: {
        person_id: { type: 'integer', description: '人的 id', required: true },
        limit: { type: 'integer', description: '返回最近几笔，默认 10' },
      },
      output: jsonOutput('往来汇总'),
      async execute(args) {
        return await get('/api/assistant/ledger', {
          person_id: args.person_id,
          limit: args.limit,
        })
      },
    }),
  )
}
