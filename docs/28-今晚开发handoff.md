# 28 · 今晚开发 Handoff（2026-11-03 凌晨起）

> **交给执行 Agent：今晚你自主推进，不要中途问用户。**
> 用户明早只看两样东西：**全部能跑的交付** + **需要他决策/操作的清单**。
>
> 基线：`main` @ `d02f078`（2026-11-02 16:10）
> 基线状态：**267 pytest · 14 Vitest · e2e 9/9 · ruff format/check 绿 · 覆盖率 80% · PWA 已构建**
>
> 工作目录：`/Users/xamic/Customers_System`

---

## 0. 全局铁律（每个任务开工前都要重读一遍）

1. **每个任务开工前，先读 `docs/ARCHITECTURE.md` 对应章节。**
   不读不写。任务卡里写了“先读哪几节”，照做；读完在任务记录里写一句“已读 §x”。
2. **Architecture 是硬约束，不是参考。**
   代码和它冲突时，以 Architecture 为准。**不许改 Architecture 来迁就代码。**
3. **遇到需要产品/架构取舍的点：不要问用户，也不要自己拍板。**
   写入 `docs/29-待决策清单.md`（格式见 §5），把该项标 `BLOCKED-DECISION`，继续做不依赖它的任务。
4. **不要中断用户。** 不提问、不要求确认、不等待回复。能做的先做完。
5. **不要读 `.env`。** 需要变量状态就让脚本打印“已填/未填”，不打印值。
6. **不要动真实用户数据做实验。**
   API 测试用 `tests/conftest.py` 的隔离账号；E2E 用 `keeper-e2e`；数据库恢复演练只能写临时库。
7. **每个任务完成后单独 commit。** 提交信息写清任务号、改动、验证。
8. **每个任务结束前必跑：**
   ```bash
   cd backend
   uv run ruff format .
   uv run ruff check --no-cache .
   uv run pytest tests/test_architecture.py -q
   uv run pytest -q
   ```
   动前端再加：
   ```bash
   cd frontend && npm test && npm run build
   ```
9. **拿不准是否算“决策”**：只要会改变用户可见行为、数据结构、部署拓扑、V1/V2 范围，就算决策，暂存，不擅自做。
10. 今晚不碰 V2（温度、需求感知、RAG、语音、批量归档），也不改 prompt 版本。

---

## 1. 今晚不做的（明早给用户，不在中途处理）

| 项 | 为什么今晚不做 |
|---|---|
| W13-A 30 张截图准确率 | 本机只有 3 张真实截图（`~/testdata/screenshots` 共 4 个文件，含 1 份核对表），凑不够 30 张；需要用户补素材/人工核对 |
| W14-A 真机推送 | 需要用户的企微 webhook 或 PushPlus token |
| W14-B 上线部署 | 需要域名/备案/服务器 |
| V2 功能 | Architecture 标 V2，不在今晚范围 |
| 改 `docs/ARCHITECTURE.md` 的产品口径 | 需要用户决策；只允许补“事实性状态”，不许改要求 |

---

## 2. 任务卡

### N0 · 开工预检

**先读**：`ARCHITECTURE` §6、§8.1、§11.2、§15、§17、§22。

**做**：
1. `git status` 必须干净；不干净先判断是不是上一个 Agent 的未提交改动，记录后继续（不要 `reset --hard`）。
2. 跑基线：`ruff format --check`、`ruff check`、`pytest -q`、`pytest tests/test_architecture.py -q`。
3. 确认 `docs/29-待决策清单.md` 存在；没有就从模板建。
4. 在 `docs/30-夜间开发报告.md` 建骨架：任务、状态、commit、验证、决策、阻塞。

**完成判据**：基线数字与本文一致；报告骨架存在。

---

### N1 · RLS 全表审计（P0，安全）

**先读**：`ARCHITECTURE` §11.2、§15.1–15.5、§17；`docs/06` 表清单。

**为什么**：Architecture 说“RLS 在数据库层强制”，但现在只有部分表有隔离用例；不能靠“我记得开了”。

**做**：
1. 从 `backend/app/models/__init__.py` 取 13 张表，对照 `pg_class.relrowsecurity`、
   `pg_class.relforcerowsecurity`、`pg_policies`。
