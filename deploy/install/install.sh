#!/bin/sh
# Ieum 설치·판 올리기. 한 번 치면 끝난다.
#
#   ./install.sh                              물어보면서 깐다
#   ./install.sh --url https://ieum.example.com --yes    안 물어보고 깐다
#   ./install.sh --version 1.1.0              깔린 것을 그 판으로 올린다
#
# **이미지는 빌드하지 않고 당긴다.** 설치한 사람과 우리가 같은 바이트를
# 돌려야 "무엇이 돌고 있냐" 에 답할 수 있다.
#
# 이 스크립트는 두 가지만 만든다: `.env`(비밀이 들어 있다, 0600)와
# `compose.yml`(옆에 있으면 그것을 쓰고, 없으면 그 판의 것을 받아 온다).
# 나머지는 전부 `docker compose` 가 한다 — 그래서 이 스크립트 없이도
# 다룰 수 있다.

set -eu

VERSION_DEFAULT="1.0.0"
IMAGE_OWNER_DEFAULT="pan889"
PORT_DEFAULT="8080"
RAW_BASE="https://raw.githubusercontent.com/pan889/Ieum"

say()  { printf '%s\n' "$*"; }
step() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
사용법: install.sh [옵션]

  --url URL         브라우저에 칠 주소. 예) https://ieum.example.com
                    포트가 붙어 있으면 그 포트로 연다. 없으면 8080 으로 열고
                    앞에 세운 프록시가 그리로 넘기면 된다.
  --port N          여는 포트를 직접 정한다 (--url 에서 뽑은 값을 덮는다)
  --bind ADDR       그 포트를 묶을 주소. 기본 0.0.0.0.
                    앞에 프록시가 있으면 127.0.0.1 을 권한다
  --version V       깔거나 올릴 판. 예) 1.0.0
  --owner NAME      이미지를 받아올 GitHub 소유자 (포크용). 기본 pan889
  --dir PATH        설치할 자리. 기본은 지금 디렉터리
  --admin EMAIL     첫 관리자 메일. 기본 admin@example.com
  --yes             안 물어본다 (기본값과 옵션만으로 진행)
  --help            이 도움말
USAGE
}

# ── 옵션 ────────────────────────────────────────────────────────
url=""; port=""; bind=""; version=""; owner=""; dir="."; admin=""; assume_yes=0
while [ $# -gt 0 ]; do
    case "$1" in
        --url)     url="${2:?--url 에 값이 없다}"; shift 2 ;;
        --port)    port="${2:?--port 에 값이 없다}"; shift 2 ;;
        --bind)    bind="${2:?--bind 에 값이 없다}"; shift 2 ;;
        --version) version="${2:?--version 에 값이 없다}"; shift 2 ;;
        --owner)   owner="${2:?--owner 에 값이 없다}"; shift 2 ;;
        --dir)     dir="${2:?--dir 에 값이 없다}"; shift 2 ;;
        --admin)   admin="${2:?--admin 에 값이 없다}"; shift 2 ;;
        --yes|-y)  assume_yes=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; die "모르는 옵션: $1" ;;
    esac
done

# ── 있어야 하는 것 ──────────────────────────────────────────────
step "무엇이 있는지 본다"

command -v docker >/dev/null 2>&1 \
    || die "docker 가 없다. https://docs.docker.com/engine/install/ 을 먼저 본다."

# v1 의 `docker-compose` 로는 안 된다. 이 파일은 Compose 규격의 요즘 문법
# (`configs.content`, `depends_on.condition`)을 쓴다.
docker compose version >/dev/null 2>&1 \
    || die "docker compose(v2) 가 없다. 도커 엔진을 최신으로 올리거나 compose 플러그인을 깐다."

docker info >/dev/null 2>&1 \
    || die "도커 데몬에 못 붙는다. 데몬이 떠 있는지, 이 계정이 docker 를 쓸 수 있는지 본다."

say "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?') / compose $(docker compose version --short 2>/dev/null || echo '?')"

