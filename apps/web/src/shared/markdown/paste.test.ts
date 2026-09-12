import { describe, expect, it } from 'vitest'

import { attachmentUri, internalLabel, internalUri, looksLikeUrl } from './paste'

/** 이 시험에서 "우리 서버". 창이 없는 곳에서도 돌게 인자로 넘긴다. */
const OURS = 'https://ieum.example.com'

describe('internalUri', () => {
  it('이슈 주소를 스킴으로 바꾼다', () => {
    // 호스트를 그대로 저장하면 주소가 바뀌는 순간 전부 죽는다.
    expect(internalUri(`${OURS}/issues/ENG-1`, OURS)).toBe('issue:ENG-1')
    expect(internalUri('/issues/eng-12', OURS)).toBe('issue:ENG-12')
  })

  it('문서 주소를 스킴으로 바꾼다', () => {
    expect(internalUri(`${OURS}/wiki/ENG/deploy/rollback`, OURS)).toBe('page:ENG/deploy/rollback')
  })

  it('인코딩된 한글 경로를 되돌린다', () => {
    expect(internalUri('/wiki/ENG/%EB%B0%B0%ED%8F%AC', OURS)).toBe('page:ENG/배포')
  })

  it('모양이 아니면 건드리지 않는다', () => {
    expect(internalUri(`${OURS}/issues/ENG-1x`, OURS)).toBeNull()
    expect(internalUri(`${OURS}/docs`, OURS)).toBeNull()
    expect(internalUri('issues/ENG-1', OURS)).toBeNull()
    expect(internalUri('그냥 글', OURS)).toBeNull()
  })

  it('스페이스만 있는 위키 주소는 문서가 아니다', () => {
    expect(internalUri('/wiki/ENG', OURS)).toBeNull()
  })

  describe('남의 서버 주소', () => {
    /*
      **경로만 보면 남의 트래커가 우리 것이 된다.**

      파트너의 레드마인 링크를 붙여넣으면 경로가 `/issues/ENG-1` 이라 우리
      쪽 ENG-1 을 가리키는 `issue:ENG-1` 로 저장된다. 링크는 멀쩡해 보이고,
      원래 주소는 이미 없다 — 눌러 보기 전에는 아무도 모른다.
    */
    it('다른 호스트면 그대로 둔다', () => {
      expect(internalUri('https://redmine.partner.example/issues/ENG-1', OURS)).toBeNull()
      expect(internalUri('https://redmine.partner.example/wiki/ENG/deploy', OURS)).toBeNull()
    })

    it('스킴이나 포트만 달라도 남의 주소다', () => {
      expect(internalUri('http://ieum.example.com/issues/ENG-1', OURS)).toBeNull()
      expect(internalUri('https://ieum.example.com:8443/issues/ENG-1', OURS)).toBeNull()
    })

    it('우리 호스트를 흉내낸 이름도 남의 주소다', () => {
      expect(internalUri('https://ieum.example.com.evil.test/issues/ENG-1', OURS)).toBeNull()
      expect(internalUri('https://notieum.example.com/issues/ENG-1', OURS)).toBeNull()
    })
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

  /*
    이 글자는 **마크다운 링크 목적지 안에** 들어간다. 공백이나 괄호가 그대로
    있으면 목적지가 그 자리에서 끝나고, 나머지는 본문의 글자로 남는다 —
    `[그림](attachment:abc/보고서 최종.png)` 은 `…/보고서` 까지만 링크다.
  */
  it('공백은 링크를 끊는다 — 감싼다', () => {
    expect(attachmentUri('abc', '보고서 최종.png')).toBe('attachment:abc/보고서%20최종.png')
  })

  it('괄호와 꺾쇠도 감싼다', () => {
    expect(attachmentUri('abc', 'shot (2).png')).toBe('attachment:abc/shot%20%282%29.png')
    expect(attachmentUri('abc', '<서식>.png')).toBe('attachment:abc/%3C서식%3E.png')
  })

  it('한글은 그대로 둔다 — 원문을 사람이 읽어야 한다', () => {
    expect(attachmentUri('abc', '배포도.png')).toBe('attachment:abc/배포도.png')
  })

  it('이름에 있던 퍼센트를 먼저 감싼다', () => {
    // 안 그러면 되돌릴 때 `%20` 이 공백이 되어 **다른 파일**을 가리킨다.
    expect(attachmentUri('abc', '할인%20.png')).toBe('attachment:abc/할인%2520.png')
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
