# @ieum/api-client

OpenAPI 에서 **생성된** 타입 + 얇은 런타임 래퍼.

```bash
make api-client     # apps/api/openapi.json 덤프 → src/generated/schema.ts
```

`src/generated/` 는 커밋하지 않는다(.gitignore). 손으로 인터페이스를 쓰지 않는다
(docs/contributing/conventions.md API 규약).

런타임 래퍼가 하는 일은 세 가지뿐이다:
- 액세스 토큰을 헤더에 붙인다
- 401 이면 리프레시를 1회 시도하고 원 요청을 재시도한다
- 에러 응답을 `ApiError` 로 정규화한다 (code/details/trace_id 보존)