mkdir -p "$dir"
dir=$(CDPATH='' cd -- "$dir" && pwd)
env_file="$dir/.env"
compose_file="$dir/compose.yml"

# 무작위 비밀. **여기서 만든 것이 곧 그 설치본의 뿌리 키다.**
#
# `if ... fi | tr` 로 짜지 않는다. 파이프는 서브셸이라 그 안의 `die` 가
# 스크립트를 못 죽이고, 그러면 **빈 비밀 키가 .env 에 적힌다.**
rand() {
    _raw=""
    if command -v openssl >/dev/null 2>&1; then
        _raw=$(openssl rand -base64 48)
    elif [ -r /dev/urandom ] && command -v base64 >/dev/null 2>&1; then
        _raw=$(head -c 48 /dev/urandom | base64)
    else
        die "무작위 값을 만들 방법이 없다 (openssl 도 base64 도 없다)."
    fi
    _out=$(printf '%s' "$_raw" | tr -d '\n=+/' | cut -c1-40)
    # 32자 미만이면 IEUM_SECRET_KEY 검사에서 앱이 기동을 거부한다.
    # 조용히 짧은 값을 쓰느니 여기서 멈춘다.
    [ "${#_out}" -ge 32 ] || die "무작위 값이 너무 짧게 나왔다 (${#_out}자)."
    printf '%s' "$_out"
}

# `.env` 에서 한 줄 읽는다. 없으면 빈 문자열.
#
# `A && B || C` 로 안 쓴다 — B 가 실패해도 C 가 돌아서 "없음" 과 "읽다 실패"
# 가 같은 값이 된다.
env_get() {
    [ -f "$env_file" ] || return 0
    sed -n "s/^$1=//p" "$env_file" | head -1
}

fresh=1
[ -f "$env_file" ] && fresh=0

# ── 판 ──────────────────────────────────────────────────────────
if [ -z "$version" ]; then
    version=$(env_get IEUM_VERSION)
    [ -n "$version" ] || version="$VERSION_DEFAULT"
fi
if [ -z "$owner" ]; then
    owner=$(env_get IEUM_IMAGE_OWNER)
    [ -n "$owner" ] || owner="$IMAGE_OWNER_DEFAULT"
fi

# 판과 소유자는 **이미지 이름과 URL 에 그대로 들어간다.** 이상한 글자가
# 섞이면 그 자리가 아니라 한참 뒤 docker 나 sed 에서 알아보기 힘든 모양으로
# 터진다. 도커 태그가 받는 글자만 통과시킨다.
case "$version" in
    ""|*[!A-Za-z0-9._-]*) die "판 이름에 못 쓰는 글자가 있다: '$version'" ;;
esac
case "$owner" in
    ""|*[!A-Za-z0-9._-]*) die "소유자 이름에 못 쓰는 글자가 있다: '$owner'" ;;
esac

# ── 주소 ────────────────────────────────────────────────────────
if [ "$fresh" -eq 0 ]; then
    step "이미 깔려 있다 — 판을 올린다"
    old_version=$(env_get IEUM_VERSION)
    say "$dir 에 설치본이 있다 (판 ${old_version:-?} → $version)"
    # **비밀은 다시 만들지 않는다.** IEUM_SECRET_KEY 가 바뀌면 그 키로 푼
    # 것들(MFA 시크릿·웹훅 시크릿)이 통째로 못 읽는 값이 된다.
    [ -n "$(env_get IEUM_SECRET_KEY)" ] \
        || die ".env 는 있는데 IEUM_SECRET_KEY 가 없다. 손으로 고친 파일 같다 — 지우지 말고 살펴본다."
    for ignored in "$url" "$port" "$bind" "$admin"; do
        [ -z "$ignored" ] || {
            warn "주소·포트·계정 옵션은 이미 깔린 설치본에는 안 먹는다."
            warn "고치려면 $env_file 의 IEUM_PUBLIC_URL·IEUM_HTTP_PORT·IEUM_HTTP_BIND 를 고친다."
            break
        }
    done
