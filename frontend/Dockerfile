# 前端镜像：构建静态文件，交给 Caddy 托管
FROM node:22-alpine AS build

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --no-frozen-lockfile

COPY . .
RUN pnpm build

FROM alpine:3.20

WORKDIR /srv
COPY --from=build /app/dist /srv

CMD ["true"]
