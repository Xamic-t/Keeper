# 前端镜像：构建静态文件，交给 Caddy 托管
FROM node:22-alpine AS build

WORKDIR /app

RUN corepack enable

# pnpm-workspace.yaml 里的 allowBuilds 得在 install 前就在，否则 esbuild/vue-demi 会被 pnpm 拦掉
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml ./
RUN pnpm install --no-frozen-lockfile

COPY . .
RUN pnpm build

FROM caddy:2-alpine

WORKDIR /srv
COPY --from=build /app/dist /srv

# Caddyfile 由 docker-compose 挂进 /etc/caddy/Caddyfile，保持可改
