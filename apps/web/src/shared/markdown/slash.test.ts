import { describe, expect, it } from 'vitest'

import en from '@ieum/i18n/en/common.json'
import ko from '@ieum/i18n/ko/common.json'

import { CONTAINER_NAMES, LEAF_NAMES } from './directives'
import { cycle } from './doc'
import { applySlash, findSlashQuery, matchSlash, slashItems } from './slash'

function byId(id: string) {
  const found = slashItems().find((entry) => entry.id === id)
  if (!found) throw new Error(`${id} 항목이 없다`)
  return found
}

describe('slashItems', () => {
  it('디렉티브를 정의에서 끌어온다', () => {
    const ids = slashItems().map((entry) => entry.id)
    // 손으로 나열하면 새 디렉티브는 목록에 안 뜨고, 없앤 것은 남는다.
    for (const name of [...LEAF_NAMES, ...CONTAINER_NAMES]) expect(ids).toContain(name)
  })

  it('모든 항목에 두 언어의 이름이 있다', () => {
    // 번역이 없으면 목록에 `slash.info` 같은 키가 그대로 보인다.
    for (const entry of slashItems()) {
      // 카탈로그는 점이 들어간 평면 키다. 배열형이라야 경로로 안 읽는다.
      expect(en, `en/${entry.id}`).toHaveProperty([`slash.${entry.id}`])
      expect(ko, `ko/${entry.id}`).toHaveProperty([`slash.${entry.id}`])
    }
  })

  it('넣는 것이 뜻이 있는 마크다운이다', () => {
    // 넣자마자 깨지면 안 된다 — 편집기를 한 바퀴 돌려도 같은 것이어야 한다.
    for (const entry of slashItems()) {
      expect(cycle(entry.insert).trim(), entry.id).not.toBe('')
    }
  })

  it('커서는 조각 안쪽을 가리킨다', () => {
    for (const entry of slashItems()) {
      expect(entry.caret, entry.id).toBeGreaterThanOrEqual(0)
      expect(entry.caret, entry.id).toBeLessThanOrEqual(entry.insert.length)
    }
  })

  it('표는 첫 칸에 커서를 놓는다', () => {
    const table = byId('table')
    expect(table.insert.startsWith('| ')).toBe(true)
    expect(table.caret).toBe(2)
    // 칸막이가 커서 표시로 잘못 읽히면 표가 깨진다.
    expect(table.insert.split('\n')[1]).toBe('| --- | --- |')
  })
})

describe('findSlashQuery', () => {
  it('줄 맨 앞의 슬래시만 명령이다', () => {
    expect(findSlashQuery('/', 1)).toEqual({ start: 0, term: '' })
    expect(findSlashQuery('앞\n/tab', 6)).toEqual({ start: 2, term: 'tab' })
  })

  it('경로나 날짜에는 열리지 않는다', () => {
    // 아무 데서나 열면 `src/shared` 를 칠 때마다 목록이 튀어나온다.
    expect(findSlashQuery('src/shared', 10)).toBeNull()
    expect(findSlashQuery('9/6', 3)).toBeNull()
  })

  it('공백이 오면 명령이 아니다', () => {
    expect(findSlashQuery('/tab le', 7)).toBeNull()
  })

  it('슬래시가 없으면 null', () => {
    expect(findSlashQuery('그냥 글', 4)).toBeNull()
  })
})

describe('matchSlash', () => {
  it('앞에서부터 맞는 것을 먼저 준다', () => {
    // 사람은 목록을 읽지 않고 첫 줄에서 엔터를 친다.
    const found = matchSlash(slashItems(), 'tab')
    expect(found[0]?.id).toBe('table')
  })

  it('한국어로도 찾는다', () => {
    expect(matchSlash(slashItems(), '제목').map((entry) => entry.id)).toContain('heading2')
  })

  it('빈 글자면 전부 준다', () => {
    expect(matchSlash(slashItems(), '')).toHaveLength(slashItems().length)
  })

  it('안 맞으면 빈 목록', () => {
    expect(matchSlash(slashItems(), 'zzzz')).toEqual([])
  })
})

describe('applySlash', () => {
  it('슬래시를 지우고 조각을 넣는다', () => {
    const query = findSlashQuery('/h2', 3)
    if (!query) throw new Error('명령을 못 찾았다')
    const result = applySlash('/h2', query, 3, byId('heading2'))
    expect(result.text).toBe('## ')
    expect(result.caret).toBe(3)
  })

  it('앞에 글이 있으면 빈 줄을 넣는다', () => {
    // 안 넣으면 `## 제목` 이 앞 문단에 이어 붙어 제목이 되지 않는다.
    const text = '앞 문단\n/'
    const query = findSlashQuery(text, text.length)
    if (!query) throw new Error('명령을 못 찾았다')
    const result = applySlash(text, query, text.length, byId('heading2'))
    expect(result.text).toBe('앞 문단\n\n## ')
    expect(result.caret).toBe(result.text.length)
  })

  it('이미 빈 줄이면 더 넣지 않는다', () => {
    const text = '앞 문단\n\n/'
    const query = findSlashQuery(text, text.length)
    if (!query) throw new Error('명령을 못 찾았다')
    expect(applySlash(text, query, text.length, byId('heading2')).text).toBe('앞 문단\n\n## ')
  })

  it('뒤에 있던 글은 남는다', () => {
    const text = '/\n뒤 문단'
    const query = findSlashQuery(text, 1)
    if (!query) throw new Error('명령을 못 찾았다')
    expect(applySlash(text, query, 1, byId('divider')).text).toBe('---\n\n\n뒤 문단')
  })

  it('상자는 커서를 안쪽에 놓는다', () => {
    const query = findSlashQuery('/', 1)
    if (!query) throw new Error('명령을 못 찾았다')
    const result = applySlash('/', query, 1, byId('info'))
    expect(result.text).toBe(':::info\n\n:::')
    expect(result.text.slice(result.caret)).toBe('\n:::')
  })
})
