# 01 · 认证板块 Spec

> 对应文件：`core/security.py`、`services/auth.py`、`api/auth.py`、`deps.py`
> 计划：**W2**
> 依赖：`users` 表（W1 已建）

---

## 1. 这个板块解决什么问题

**一句话**：让人能登进来，并且只看到自己的数据。

产品上有三个约束，决定了这个板块的设计：

**① 不用短信验证码。** 用户是国内主播/小老板，不常看邮箱；20 人以内邀请制，账号由管理员手动创建，天然可信。所以账号体系只有 `username + password`，`username` 允许填用户名 / 手机号 / 邮箱，**不校验格式**。

**② 忘记密码找管理员。** 不做自助找回（要接 SMTP 或短信，成本不值）。管理员在后台重置，微信发给对方。

**③ 登录失败要有限制。** 连续 5 次锁 15 分钟。不是为了防黑客，是为了防自己手滑。

---

## 2. 业务流程

```
                    ┌─────────────────┐
   输入账号密码 ───▶ │ 账号被锁？        │──是──▶ 401 ACCOUNT_LOCKED
                    └────────┬────────┘
                             │否
                    ┌────────▼────────┐
                    │ 账号存在且启用？  │──否──▶ 401 BAD_CREDENTIALS
                    └────────┬────────┘        （不区分"不存在"和"密码错"）
                             │是
                    ┌────────▼────────┐
                    │ 密码对？          │──否──▶ 失败计数+1，≥5 则锁定
                    └────────┬────────┘
                             │是
                    ┌────────▼────────┐
                    │ 失败计数清零      │
                    │ 写 last_login_at │
                    │ 签发 cookie      │
                    └────────┬────────┘
                             ▼
                    200 + Set-Cookie(session, csrf)

   之后每个请求：
   cookie → 验签 → 查用户 → is_active？→ 挂到 request.state
```

---

## 3. 数据流

| 操作 | 读 | 写 |
|---|---|---|
| 登录 | `users` | `users.failed_attempts`、`locked_until`、`last_login_at` |
| 每次请求 | `users` | — |
| 改密码 | `users` | `users.password_hash` |

---

## 4. 函数清单

| 函数 | 一句话 | 被谁调用 |
|---|---|---|
| `hash_password` | 把明文密码变成 argon2 哈希 | `create_user`、`change_password` |
| `verify_password` | 验证明文和哈希是否匹配 | `authenticate` |
| `create_session_token` | 签发一个含 user_id 的签名串 | `login` |
| `decode_session_token` | 验签并取出 user_id | `get_current_user` |
| `generate_csrf_token` | 生成 CSRF token | `login` |
| `authenticate` | 完整登录校验（锁/存在/密码） | `api/auth.login` |
| `record_login_failure` | 失败计数 +1，够了就锁 | `authenticate` |
| `record_login_success` | 清零计数、写最后登录时间 | `authenticate` |
| `change_password` | 验旧密码 → 写新密码 | `api/auth.change_password` |
| `get_current_user` | 从 cookie 解析当前用户 | 所有业务路由（通过 `get_session`） |

---

## 5. 逐函数六问

### 5.1 `core/security.py`

---

#### `hash_password(password: str) -> str`

1) **WHAT** —— 用 argon2id 把明文密码生成不可逆哈希。
2) **WHO** —— `services/auth.create_user`、`services/auth.change_password`。
3) **INPUT** —— `password`：明文，1-128 字符，已由 Pydantic 校验过长度。
4) **OUTPUT** —— 一个以 `$argon2id$` 开头的字符串，约 100 字符。**永不为空**。
5) **HAPPY** ——
   - 调用 `PasswordHasher().hash(password)`
   - 返回结果字符串
6) **EDGE** ——
   - 密码为空 → **不在这里校验**，抛给上游（Pydantic 已拦）
   - 哈希失败（内存不足等）→ 抛 `RuntimeError`，由全局 handler 转 500
   - **绝不记录明文密码到日志**

---

#### `verify_password(password: str, hashed: str) -> bool`

1) **WHAT** —— 判断明文密码是否匹配某个 argon2 哈希。
2) **WHO** —— `services/auth.authenticate`。
3) **INPUT** —— `password` 明文；`hashed` 库里存的哈希串。
4) **OUTPUT** —— `True` 匹配 / `False` 不匹配。**不抛异常**（除哈希格式非法）。
5) **HAPPY** ——
   - 调用 `PasswordHasher().verify(hashed, password)`
   - 返回布尔值
6) **EDGE** ——
   - `hashed` 格式非法（数据被污染）→ 捕获异常，**返回 False 并记 WARNING**（不能因为这个 500）
   - `password` 为空串 → 返回 False（argon2 本身也会拒）

---

#### `create_session_token(user_id: int, max_age: int) -> str`

