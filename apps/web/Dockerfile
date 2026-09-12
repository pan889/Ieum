# syntax=docker/dockerfile:1
FROM node:22-alpine AS build

RUN corepack enable
WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/api-client/package.json packages/api-client/
COPY packages/i18n/package.json packages/i18n/
# `ca` 시크릿은 **있으면 쓴다** — api 이미지와 같은 이유다. TLS 를 다시
# 끝내는 프록시 뒤에서 빌드하면 레지스트리 인증서가 그 CA 로 서명돼 있어서,
# 없으면 `pnpm install` 이 자기 인증서 오류로 죽는다. 검증을 끄지는 않는다.
RUN --mount=type=secret,id=ca,required=false \
    sh -c 'if [ -f /run/secrets/ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca; fi; \
     pnpm install --frozen-lockfile'

COPY . .
# 생성된 API 클라이언트가 있어야 타입이 맞는다. 없으면 빌드 단계에서 만든다.
RUN test -f apps/api/openapi.json && pnpm --filter @ieum/api-client run generate || true
RUN pnpm --filter @ieum/web run build

FROM nginx:1.29-alpine AS runtime
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
# `default.conf` 의 server 블록이 여기를 `include` 한다. 배포가 자기 자리를
# 덧붙이는 칸이다(번들 MinIO 를 같은 오리진에 붙이는 설치본이 쓴다). 비어
# 있으면 아무 일도 없다 — 글롭이 하나도 안 맞으면 nginx 는 그냥 지나간다.
# **디렉터리는 이미지가 만든다.** 없으면 도커가 마운트할 때 만들어 주긴
# 하지만, 아무것도 안 마운트하는 배포에서 nginx 가 기동에 실패한다.
RUN mkdir -p /etc/nginx/ieum-extra
EXPOSE 80
