/**
 * 소스 링크는 **라이선스 의무**다(AGPL-3.0 13조). 그래서 "링크가 죽는" 경우가
 * 그냥 못생긴 것이 아니라 의무 위반이다.
 *
 * 죽는 길이 둘 있고 둘 다 조용하다: 환경변수를 아예 안 준 경우와, 준다고
 * 줬는데 빈 문자열·공백인 경우다. 후자는 배포 스크립트가 값을 못 읽었을 때
 * 실제로 생기는 모양이고, `?? 기본값` 만으로는 안 걸러진다 — 빈 문자열은
 * `null` 이 아니기 때문이다.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

/** 모듈이 **읽는 순간** 값을 정하므로, 환경을 바꾼 뒤 새로 불러야 한다. */
async function load(value?: string): Promise<string> {
  vi.resetModules()
  if (value === undefined) vi.stubEnv('VITE_SOURCE_URL', '')
  else vi.stubEnv('VITE_SOURCE_URL', value)
  const { SOURCE_URL } = await import('./source')
  return SOURCE_URL
}

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('소스 코드 주소', () => {
  it('고쳐서 돌리는 곳이 준 주소를 그대로 쓴다', async () => {
    await expect(load('https://git.example.com/our-ieum')).resolves.toBe(
      'https://git.example.com/our-ieum',
    )
  })

  it('안 주면 이 저장소를 가리킨다', async () => {
    // 안 고치고 그대로 쓰는 곳은 우리 소스가 곧 그 판의 소스다.
    await expect(load(undefined)).resolves.toBe('https://github.com/pan889/Ieum')
  })

  it('빈 값이나 공백을 줘도 죽은 링크를 내보내지 않는다', async () => {
    await expect(load('   ')).resolves.toBe('https://github.com/pan889/Ieum')
  })
})
