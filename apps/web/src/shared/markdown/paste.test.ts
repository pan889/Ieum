import { describe, expect, it } from 'vitest'

import { attachmentUri, internalLabel, internalUri, looksLikeUrl } from './paste'

describe('internalUri', () => {
  it('이슈 주소를 스킴으로 바꾼다', () => {
    // 호스트를 그대로 저장하면 주소가 바뀌는 순간 전부 죽는다.
    expect(internalUri('http://localhost:5173/issues/ENG-1')).toBe('issue:ENG-1')
    expect(internalUri('/issues/eng-12')).toBe('issue:ENG-12')
  })

  it('문서 주소를 스킴으로 바꾼다', () => {
    expect(internalUri('https://ieum.example.com/wiki/ENG/deploy/rollback')).toBe(
      'page:ENG/deploy/rollback',
    )
  })

  it('인코딩된 한글 경로를 되돌린다', () => {
    expect(internalUri('/wiki/ENG/%EB%B0%B0%ED%8F%AC')).toBe('page:ENG/배포')
  })

  it('우리 주소가 아니면 건드리지 않는다', () => {
    expect(internalUri('https://example.com/issues/ENG-1x')).toBeNull()
    expect(internalUri('https://example.com/docs')).toBeNull()
    expect(internalUri('issues/ENG-1')).toBeNull()
    expect(internalUri('그냥 글')).toBeNull()
  })

  it('스페이스만 있는 위키 주소는 문서가 아니다', () => {
    expect(internalUri('/wiki/ENG')).toBeNull()
  })
})

describe('looksLikeUrl', () => {
  it('공백이 있으면 주소가 아니다', () => {
    expect(looksLikeUrl('https://example.com')).toBe(true)
    expect(looksLikeUrl('/issues/ENG-1')).toBe(true)
    expect(looksLikeUrl('https://example.com 과 글')).toBe(false)
    expect(looksLikeUrl('')).toBe(false)
  })
})

describe('attachmentUri', () => {
  it('id 와 파일 이름을 함께 담는다', () => {
    // 이름이 있어야 내보낸 `.md` 에서도 무엇인지 알 수 있다.
    expect(attachmentUri('abc', 'a.png')).toBe('attachment:abc/a.png')
  })
})

describe('internalLabel', () => {
  it('스킴을 벗겨 사람이 읽는 이름으로', () => {
    expect(internalLabel('issue:DEV-1')).toBe('DEV-1')
    expect(internalLabel('page:ENG/deploy')).toBe('ENG/deploy')
  })

  it('밖의 주소는 그대로 둔다', () => {
    expect(internalLabel('https://example.com')).toBe('https://example.com')
  })
})