else
    step "어디로 접속할지 정한다"
    if [ -z "$url" ] && [ "$assume_yes" -eq 0 ] && [ -t 0 ]; then
        say "브라우저 주소창에 칠 주소를 적는다. 이 값이 로그인 쿠키·첨부 링크·"
        say "메일 안의 링크에 그대로 들어간다 — 나중에 바꾸려면 .env 를 고쳐야 한다."
        printf '주소 [http://localhost:%s]: ' "$PORT_DEFAULT"
        read -r answer || answer=""
        url="$answer"
    fi
    [ -n "$url" ] || url="http://localhost:$PORT_DEFAULT"
fi

if [ "$fresh" -eq 1 ]; then
    # 끝의 `/` 는 뗀다. 안 떼면 CORS 오리진이 안 맞고 링크에 `//` 가 낀다.
    while [ "${url%/}" != "$url" ]; do url="${url%/}"; done
    case "$url" in
        http://*|https://*) ;;
        *) die "주소는 http:// 또는 https:// 로 시작해야 한다: $url" ;;
    esac

    # 포트를 주소에서 뽑는다. 없으면 앞에 프록시가 있다는 뜻이라 8080 으로 연다.
    if [ -z "$port" ]; then
        hostport="${url#*://}"; hostport="${hostport%%/*}"
        case "$hostport" in
            *:*) port="${hostport##*:}" ;;
            *)   port="$PORT_DEFAULT" ;;
        esac
    fi
    # 숫자가 아니면 여기서 잡는다. compose 까지 가면 "invalid published port"
    # 라는, 무엇을 잘못 쳤는지 안 알려 주는 오류가 난다. IPv6 리터럴 주소
    # (`http://[::1]`)를 넣었을 때 여기로 떨어진다.
    case "$port" in
        ""|*[!0-9]*) die "포트가 숫자가 아니다: '$port' — --port 로 직접 준다" ;;
    esac
    if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        die "포트 범위를 벗어났다: $port"
    fi

    [ -n "$bind" ] || bind="0.0.0.0"
    [ -n "$admin" ] || admin="admin@example.com"
fi

# ── .env ────────────────────────────────────────────────────────
if [ "$fresh" -eq 1 ]; then
    step ".env 를 만든다"
    admin_password=$(rand | cut -c1-20)
    # 만들어지는 **그 순간부터** 남이 못 읽게 한다. 뒤에서 chmod 를 해도
    # 그 사이의 짧은 창에 비밀이 노출된다. 끝나면 되돌린다 — 안 그러면
    # 뒤에 놓는 compose.yml·install.sh 까지 0600 이 된다.
    _umask=$(umask)
    umask 077
    cat > "$env_file" <<ENV
# Ieum 설치 설정. install.sh 가 만들었다.
#
# **이 파일에는 비밀이 들어 있다.** 특히 IEUM_SECRET_KEY 는 세션·MFA·웹훅
# 시크릿을 푸는 뿌리다 — 잃어버리면 그 값들을 되살릴 방법이 없다.
# 백업에 이 파일을 포함한다.

# 당겨 올 판과 소유자.
IEUM_VERSION=$version
IEUM_IMAGE_OWNER=$owner

# 브라우저가 보는 주소. 화면·API·첨부가 다 이 오리진으로 나간다.
IEUM_PUBLIC_URL=$url
IEUM_HTTP_PORT=$port
IEUM_HTTP_BIND=$bind

# 비밀 — 자동 발급.
IEUM_SECRET_KEY=$(rand)
IEUM_DB_PASSWORD=$(rand)
IEUM_S3_ACCESS_KEY=$(rand | cut -c1-20)
IEUM_S3_SECRET_KEY=$(rand)

# 첫 관리자. 들어가서 비밀번호를 바꾸고 나면 이 두 줄은 지워도 된다.
SEED_ADMIN_EMAIL=$admin
SEED_ADMIN_PASSWORD=$admin_password

