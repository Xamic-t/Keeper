/// <reference types="vite/client" />

/**
 * 自己用到的环境变量在这里声明，免得各处写 any。
 * 只放**前端可见**的配置 —— 任何密钥都不能出现在 VITE_ 开头的变量里，
 * 它们会被打进 bundle。
 */
interface ImportMetaEnv {
  /** Harness（dsh）的 Web UI 地址。默认 http://127.0.0.1:3080 */
  readonly VITE_HARNESS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
