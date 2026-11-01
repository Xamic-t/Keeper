# Harness 侧车（Keeper 的"助理"）

> 这个目录是 **Node 侧车**，跟 Python 主服务是两套进程、两套依赖。
> 它挂了、删了、升级坏了，Keeper 的投喂 / 抽取 / 提醒 / 待办**一根汗毛都不受影响**。
>
> 设计与红线：`docs/ARCHITECTURE.md` 19.7 · 接入记录：`docs/22-Harness接入记录.md`

---

## 它做什么

Harness（`dsh`，DeepSeek 开源的 agent 运行时）跑一个对话式助理，模型需要数据时**自己决定**
调哪个工具，工具就是 `keeper-tools/index.js` 里注册的 5 个只读函数 —— 它们 `fetch` 主服务的
`/api/assistant/*`，拿回真实数据再组织成回答。

```
你问：王姐喜欢什么？
  → 模型：调 keeper_find_person{keyword:"王姐"}   → 拿到 person_id
  → 模型：调 keeper_facts{person_id}              → 拿到已确认的事实 + 依据
  → 模型：用事实组织回答
```

**模型不记业务事实。** 每次回答都真的去查 Postgres。用户在档案页把「喜欢普洱」改成「喜欢白茶」，
助理下一次就会答白茶——这是 19.7.5 的硬要求，破了就会出现"两个都对"的打架。

## 三条红线（别在这儿破）

| # | 红线 | 破了会怎样 |
|---|---|---|
| 1 | 只能调只读 API | 助理能改数据 → 必须回答"写操作要不要进确认队列"，在那之前不开 |
| 2 | 不许把事实存进 assistant 的 memory | 两处存同一件事，必然打架 |
| 3 | 主服务不许依赖侧车 | 地基变单点 |

## 怎么跑

```bash
cd harness
./run.sh            # 第一次会自动 npm install（约 600 个包）
```

打开 http://127.0.0.1:3080 ，问一句「我今天该联系谁」。

前置：主服务在跑（`../dev.sh`），且项目根 `.env` 里有 `ASSISTANT_TOKEN`。
本目录的 `.env` 里有 `KEEPER_ASSISTANT_TOKEN`（与主服务同一个值）和 `KEEPER_API`，
**这个文件不进 git**。

## 三条验收（能跑起来就先看这三条）

| # | 怎么问 | 应该发生什么 |
|---|---|---|
| 1 | 「你今天能查什么」 | 它列出 `keeper_today` / `keeper_find_person` / `keeper_person` / `keeper_facts` / `keeper_ledger` |
| 2 | 「我今天该联系谁」 | 它**调了** `keeper_today`（界面上能看到工具调用），然后按回来的任务回答 |
| 3 | 「孟雅楠喜欢喝什么」 | 先 `keeper_find_person` 再 `keeper_facts`，答案里带依据 |

第 2 条最关键：**没看到工具调用就是插件没加载**，不是模型笨。

## 出问题先看这里

| 现象 | 大概原因 |
|---|---|
| 5 个工具一个都没出现 | 覆盖层没生效：`--patch ./cordis.patch.yml` 的路径不对，或者 dsh 升级后 patch 格式变了。用 `npx dsh --profile web --dump-default-config` 对照 |
| 工具在，但一调就报 `Keeper API 401` | `.env` 里的 token 和主服务 `ASSISTANT_TOKEN` 不一致，或者主服务没重启（改 `.env` 要重启才生效） |
| 报 `Keeper API 503` | 主服务没配 `ASSISTANT_TOKEN`，或者 `ASSISTANT_USERNAME` 指向的账号不存在 |
| 助理答得像是编的 | 看它有没有真的调工具。没调就说明工具描述不够清楚，或者模型没被要求"先查再答" |
| 端口 3080 被占 | `npx dsh web --port 3081 --patch ./cordis.patch.yml` |

## 目录

```
harness/
├── keeper-tools/index.js   # 插件本体：5 个只读工具
├── cordis.patch.yml        # 启动时叠的覆盖层（把插件挂进 web profile）
├── run.sh                  # 装依赖 → 凑环境变量 → 起服务
└── .env                    # token 与主服务地址（不进 git）
```

## 升级注意

dsh 是 **developer preview**，官方明说会有破坏性变更。升级时：

1. 先把 `package.json` 里的版本改掉，`npm install`
2. 跑一次上面三条验收
3. 坏了就 `git checkout harness/cordis.patch.yml` 或直接不带 `--patch` 启动 —— 主服务毫发无损
