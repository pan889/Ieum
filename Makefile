# Ieum 개발 명령. 상세: docs/contributing/dev-setup.md (문서 저장소)
.DEFAULT_GOAL := help
SHELL := /bin/bash
API := apps/api
COMPOSE := docker compose

# uv 는 apps/api 를 워크스페이스 멤버로 인식한다. 모든 python 실행은 uv run 경유.
UV := uv run --project $(API)

.PHONY: help
help: ## 명령 목록
	@grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── 초기 세팅 ──────────────────────────────────────────────────
.PHONY: secret
secret: ## IEUM_SECRET_KEY 용 무작위 문자열 생성
	@python3 -c "import secrets; print(secrets.token_urlsafe(48))"

.PHONY: bootstrap
bootstrap: ## 최초 세팅: 의존성 + 인프라 + 마이그레이션 + 시드
	@test -f .env || (cp .env.example .env && \
		python3 -c "import re,secrets,pathlib; p=pathlib.Path('.env'); \
		p.write_text(re.sub(r'^IEUM_SECRET_KEY=.*$$','IEUM_SECRET_KEY='+secrets.token_urlsafe(48),p.read_text(),flags=re.M))" && \
		echo ".env 생성 및 IEUM_SECRET_KEY 자동 발급 완료")
	uv sync --project $(API)
	pnpm install
	$(COMPOSE) up -d postgres redis minio mailpit
	@echo "인프라 기동 대기..."; sleep 5
	$(MAKE) upgrade
	$(MAKE) seed

.PHONY: dev
dev: ## 전체 개발 환경 기동 (인프라 + api + web)
	$(COMPOSE) up -d postgres redis minio mailpit
	$(COMPOSE) up api worker web

.PHONY: infra
infra: ## 인프라만 기동
	$(COMPOSE) up -d postgres redis minio mailpit

.PHONY: down
down: ## 컨테이너 정지
	$(COMPOSE) down

.PHONY: logs
logs: ## 로그 확인. 예) make logs s=worker
	$(COMPOSE) logs -f $(s)

# ── 품질 게이트 ────────────────────────────────────────────────
.PHONY: check
check: lint typecheck security test i18n-check ## 커밋 전 필수 전체 검사

.PHONY: security
security: ## bandit 정적 분석 (medium 이상만 실패)
	$(UV) bandit -q -ll -r $(API)/src -x '*/tests/*'
	# 개발 스택 도구도 본다. 저장소에 있는 파이썬은 다 검사한다.
	$(UV) bandit -q -ll -r deploy

.PHONY: lint
lint: ## ruff + eslint
	$(UV) ruff check $(API)/src $(API)/tests deploy
	$(UV) ruff format --check $(API)/src $(API)/tests deploy
	pnpm -r --if-present lint

.PHONY: fmt
fmt: ## 자동 포맷
	$(UV) ruff format $(API)/src $(API)/tests deploy
	$(UV) ruff check --fix $(API)/src $(API)/tests deploy

.PHONY: typecheck
typecheck: ## mypy --strict + tsc
	$(UV) mypy --config-file $(API)/pyproject.toml $(API)/src
	pnpm -r --if-present typecheck

.PHONY: test
test: test-api test-web ## 전체 테스트

.PHONY: test-api
test-api: ## 백엔드 테스트. 예) make test-api k=identity
	$(UV) pytest $(API)/tests $(if $(k),-k $(k),)

.PHONY: test-web
test-web: ## 프론트 테스트
	pnpm -r --if-present test

.PHONY: e2e
e2e: ## 브라우저 E2E. api(:8000) 와 web(:5173) 이 이미 떠 있어야 한다
	@curl -sf http://127.0.0.1:8000/healthz > /dev/null \
		|| (echo "api 가 :8000 에 없다. 'make dev' 로 띄우고 다시 실행한다." && exit 1)
	pnpm --filter @ieum/web run e2e

.PHONY: e2e-install
e2e-install: ## E2E 용 브라우저 내려받기 (최초 1회)
	pnpm --filter @ieum/web exec playwright install chromium

.PHONY: cov
cov: ## 서비스 레이어 커버리지 (M0 완료 조건 70%+)
	$(UV) pytest $(API)/tests --cov=ieum --cov-report=term-missing

.PHONY: i18n-check
i18n-check: ## en/ko 키 동기화 + ICU + 하드코딩 탐지
	node tools/i18n-check.mjs

# ── 데이터베이스 ───────────────────────────────────────────────
.PHONY: migrate
migrate: ## 마이그레이션 생성. 예) make migrate m="add issue table"
	@test -n "$(m)" || (echo "사용법: make migrate m=\"메시지\""; exit 1)
	$(UV) alembic -c $(API)/alembic.ini revision --autogenerate -m "$(m)"
	@echo "생성된 파일을 반드시 육안 검토할 것 (인덱스·제약 보강)"

.PHONY: upgrade
upgrade: ## 최신 리비전 적용
	$(UV) alembic -c $(API)/alembic.ini upgrade head

.PHONY: downgrade
downgrade: ## 한 단계 되돌리기
	$(UV) alembic -c $(API)/alembic.ini downgrade -1

.PHONY: migration-check
migration-check: ## 모델과 마이그레이션 불일치 검사 (CI 게이트)
	$(UV) alembic -c $(API)/alembic.ini upgrade head
	$(UV) alembic -c $(API)/alembic.ini check

.PHONY: seed
seed: ## 관리자 계정 + 기본 역할·워크스페이스 생성
	# `.env` 가 있으면 읽는다. SEED_* 는 pydantic 설정이 아니라 `os.getenv` 로
	# 보므로, 여기서 넣어 주지 않으면 .env 에 적어 둔 값이 시드에 닿지 않는다 —
	# 비밀번호가 없으면 관리자 생성이 조용히 넘어간다. 없는 파일을 가리키면
	# uv 가 실패하므로 있을 때만 붙인다(CI 는 잡 환경으로 준다).
	$(UV) $$(test -f .env && echo --env-file .env) python -m ieum.cli seed

.PHONY: seed-fields
seed-fields: ## 데모용 커스텀 필드 정의 (관리 화면이 생기기 전까지)
	$(UV) python -m ieum.cli seed-fields

.PHONY: reset-db
reset-db: ## 볼륨 삭제 후 재생성 (주의: 데이터 전부 삭제)
	$(COMPOSE) down -v postgres
	$(COMPOSE) up -d postgres
	@sleep 5
	$(MAKE) upgrade seed

# ── 생성물 ─────────────────────────────────────────────────────
.PHONY: openapi
openapi: ## OpenAPI 스키마 덤프
	$(UV) python -m ieum.cli openapi > $(API)/openapi.json

.PHONY: api-client
api-client: openapi ## OpenAPI → packages/api-client TS 클라이언트 재생성
	pnpm --filter @ieum/api-client run generate
