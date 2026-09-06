/** 서버 에러 응답. 포맷은 고정 계약이다 (CLAUDE.md 8절). */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details: Record<string, unknown>
    trace_id: string | null
  }
}

/**
 * 정규화된 API 오류.
 *
 * `message` 는 디버깅용 서버 문구다. 사용자에게는 `code` 를 번역해서 보여준다
 * (docs/architecture/i18n.md 1절).
 */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details: Record<string, unknown>
  readonly traceId: string | null

  constructor(status: number, body: Partial<ApiErrorBody['error']> & { code: string }) {
    super(body.message ?? body.code)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.details = body.details ?? {}
    this.traceId = body.trace_id ?? null
  }

  /** 번역 키. errors.json 의 키가 곧 에러 코드다. */
  get translationKey(): string {
    return `errors:${this.code}`
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    let parsed: unknown
    try {
      parsed = await response.json()
    } catch {
      return new ApiError(response.status, { code: 'common.http_error' })
    }
    const body = (parsed as Partial<ApiErrorBody>).error
    if (!body?.code) {
      return new ApiError(response.status, { code: 'common.http_error' })
    }
    return new ApiError(response.status, { ...body, code: body.code })
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError
}
