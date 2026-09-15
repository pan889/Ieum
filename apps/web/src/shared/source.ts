/**
 * 이 판의 **소스 코드가 있는 곳.**
 *
 * 장식이 아니라 라이선스 의무다. AGPL-3.0 13조는, 이 프로그램을 고쳐서
 * 네트워크로 서비스하는 사람에게 **그 화면을 쓰는 모든 사용자에게 소스를
 * 받을 길을 눈에 띄게 제안하라**고 요구한다. 그래서 화면 안에 링크가 있어야
 * 하고, 고쳐서 돌리는 사람은 **자기 소스**를 가리켜야 한다.
 *
 * 그 값이 여기서 온다. 안 고치고 그대로 쓰는 사람은 기본값(우리 저장소)이
 * 맞다 — 고친 것이 없으니 우리 소스가 곧 그 판의 소스다. 고친 사람은 어차피
 * 웹 이미지를 다시 굽게 되므로 그때 이 값을 준다:
 *
 *   VITE_SOURCE_URL=https://git.example.com/our-ieum pnpm --filter @ieum/web build
 */
const FALLBACK = 'https://github.com/pan889/Ieum'

/** 빈 문자열이나 공백만 준 경우도 기본값으로 돌린다 — 링크가 죽으면 의무를 못 지킨다. */
export const SOURCE_URL: string = (import.meta.env.VITE_SOURCE_URL ?? '').trim() || FALLBACK