# 메일 — **비워 두면 안 나간다.** 초대·알림·다이제스트가 조용히 멈춘다.
# 쓰려면 아래를 채우고 \`docker compose up -d\` 를 다시 친다.
IEUM_SMTP_HOST=
IEUM_SMTP_PORT=587
IEUM_SMTP_TLS=true
IEUM_SMTP_USER=
IEUM_SMTP_PASSWORD=
IEUM_MAIL_FROM=ieum@example.com
ENV
    chmod 600 "$env_file"
    umask "$_umask"
    say "$env_file (0600)"
else
    # 판만 바꿔 넣는다. 나머지는 손대지 않는다.
    tmp="$env_file.tmp.$$"
    if grep -q '^IEUM_VERSION=' "$env_file"; then
        sed "s/^IEUM_VERSION=.*/IEUM_VERSION=$version/" "$env_file" > "$tmp"
    else
        { cat "$env_file"; printf 'IEUM_VERSION=%s\n' "$version"; } > "$tmp"
    fi
    cat "$tmp" > "$env_file" && rm -f "$tmp"
fi

# ── compose.yml ─────────────────────────────────────────────────
#
# **판을 올릴 때마다 그 판의 것으로 갈아 끼운다.** 처음 깔 때만 놓으면, 새 판이
# 서비스를 하나 늘리거나 헬스체크를 고쳐도 옛 파일이 그대로 남아서 이미지만
# 새것이 된다 — 그 어긋남은 몇 달 뒤 엉뚱한 증상으로 나타난다.
#
# 그래서 이 파일은 **우리 것**이다. 고칠 것이 있으면 `compose.override.yml` 에
# 적는다 — compose 가 알아서 겹쳐 읽고, 이 스크립트는 그 파일을 건드리지 않는다.
step "compose.yml 을 놓는다"
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$script_dir/compose.yml" ] && [ "$script_dir/compose.yml" != "$compose_file" ]; then
    cp "$script_dir/compose.yml" "$compose_file"
    say "$script_dir/compose.yml 에서 복사했다"
elif [ ! -f "$script_dir/compose.yml" ]; then
    remote="$RAW_BASE/v$version/deploy/install/compose.yml"
    say "$remote 에서 받는다"
    # 내려받다 끊기면 반쪽짜리 파일이 남는다. 다 받은 뒤에 옮긴다.
    tmp_compose="$compose_file.new.$$"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$remote" -o "$tmp_compose" \
            || { rm -f "$tmp_compose"; die "compose.yml 을 못 받았다. v$version 태그가 있는지 본다."; }
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$tmp_compose" "$remote" \
            || { rm -f "$tmp_compose"; die "compose.yml 을 못 받았다. v$version 태그가 있는지 본다."; }
    else
        die "curl 도 wget 도 없다. compose.yml 을 손으로 받아 $dir 에 둔다."
    fi
    mv "$tmp_compose" "$compose_file"
else
    say "이미 이 자리에 있다"
fi

# 설치 자리가 **자기 판올림 도구를 들고 있게** 한다. 안 그러면 마지막에
# 찍어 주는 `./install.sh --version …` 이 그 디렉터리에서 안 먹는다.
#
# `mv` 로 갈아 끼운다 — `cp` 로 덮으면 지금 **자기 자신을 실행 중인** 파일을
# 제자리에서 고치는 것이 되고, sh 는 스크립트를 조금씩 읽으므로 남은 부분이
# 어긋난다. 이름 바꾸기는 열려 있는 inode 를 안 건드린다.
self="$dir/install.sh"
if [ "$script_dir/install.sh" != "$self" ] && [ -f "$script_dir/install.sh" ]; then
    cp "$script_dir/install.sh" "$self.new.$$"
    chmod +x "$self.new.$$"
    mv "$self.new.$$" "$self"
fi

