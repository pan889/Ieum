/**
 * 앱이 **디스크의 카탈로그를 다 싣는지** 본다.
 *
 * `tools/i18n-check.mjs` 는 카탈로그 디렉터리를 읽어 검사하므로, 앱이 싣지
 * 않는 네임스페이스도 "깨끗하다" 고 말해 준다. 실제로 `ns` 목록에서
 * `admin`·`desk` 두 개가 빠져 있었고 게이트는 통과했다 — 카탈로그 쪽만
 * 보기 때문이다. 그 사이를 여기서 잇는다.
 */

import { readdirSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { NAMESPACES, i18next, initI18n } from './index'

const CATALOG_DIR = join(import.meta.dirname, '../../../../../packages/i18n/en')

function catalogNamespaces(): string[] {
  return readdirSync(CATALOG_DIR)
    .filter((f) => f.endsWith('.json'))
    .map((f) => f.replace(/\.json$/, ''))
    .sort()
}

describe('i18n 네임스페이스', () => {
  it('디스크의 카탈로그를 하나도 빠뜨리지 않는다', () => {
    expect([...NAMESPACES].sort()).toEqual(catalogNamespaces())
  })

  it('탐색이 아무것도 못 찾는 경우를 통과시키지 않는다', () => {
    // 경로가 틀리면 위 시험은 빈 배열 두 개를 비교해 조용히 통과한다.
    expect(catalogNamespaces().length).toBeGreaterThan(5)
  })
})

describe('키 해석', () => {
  /**
   * 카탈로그는 **평평한 키**를 쓴다(`"agent.channel"` 이 글자 그대로 키다).
   * 그런데 i18next 의 기본 `keySeparator` 는 `.` 이므로, 값이 다른 키의
   * 접두사이기도 하면(`agent.channel` 과 `agent.channel.portal`) 해석이
   * 어긋날 수 있다. 상담원 화면이 `agent.channel.${channel}` 로 값을 고르고
   * 라벨로는 `agent.channel` 을 쓰므로 실제로 그 모양이다.
   *
   * 어긋나면 화면에 번역이 아니라 **키가 그대로 찍힌다** — 그리고 i18n
   * 게이트는 두 키가 두 언어에 다 있으니 통과시킨다.
   */
  it('다른 키의 접두사인 평평한 키도 그대로 찾는다', async () => {
    await initI18n('en')
    expect(i18next.t('desk:agent.channel')).toBe('Came in via')
    expect(i18next.t('desk:agent.channel.portal')).toBe('Portal')
    expect(i18next.t('desk:agent.channel.email')).toBe('Email')
    expect(i18next.t('desk:agent.channel.agent')).toBe('Agent entered it')
  })
})