1) **WHAT** —— 用 `itsdangerous` 签发一个带过期时间的会话串。
2) **WHO** —— `services/auth.authenticate` 成功后。
3) **INPUT** —— `user_id` 正整数；`max_age` 有效期秒数（默认从 `settings.session_max_age` 取，7 天）。
4) **OUTPUT** —— URL-safe 的签名字符串。**永不为空**。
5) **HAPPY** ——
   - 组装 payload：`{"uid": user_id, "exp": now + max_age}`
   - `TimedSerializer(secret_key).dumps(payload)`
   - 返回字符串
6) **EDGE** ——
   - `secret_key` 为空 → 启动时就该失败，**不在这里兜底**（配置错误要早暴露）
   - 序列化异常 → 抛 500

---

#### `decode_session_token(token: str) -> int | None`

1) **WHAT** —— 验签并取出 user_id；过期或篡改返回 `None`。
2) **WHO** —— `deps.get_current_user`。
3) **INPUT** —— cookie 里的原始字符串。
4) **OUTPUT** —— 合法则返回 `user_id`（int），否则 `None`。**不抛异常**。
5) **HAPPY** ——
   - `TimedSerializer.loads(token, max_age=...)`
   - 取出 `payload["uid"]`
   - 返回 int
6) **EDGE** ——
   - `token` 为空 / 非字符串 → 返回 None
   - 签名不对 → `BadSignature` → 返回 None
   - 过期 → `SignatureExpired` → 返回 None
   - payload 里没有 `uid` → 返回 None
   - **所有失败一律返回 None，不区分原因**（避免给攻击者信息）

---

#### `generate_csrf_token() -> str`

1) **WHAT** —— 生成一个随机串，写进 cookie 供前端回传校验。
2) **WHO** —— `api/auth.login`。
3) **INPUT** —— 无。
4) **OUTPUT** —— 32 字节 URL-safe 随机串。
5) **HAPPY** —— `secrets.token_urlsafe(32)`
6) **EDGE** —— 无边界情况（`secrets` 不会失败）

---

#### `verify_csrf(cookie_token: str | None, header_token: str | None) -> bool`

1) **WHAT** —— 校验请求头里的 CSRF token 和 cookie 里的是否一致。
2) **WHO** —— `deps.get_current_user` 之后的写操作依赖。
3) **INPUT** —— cookie 值、请求头值，都可能为 None。
4) **OUTPUT** —— `True` 通过 / `False` 不通过。
5) **HAPPY** ——
   - 两者都非空
   - 用 `secrets.compare_digest` 恒定时间比较
   - 返回结果
6) **EDGE** ——
   - 任一为 None → False
   - 长度不同 → 直接 False（compare_digest 会报错，先比长度）
   - **只读请求（GET/HEAD）不校验**

---

### 5.2 `services/auth.py`

---

#### `authenticate(session, username: str, password: str) -> User`

1) **WHAT** —— 完整登录校验，通过则返回用户对象。
2) **WHO** —— `api/auth.login`。
3) **INPUT** —— `session` 系统级 session（**不能用带 RLS 的，此时还没 user_id**）；`username` 和 `password` 来自表单。
4) **OUTPUT** —— `User` 实例。
5) **HAPPY** ——
   - 按 `username` 查 `users`
   - 查不到 → 抛 `AppError(BAD_CREDENTIALS, 401)`
   - `locked_until > now()` → 抛 `AppError(ACCOUNT_LOCKED, 401)`，message 里带剩余分钟数
   - `is_active=False` → 抛 `AppError(BAD_CREDENTIALS, 401)`（**和密码错同一个码**）
   - `verify_password` 失败 → `record_login_failure` → 抛 `AppError(BAD_CREDENTIALS, 401)`
   - 全部通过 → `record_login_success` → 返回 user
6) **EDGE** ——
   - **账号不存在和密码错必须返回同一个错误码**，防账号枚举
   - 锁定判断要在密码校验**之前**（否则无法锁定期间试密码）
   - 数据库异常 → 抛 500，不要吞

---

#### `record_login_failure(session, user: User) -> None`

1) **WHAT** —— 失败计数 +1，达到 5 次则锁定 15 分钟。
2) **WHO** —— `authenticate`。
3) **INPUT** —— `session`、查到的 `user`。
4) **OUTPUT** —— 无。
5) **HAPPY** ——
   - `user.failed_attempts += 1`
   - 若 `>= 5`：`user.locked_until = now + 15min`，`failed_attempts` 归零
   - commit
6) **EDGE** ——
   - 并发两次失败请求 → 可能丢一次计数，**可接受**（不是安全关键）
   - 用户已被删除（并发）→ 忽略，不抛异常

---

#### `record_login_success(session, user: User) -> None`

1) **WHAT** —— 清零失败计数，写最后登录时间。
2) **WHO** —— `authenticate`。
3) **INPUT** —— `session`、`user`。
4) **OUTPUT** —— 无。
5) **HAPPY** ——
   - `failed_attempts = 0`
   - `locked_until = None`
   - `last_login_at = now`
   - commit