2. 明确例外：`users` 登录前就要查，**按设计不开 RLS**；`alembic_version` 不是业务表。
   其余业务表必须：`ENABLE` + `FORCE` RLS，且有 `USING` + `WITH CHECK`。
3. 新增 `backend/tests/test_rls_metadata.py`：
   - 业务表逐一断言 RLS/策略存在；
   - `users` 例外必须显式白名单，不能“漏了也算过”。
4. 如果发现缺策略：**先改 `docs/06`，再加 Alembic 迁移**（`0007_rls_hardening`），不许手改库。
5. 跑 `tests/api/test_rls_isolation.py` + 新增用例。

**完成判据**：13 张表审计矩阵有结论；缺的已补；新增测试全绿。
**不要做**：不要给 `users` 强行开 RLS；不要改登录流程。

---

### N2 · 后端 P2/P3 安全清债（P1）

**先读**：`ARCHITECTURE` §8.1、§9.2、§11.2、§15、§18；`docs/15` P2/P3 清单。

**只做下面这些“不需要产品决策”的**：

| 项 | 做什么 | 验收 |
|---|---|---|
| P3-2 | `api/persons.py` 的 `limit` 加 `Query(ge=1, le=200)`，防 `limit=99999` | 超限 422；正常列表仍绿 |
| P3-5 | `core/ratelimit.py` 每次 check 清掉过期空 key；加单测 | 连续 1000 个假 IP 后字典不无限增长 |
| P3-4 | `api/settings.py` 写操作改用 `dependencies=[Depends(require_csrf)]`，与全项目一致 | 现有设置用例全绿 |
| P3-6 | `confirm.list_confirmations` 批量查 `ChatSegment`，消 N+1 | 查询次数断言（可用 SQLAlchemy event 计数） |
| P2-11 | `ingest` 写文件后 commit 失败要删孤儿文件 | 模拟 commit 抛错，断言文件被删 |
| P3-8 | `docs/待填写清单.md` 重复段落去重 | 文件只剩一份 |

**不要做**：
- `avatar_path` 只出不进（P3-7）— 是“删字段还是补上传”的产品决策，进 `docs/29`。
- P2-10 死代码批量删除 — 哪些是“以后要用”需要人判断，进 `docs/29`。
- 不改表结构（N1 的 RLS 迁移除外）。

**完成判据**：每项有测试或可复现验证；全量 pytest 仍绿；报告里逐项写“改了什么/怎么验”。

---

### N3 · 备份恢复演练（P1，可恢复性）

**先读**：`ARCHITECTURE` §6.1、§17；`backup.sh`、`docker-compose.yml`。

**做**：
1. 确认 `backup/` 有最近备份；没有就先跑 `./backup.sh`（只读生产，安全）。
2. 新增 `backend/scripts/verify_backup.sh`：
   - 参数：备份文件路径；
   - **目标库名必须含 `_restore_check`，否则拒绝执行**；
   - 建临时库 → `psql` 恢复 → 检查 13 张表存在、RLS 开启、关键表行数；
   - 无论成败都 drop 临时库（Trap）。
3. 跑一次，输出摘要；把命令和结果写进 `docs/30`。
4. 如果恢复失败：不修生产库，记录失败点和错误原文，标 `BLOCKED-ENV`。

**完成判据**：脚本可重复跑；临时库已清理；生产库未被写入。
**禁止**：不要对 `keeper` 库执行 restore/drop。

---

### N4 · API 契约 / docs/07 漂移检查（P1）

**先读**：`ARCHITECTURE` §9.2、§12、§20、§21；`docs/07` 端点总表。

**做**：
1. 新增 `backend/scripts/check_api_docs.py`：
   - 用 `app.openapi()` 取全部 `(method, path)`；
   - 从 `docs/07` 抓 `/api/...` 端点；
   - 输出三类：代码有文档无、文档有代码无、路径参数写法不一致。
2. 先跑一次，按结果修 `docs/07`（只补文档，不改接口）。
3. 如果“文档有、代码无”是产品功能缺失：不要新写接口，记入 `docs/29` 决策。
4. 脚本退出码：有漂移 = 1，无漂移 = 0；把它写进 `docs/30` 的验证命令。

