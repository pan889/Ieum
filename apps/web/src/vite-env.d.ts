/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 비어 있으면 same-origin. 개발은 vite 프록시가 /api 를 백엔드로 넘긴다. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.json' {
  const value: Record<string, string>
  export default value
}