6) **EDGE** —— commit 失败 → 抛 500（登录成功但记录没写，宁可报错）

---

#### `change_password(session, user: User, old_password: str, new_password: str) -> None`

1) **WHAT** —— 验旧密码后更新为新密码。
2) **WHO** —— `api/auth.change_password`。
3) **INPUT** —— `user` 当前用户；两个密码串。
4) **OUTPUT** —— 无。
5) **HAPPY** ——
   - `verify_password(old_password, user.password_hash)` 失败 → 抛 `AppError(BAD_CREDENTIALS, 400)`
   - `hash_password(new_password)` → 写入
   - commit
6) **EDGE** ——
   - 新旧密码相同 → 允许（不做历史密码校验）
   - 新密码长度不合规 → Pydantic 已拦（8-128）
   - **改完要不要踢掉其他设备？** V1 不做（无会话表）

---

### 5.3 `api/auth.py`

---

#### `login(payload: LoginRequest, response: Response, session) -> dict`

1) **WHAT** —— 登录接口，签发 cookie 并返回用户信息。
2) **WHO** —— 前端登录页。
3) **INPUT** —— `{username, password}`。
4) **OUTPUT** —— `{"data": {"user": {...}, "csrf_token": "..."}}`。
5) **HAPPY** ——
   - 调 `authenticate`
   - `create_session_token` → 写 httpOnly cookie
   - `generate_csrf_token` → 写**可读** cookie（前端要读出来放到 header）
   - 返回用户信息和 csrf_token
6) **EDGE** ——
   - 失败由 `AppError` 统一处理，路由里**不写 try/except**
   - cookie 参数：`httponly=True, secure=not is_dev, samesite="lax", max_age=session_max_age`

---

#### `logout(response: Response) -> dict`

1) **WHAT** —— 清除会话 cookie。
2) **WHO** —— 用户点退出。
3) **INPUT** —— 无。
4) **OUTPUT** —— `{"data": {"ok": true}}`。
5) **HAPPY** —— `response.delete_cookie("session")` + `delete_cookie("csrf")`
6) **EDGE** —— 未登录调用也返回成功（幂等）

---

#### `me(user: CurrentUser, session) -> dict`

1) **WHAT** —— 返回当前登录用户信息。
2) **WHO** —— 前端启动时校验登录态。
3) **INPUT** —— 依赖注入的 `user`。
4) **OUTPUT** —— `{"data": {"id", "username", "display_name", "role"}}`。
5) **HAPPY** —— 直接组装返回，**不查库**（`CurrentUser` 已够）
6) **EDGE** —— 未登录 → 依赖抛 401

---

### 5.4 `deps.get_current_user`（替换占位实现）

---

#### `get_current_user(request: Request, session) -> CurrentUser`

1) **WHAT** —— 从 cookie 解析当前用户，是所有业务路由的认证入口。
2) **WHO** —— `get_session`、`require_owner`、`api/auth.me`。
3) **INPUT** —— `request`（读 cookie）。
4) **OUTPUT** —— `CurrentUser(id, username, role)`。
5) **HAPPY** ——
   - 读 `request.cookies["session"]`
   - `decode_session_token` → `user_id`
   - 用**系统级 session** 查 `users`（此时还没 user_id 设进 RLS）
   - `is_active=False` → 401
   - 返回 `CurrentUser`
6) **EDGE** ——
   - cookie 缺失 / 无效 / 过期 → `AppError(UNAUTHENTICATED, 401)`
   - 用户已被删除 → 401
   - **这里必须用 `get_raw_session` 的等价逻辑，不能用 `get_session`（否则循环依赖 + RLS 变量还没设）**

> ⚠️ 注意：此处需要系统级 session。W1 时按最小封装原则删掉了 `get_raw_session`，
> **现在有了真实调用者，可以加回来**——这正好符合 19.6「有调用者才加」。

---

## 6. 验收标准

| # | 检查 | 怎么验 |
|---|---|---|
| 1 | 三种格式都能登录 | `xamic` / `13800138000` / `a@b.com` 各建一个账号 |
| 2 | 密码错 5 次锁定 15 分钟 | 连错 5 次，第 6 次返回 `ACCOUNT_LOCKED` |
| 3 | 账号不存在和密码错同码 | 两者都返回 `BAD_CREDENTIALS` |
| 4 | 未登录访问业务接口 401 | 清 cookie 后请求 `/api/persons` |
| 5 | 改密码后旧密码失效 | 改完用旧密码登录应失败 |
| 6 | cookie 是 httpOnly | 浏览器 Console `document.cookie` 看不到 session |

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版：5 个安全原语 + 4 个 service + 3 个路由 + get_current_user 真实实现 |