# ── 당긴다 ──────────────────────────────────────────────────────
step "이미지를 당긴다 (판 $version)"
if ! (cd "$dir" && docker compose pull); then
    say ""
    # **어느 이미지가 막혔는지 위의 줄을 보라고 말한다.** 우리 이미지는
    # ghcr.io 에서 오지만 Postgres·Redis·MinIO 는 Docker Hub 에서 온다 —
    # 실제로 Hub 의 익명 내려받기 제한에 걸려 여기서 멈추는 것을 봤다.
    # 원인을 하나로 단정하면 엉뚱한 데를 고치게 된다.
    warn "이미지를 못 당겼다. 위의 줄이 **어느 이미지**에서 막혔는지 말해 준다."
    warn ""
    warn "  ghcr.io/$owner/ieum-* 였다면:"
    warn "    · 그 판이 아직 안 올라갔을 수 있다 (태그 v$version 이 있는지 본다)"
    warn "    · 패키지가 비공개면 'docker login ghcr.io' 로 로그인한 뒤 다시 친다"
    warn "  postgres·redis·minio 였다면:"
    warn "    · Docker Hub 의 익명 내려받기 제한이다. 'docker login' 하거나 잠시 뒤에"
    warn "  둘 다였다면 이 기계가 밖으로 못 나가는 것이다 — 프록시·방화벽을 본다"
    die "여기서 멈춘다. 반쯤 깔린 상태로 두지 않는다."
fi

# ── 띄운다 ──────────────────────────────────────────────────────
#
# `--force-recreate` 를 쓰는 이유: **compose 는 인라인 설정(`configs.content`)
# 이 바뀐 것을 컨테이너 재생성 조건으로 안 본다.** 재어 봤다 — compose.yml 의
# 프록시 설정을 고치고 `up -d` 를 쳐도 컨테이너 안의 파일은 옛것 그대로다.
# 그러면 판을 올려도 이미지만 새것이 되고 설정은 안 따라간다.
#
# 이 명령은 사람이 일부러 치는 설치·판올림이지 평소의 `up` 이 아니다. 몇 초
# 더 걸리더라도 **파일에 적힌 그대로**를 만들어 놓는 쪽이 맞다.
step "띄운다 (스키마 올리기와 시드가 앱보다 먼저 돈다)"
(cd "$dir" && docker compose up -d --wait --force-recreate) || {
    say ""
    warn "기동이 끝나지 않았다. 무엇이 막혔는지는 로그에 있다:"
    warn "  cd $dir && docker compose logs --tail 50 migrate api"
    die "여기서 멈춘다."
}

# ── 다 됐다 ─────────────────────────────────────────────────────
public_url=$(env_get IEUM_PUBLIC_URL)
step "다 됐다"
say ""
say "  주소   $public_url"
if [ "$fresh" -eq 1 ]; then
    say "  계정   $(env_get SEED_ADMIN_EMAIL)"
    say "  비밀번호  $(env_get SEED_ADMIN_PASSWORD)"
    say ""
    say "들어가자마자 비밀번호를 바꾼다. 바꾸고 나면 .env 의 SEED_ADMIN_* 두 줄은 지워도 된다."
fi
say ""
say "  로그      cd $dir && docker compose logs -f"
say "  세우기    cd $dir && docker compose down"
say "  판 올리기 cd $dir && ./install.sh --version <판>"
say ""

case "$public_url" in
    http://localhost*|http://127.0.0.1*)
        warn "주소가 localhost 다. 다른 기계에서 열 거라면 .env 의 IEUM_PUBLIC_URL 을"
        warn "그 주소로 고치고 'docker compose up -d' 를 다시 친다 — 지금 값으로는"
        warn "로그인은 되어도 첨부 링크가 남의 기계에서 안 열린다."
        ;;
    http://*)
        warn "http 다. 로그인 쿠키가 평문으로 오간다 — 앞에 TLS 를 세우는 것을 권한다."
        ;;
esac
[ -n "$(env_get IEUM_SMTP_HOST)" ] \
    || warn "SMTP 가 비어 있다. 초대·알림 메일이 안 나간다 (.env 의 IEUM_SMTP_* 를 채운다)."
say ""
say "IEUM_SECRET_KEY 를 백업한다. 잃어버리면 MFA·웹훅 시크릿을 되살릴 수 없다:"
say "  $env_file"
