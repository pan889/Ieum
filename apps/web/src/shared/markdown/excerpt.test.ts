import { describe, expect, it } from 'vitest'

import { firstParagraph } from './directives'

describe('firstParagraph', () => {
  it('첫 문단을 뽑는다', () => {
    expect(firstParagraph('첫 문단이다.\n\n두 번째.')).toBe('첫 문단이다.')
  })

  it('여러 줄짜리 문단은 한 줄로 잇는다', () => {
    expect(firstParagraph('한 줄\n또 한 줄\n\n다음')).toBe('한 줄 또 한 줄')
  })

  it('제목은 건너뛴다', () => {
    // 제목만 뜨는 인용은 원문 링크보다 나을 게 없다.
    expect(firstParagraph('# 제목\n\n본문이다.')).toBe('본문이다.')
  })

  it('front matter 를 문단으로 읽지 않는다', () => {
    expect(firstParagraph('---\ntitle: 문서\n---\n\n본문이다.')).toBe('본문이다.')
  })

  it('코드 펜스를 통째로 건너뛴다', () => {
    expect(firstParagraph('```sh\necho hi\n```\n\n본문이다.')).toBe('본문이다.')
  })

  it('디렉티브 줄을 건너뛴다', () => {
    expect(firstParagraph('::toc{depth=2}\n\n본문이다.')).toBe('본문이다.')
  })

  it('본문이 없으면 빈 문자열', () => {
    expect(firstParagraph('# 제목만 있다')).toBe('')
    expect(firstParagraph('')).toBe('')
  })
})
