/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 비어 있으면 same-origin. 개발은 vite 프록시가 /api 를 백엔드로 넘긴다. */
  readonly VITE_API_BASE_URL?: string
  /**
   * 이 판의 소스가 있는 곳. **고쳐서 돌리는 곳은 반드시 준다** — AGPL-3.0
   * 13조가 쓰는 사람에게 소스를 제안하라고 요구하고, 그 소스는 우리 것이
   * 아니라 **그 판의 것**이어야 한다. 비우면 우리 저장소를 가리킨다
   * (`shared/source.ts`).
   */
  readonly VITE_SOURCE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.json' {
  const value: Record<string, string>
  export default value
}