**完成判据**：脚本可重复跑；当前仓库漂移为 0 或已逐条解释。

---

### N5 · 高风险分支测试（P1，质量）

**先读**：`ARCHITECTURE` §8.11、§15、§17；`docs/25` 未覆盖原因。

**做**（只补测试，不改业务行为）：
1. `notify`：主通道失败 → 降级通道成功；两路都失败 → 返回 false；不得产生假 DB 记录。
2. `reminder`：模型失败 / 无 reasons → 降级不推送；有 reasons → 正常发送。
3. 登录：连续 5 次错 → 锁定 15 分钟；IP 限流返回 429。
4. 上传：超大、非图片、超过 10 张 → 413/400，且不落库。
5. `jobs/daily`：同一天跑两次不重复生成/重复推送（幂等）。

**完成判据**：新增测试全绿；覆盖率高风险模块（`notify` / `reminder` / `auth`）有提升；
**不要为了覆盖率写无意义断言**。

---

### N6 · 收尾报告与决策清单

**做**：
1. `docs/30-夜间开发报告.md`：
   - 任务表：N1–N5 状态、commit、验证；
   - 基线 vs 现在：pytest / Vitest / e2e / 覆盖率 / ruff；
   - Architecture 是否被改（理想：没有；若改了，逐条说明为什么不是“迁就代码”）；
   - 未完成/阻塞项。
2. `docs/29-待决策清单.md`：
   - 每条：编号、问题、选项、各自代价、我的建议、阻塞了哪个任务；
   - 至少把 P3-7 avatar_path、P2-10 死代码、W13-A/W14-A/W14-B 的“需要用户操作”写清。
3. `HANDOFF2.md` 顶部更新最后状态与报告链接。

**完成判据**：明早用户只读 `docs/30` 和 `docs/29` 就能知道“发生了什么、要决定什么”。

---

## 3. 推荐顺序与依赖

```
N0 预检
  ↓
N1 RLS 全表审计（安全底座，先做）
  ↓
N2 P2/P3 清债 ──→ N5 高风险分支测试（有改动就补测试）
  ↓
N3 备份恢复演练（独立，可与 N2 并行）
  ↓
N4 API 文档漂移（独立，可与 N3 并行）
  ↓
N6 报告
```

N3 与 N4 可交换；N1 不要拖到最后。

---

## 4. 遇到决策时的固定动作

1. 停当前子项，不改代码、不改 Architecture。
2. 在 `docs/29-待决策清单.md` 追加：

```markdown
### D-N · 一句话问题
- 发现于：N? / 文件:行
- 为什么不能自行决定：
- 选项 A：
- 选项 B：
- 我的建议：
- 阻塞任务：N?
```

3. 把任务标 `BLOCKED-DECISION`，继续下一个任务。
4. 不要用“先按 A 做，明天再改”绕过去；除非它是可逆的纯技术默认值，且不影响用户可见行为。

---

## 5. 明早必须交出的文件

| 文件 | 内容 |
|---|---|
| `docs/29-待决策清单.md` | 需要用户拍板/操作的事项；没有就写“无” |
| `docs/30-夜间开发报告.md` | 完成项、验证数字、commit、阻塞、Architecture 变更 |
| 代码与测试 | 全部提交并推送；工作区干净 |

**推送命令**（必须带代理）：
```bash
git -c http.proxy=http://127.0.0.1:7897 \
    -c https.proxy=http://127.0.0.1:7897 push origin main
```

---

## 6. 完成定义（Definition of Done）

- [ ] N1 RLS 矩阵：每张业务表有 RLS/策略结论，缺的已补
- [ ] N2 六项清债全部完成并有验证
- [ ] N3 备份恢复演练通过，临时库清理
- [ ] N4 API 文档漂移为 0 或逐条解释
- [ ] N5 高风险分支测试全绿
- [ ] `docs/29` / `docs/30` 完成
- [ ] `ruff format --check` / `ruff check` / `pytest -q` / 架构测试全绿
- [ ] 工作区干净，commit 已推送
- [ ] **全程没有中途问用户**

> 记住：今晚的目标不是“多写代码”，是**把 Architecture 的硬约束变成可验证的交付**。
