# 16 · DeepSeek Harness 接入方案（真实调研 + 路线图）

> **调研时间**：2026-10-29 · **调研方式**：clone 官方仓库 `deepseek-ai/deepseek-harness` 读源码与官方教程（非二手资料）
> **结论**：能接，且"能编辑功能、能装插件"正是它的长项。但它处于 developer preview，只能当**侧车**接。
> 配套架构决策见 [`ARCHITECTURE.md`](./ARCHITECTURE.md) 19.7 节。

---

## 一、它到底是什么（纠正之前的模糊认识）

`dsh` 是一个开源 agent harness（智能体框架），MIT 协议，基于 Cordis，**"一切皆插件"**。

| 事实 | 之前文档写 | 实际（调研后） |
|---|---|---|
| 运行形态 | 只提了"Node 侧车" | `npx @deepseek-ai/dsh web` 起 Web UI（默认 `127.0.0.1:3080`）；也可源码 `pnpm dsh web`；还有 CLI、Desktop |
| 语言 | 只提 Node.js | Node 为主，**另带 Python SDK**（`deepseek-harness-sdk`，stdio JSON-RPC 驱动运行时） |
| 插件 | "插件系统" | 插件 = 一个 TS 文件，导出 `name` / `Config` / `apply(ctx)`；装成**组合包**用 `dsh plugin add` 装进 profile |
| 工具 | 没写细节 | `ctx.tools.register(defineTool({ name, description, parameters, execute }))`，`execute` 里写任意代码 |
| 模型 | "40+ 提供方" | 内置官方 DeepSeek 适配器（`packages/llm/llm-deepseek`），key 配环境变量即可 |
| 稳定性 | 未提 | ⚠️ **developer preview，官方明确"未来有破坏性兼容变更"** |

---

## 二、"给 key 到底发生什么"（回应你之前的担心）

接 dsh 后，"给 key"就是**配一个环境变量**：

```yaml
# dsh 的配置里，模型 key 这样注入
config:
  apiKey: !!js process.env.DEEPSEEK_API_KEY
```

- dsh 内置了 DeepSeek 官方适配器（用 Messages API），key 只在**你的机器和 DeepSeek 之间**传。
- **这个 key 就是我们现在抽取已经在用的 `DEEPSEEK_API_KEY`**（`vision.py` 里同一个），不会多出一个新的花钱口子。
- dsh 本体是本地跑的（npm 包或源码），不经过任何第三方中转。

---

## 三、怎么接：三样东西

### 1. 写一个"客户查询"工具插件（核心动作）

dsh 里"工具"就是一段 TS 代码，`execute` 里可以 `fetch` 我们 FastAPI 的接口：

```ts
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'keeper-tools'
export const inject = ['tools']

export function apply(ctx: Context) {
  ctx.tools.register(defineTool({
    name: 'today_tasks',
    description: '查今天该联系谁、该做什么',
    parameters: {},
    output: { schema: { type: 'object' }, render: (_a, v) => [{ type: 'text', text: JSON.stringify(v) }] },
    async execute() {
      const res = await fetch('http://app:8000/api/today')   // 容器内主机名
      return await res.json()
    },
  }))
}
```

装插件：打包成组合包（npm 包 + `dsh.bundle` + `cordis.patch.yml`），`dsh plugin add ./keeper-tools` 装进 profile。

### 2. 主服务给 5 个只读接口（19.7.8 已列）

`persons` / `persons/{id}` / `persons/{id}/facts` / `ledger` 汇总 / `today`。工具插件只调这些，**只读、不写**。

### 3. docker-compose 加一个 dsh 侧车容器

独立容器，工具里 `fetch('http://app:8000/...')`。主服务不知道 dsh 存在，反之亦然。

---

## 四、接入路线图（4 阶段，每阶段有判据）

| 阶段 | 做什么 | 完成判据 | 大概量 |
|---|---|---|---|
| **A · 跑起来** | `npx @deepseek-ai/dsh web`，配 `DEEPSEEK_API_KEY` | Web UI 能开，能对话，它用上了 DeepSeek 模型 | 半天（纯环境） |
| **B · 第一个工具** | 写 `today_tasks` 工具插件，`fetch` 调 `GET /api/today` | 你问"我今天该联系谁"，它调接口、把结果组织成回答 | 1–2 天 |
| **C · 接主服务** | 抽 5 个只读接口 + dsh 侧车容器 + 装进 profile | 一条命令起全栈；dsh 挂了不影响投喂/提醒 | 1–2 天 |
| **D · 打磨 + 灰度** | 前端加"助理"入口，先自己用一周 | 你天天问它、它查了再回 | 按反馈 |

---

## 五、三条不能破的红线（照抄 19.7.4 / 19.7.5）

1. **业务真相只在 Postgres** —— dsh 绝不能另存一份人物档案。
2. **dsh 只能通过只读 API 访问数据** —— 直连数据库会绕过 RLS。
3. **侧车不可用不影响主流程** —— 助理挂了，投喂/提醒照跑。

**记忆三分**：会话记忆/工作记忆可以放 dsh；**业务事实（王姐喜欢白茶）严禁存进 dsh 的 memory，必须每次去 Postgres 读**——否则会出现"用户改了档案、助理还答旧答案"的打架。

---

## 六、风险提示（如实说）

- **破坏性变更**：dsh 是 preview，插件 API 可能几个月后变。所以每个阶段都以"不侵入主服务、可随时拆"为前提；坏了就删侧车容器，主流程毫发无损。
- **助理的价值要等数据**：档案没攒厚之前，它"查了再回"也查不出东西。所以接 dsh 和"继续投喂攒数据"要**并行**——不是接了 dsh 就万事大吉，V1 的投喂/抽取主线才是它的命根子。

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-29 | v1.0 | 首版：clone 官方仓库调研，记录真实形态、给 key 机制、工具/插件写法、4 阶段路线图 |
