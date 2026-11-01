# e2e · 真浏览器验收

一条命令跑完，结果是退出码 + `last-summary.json`。

## 为什么单独一套

单元测试验不了"按钮点下去有没有反应"。这套用真 Chromium 点真页面，
只跑**隔离账号**（`seed_e2e.py` 建的），绝不碰真实数据。

## 前置

```bash
# 1. 种子数据（幂等，跑几次都一样）
cd backend && uv run python scripts/seed_e2e.py

# 2. 后端起在 8001（和日常的 8000 分开，互不影响）
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8001

# 3. 前端起在 5273，指向 8001
cd frontend && VITE_PORT=5273 VITE_API_TARGET=http://127.0.0.1:8001 npx vite
```

## 跑

```bash
cd e2e && npm install          # 只装 playwright，浏览器已在系统缓存里
node run.mjs --list            # 看有哪些用例
node run.mjs --smoke           # 只跑冒烟（快速回归）
node run.mjs                   # 全跑
node run.mjs --only calendar   # 单跑一个
```

默认连 `http://127.0.0.1:5173`，所以上面这种并存的场景要显式给：

```bash
E2E_BASE_URL=http://127.0.0.1:5273 node run.mjs --smoke
```

## 约定

- **用例自己清场**：建的临时数据自己删，不留垃圾给下次
- **日期相对算**：`day(3)` 而不是 `2026-11-05`，脚本不会过期
- **失败自动截图**：`artifacts/<用例>-FAILED.png`
- **页面报错算失败**：控制台 error 和未捕获异常都会让用例红，不放过"静默白屏"
- **只断言用户看得见的东西**：不测内部状态，测界面上有没有、对不对

## 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `E2E_BASE_URL` | `http://127.0.0.1:5173` | 前端地址 |
| `E2E_USERNAME` | `keeper-e2e` | 隔离账号（**别换成真实账号**） |
| `E2E_PASSWORD` | `keeper-e2e-pass` | 同上 |
| `E2E_HEADFUL` | 空 | 设 `1` 打开可见浏览器，排查用 |
