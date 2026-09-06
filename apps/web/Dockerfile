# syntax=docker/dockerfile:1
FROM node:22-alpine AS build

RUN corepack enable
WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/api-client/package.json packages/api-client/
COPY packages/i18n/package.json packages/i18n/
RUN pnpm install --frozen-lockfile

COPY . .
# 생성된 API 클라이언트가 있어야 타입이 맞는다. 없으면 빌드 단계에서 만든다.
RUN test -f apps/api/openapi.json && pnpm --filter @ieum/api-client run generate || true
RUN pnpm --filter @ieum/web run build

FROM nginx:1.29-alpine AS runtime
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
EXPOSE 80
