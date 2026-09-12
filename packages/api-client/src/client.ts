import { ApiError } from './errors'
import { tokenStore } from './tokens'

export interface ClientOptions {
  baseUrl?: string
  /** 리프레시까지 실패했을 때(=로그아웃해야 할 때) 호출된다. */
  onSessionLost?: () => void
}

interface RequestOptions {
  method?: string
  body?: unknown
  signal?: AbortSignal
  /** true 면 401 이어도 리프레시를 시도하지 않는다 (로그인·리프레시 자체). */
  anonymous?: boolean
  /** 추가 헤더. 낙관적 잠금의 If-Match 가 여기로 간다. */
  headers?: Record<string, string>
}

/**
 * 얇은 fetch 래퍼.
 *
 * 동시에 여러 요청이 401 을 받아도 리프레시는 한 번만 나간다. 안 그러면
 * 로테이션된 토큰을 서로 덮어쓰다가 서버가 재사용으로 판단해 세션 계열을
 * 통째로 폐기한다 — 즉 화면 여러 개를 띄운 사용자가 갑자기 로그아웃된다.
 */
export class ApiClient {
  private readonly baseUrl: string
  private readonly onSessionLost: (() => void) | undefined
  private refreshing: Promise<boolean> | null = null

  constructor(options: ClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? '').replace(/\/$/, '')
    this.onSessionLost = options.onSessionLost
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    let response = await this.send(path, options)

    if (response.status === 401 && !options.anonymous) {
      const refreshed = await this.refreshOnce()
      if (refreshed) {
        response = await this.send(path, options)
      } else {
        tokenStore.clear()
        this.onSessionLost?.()
      }
    }

    if (!response.ok) {
      throw await ApiError.fromResponse(response)
    }
    if (response.status === 204) {
      return undefined as T
    }
    return (await response.json()) as T
  }

  get<T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>): Promise<T> {
    return this.request<T>(path, { ...options, method: 'GET' })
  }

  post<T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method'>): Promise<T> {
    return this.request<T>(path, { ...options, method: 'POST', body })
  }

  patch<T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method'>): Promise<T> {
    return this.request<T>(path, { ...options, method: 'PATCH', body })
  }

  /** 통째로 바꾸는 자원에. 부분 수정은 PATCH 를 쓴다. */
  put<T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method'>): Promise<T> {
    return this.request<T>(path, { ...options, method: 'PUT', body })
  }

  /**
   * 파일 하나를 multipart 로 올린다.
   *
   * `FormData` 에는 Content-Type 을 **직접 넣지 않는다** — 브라우저가
   * boundary 를 붙여 만들어야 하고, 우리가 덮으면 서버가 못 읽는다.
   */
  async postFile<T>(path: string, file: File, field = 'file'): Promise<T> {
    const form = new FormData()
    form.append(field, file)
    return this.request<T>(path, { method: 'POST', body: form })
  }

  /**
   * 파일과 다른 칸을 **함께** 올린다. 이관 묶음이 그렇다 — 파일 옆에 어느
   * 프로젝트로 넣을지와 사람이 지어 준 짝이 같이 가야 한다.
   *
   * `postFile` 과 나눈 이유: 저쪽은 칸이 하나라는 것이 계약이고, 여기에
   * 선택 칸을 얹으면 부르는 쪽마다 무엇이 필수인지 달라진다. 여기서는
   * `FormData` 를 부르는 쪽이 통째로 만든다.
   */
  postForm<T>(path: string, form: FormData): Promise<T> {
    return this.request<T>(path, { method: 'POST', body: form })
  }

  /** JSON 이 아니라 파일을 받는 GET. ZIP·마크다운 내보내기에 쓴다. */
  async getBlob(path: string): Promise<Blob> {
    let response = await this.send(path, { method: 'GET' })
    if (response.status === 401) {
      const refreshed = await this.refreshOnce()
      if (refreshed) response = await this.send(path, { method: 'GET' })
      else {
        tokenStore.clear()
        this.onSessionLost?.()
      }
    }
    if (!response.ok) throw await ApiError.fromResponse(response)
    return response.blob()
  }

  /**
   * JSON 이 아니라 파일을 받는 POST. CSV 내보내기처럼 스트리밍 응답에 쓴다.
   *
   * 401 리프레시는 request() 와 같은 경로를 타야 하므로 send() 를 재사용한다.
   */
  async postForBlob(path: string, body?: unknown): Promise<Blob> {
    let response = await this.send(path, { method: 'POST', body })
    if (response.status === 401) {
      const refreshed = await this.refreshOnce()
      if (refreshed) response = await this.send(path, { method: 'POST', body })
      else {
        tokenStore.clear()
        this.onSessionLost?.()
      }
    }
    if (!response.ok) throw await ApiError.fromResponse(response)
    return response.blob()
  }

  /**
   * 본문을 받는다. 드문 일이지만 구독 해제(`DELETE /watches`)처럼 대상을
   * 본문으로 받는 엔드포인트가 있다 — 시작과 해제가 같은 모양이어야 한다.
   */
  delete<T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method'>): Promise<T> {
    return this.request<T>(path, { ...options, method: 'DELETE', body })
  }

  private async send(path: string, options: RequestOptions): Promise<Response> {
    // FormData 는 브라우저가 boundary 를 붙여 직렬화한다. 우리가
    // Content-Type 을 넣거나 JSON 으로 바꾸면 서버가 못 읽는다.
    const isForm = options.body instanceof FormData

    const headers: Record<string, string> = { Accept: 'application/json' }
    if (options.body !== undefined && !isForm) headers['Content-Type'] = 'application/json'

    const token = tokenStore.access
    if (token && !options.anonymous) headers['Authorization'] = `Bearer ${token}`
    Object.assign(headers, options.headers)

    const init: RequestInit = {
      method: options.method ?? 'GET',
      headers,
      credentials: 'include',
    }
    if (options.body !== undefined) {
      init.body = isForm ? (options.body as FormData) : JSON.stringify(options.body)
    }
    if (options.signal) init.signal = options.signal

    return fetch(`${this.baseUrl}${path}`, init)
  }

  /** 동시 401 이 몰려도 리프레시는 한 번만 나가게 묶는다. */
  private refreshOnce(): Promise<boolean> {
    this.refreshing ??= this.doRefresh().finally(() => {
      this.refreshing = null
    })
    return this.refreshing
  }

  private async doRefresh(): Promise<boolean> {
    const refreshToken = tokenStore.refresh
    if (!refreshToken) return false

    const response = await this.send('/api/v1/auth/refresh', {
      method: 'POST',
      body: { refresh_token: refreshToken },
      anonymous: true,
    })
    if (!response.ok) return false

    const tokens = (await response.json()) as {
      access_token: string
      refresh_token: string
    }
    tokenStore.set({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token })
    return true
  }
}
