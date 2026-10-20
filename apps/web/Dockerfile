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
# **설정은 템플릿이다** (`nginx.conf` 머리 주석). 진입점이 기동할 때
# `/etc/nginx/templates/*.template` 를 envsubst 로 펴서 `conf.d/` 에 쓴다.
#
# - `IEUM_API_UPSTREAM` 의 기본값이 compose 의 서비스 이름이다. 그래서 compose
#   설치본들은 아무것도 안 줘도 예전과 같이 돈다. Helm 은 `<fullname>-api` 를 준다.
# - `NGINX_ENVSUBST_FILTER` 로 `IEUM_` 만 편다. 안 거르면 환경에 있는 아무
#   이름이나 nginx 의 `$변수` 자리를 갈아 버린다.
ENV IEUM_API_UPSTREAM=api:8000 \
    NGINX_ENVSUBST_FILTER=^IEUM_
COPY apps/web/nginx.conf /etc/nginx/templates/default.conf.template
# **기본값으로 한 번 미리 펴 둔다.** 진입점은 명령이 `nginx` 일 때만 템플릿을
# 펴므로, 명령을 갈아 끼운 배포에서는 이미지에 든 `default.conf` 가 그대로
# 쓰인다 — 안 펴 두면 그것이 nginx 의 환영 페이지다.
#
# 읽기 전용 루트로 돌린다면 `/etc/nginx/conf.d` 도 쓸 수 있는 자리(emptyDir·
# tmpfs)여야 한다. 진입점이 거기에 쓰지 못하면 컨테이너가 기동하지 않는다
# (재어 봤다). `/var/cache/nginx`·`/run` 과 같은 처지다.
RUN envsubst '${IEUM_API_UPSTREAM}' \
        < /etc/nginx/templates/default.conf.template \
        > /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
# `default.conf` 의 server 블록이 여기를 `include` 한다. 배포가 자기 자리를
# 덧붙이는 칸이다(번들 MinIO 를 같은 오리진에 붙이는 설치본이 쓴다). 비어
# 있으면 아무 일도 없다 — 글롭이 하나도 안 맞으면 nginx 는 그냥 지나간다.
# **디렉터리는 이미지가 만든다.** 없으면 도커가 마운트할 때 만들어 주긴
# 하지만, 아무것도 안 마운트하는 배포에서 nginx 가 기동에 실패한다.
RUN mkdir -p /etc/nginx/ieum-extra
EXPOSE 80
