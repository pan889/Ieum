import { describe, expect, it } from 'vitest'

import { attachmentIds, withAttachmentUrls } from './attachments'

const ID = '01a079aa-40fb-7dd5-bd36-0d3d5c5faee9'

describe('attachmentIds', () => {
  it('본문이 가리키는 첨부를 찾는다', () => {
    expect(attachmentIds(`<img src="attachment:${ID}/a.png">`)).toEqual([ID])
  })

  it('같은 첨부는 한 번만', () => {
    const html = `<img src="attachment:${ID}/a.png"><a href="attachment:${ID}/a.png">a</a>`
    expect(attachmentIds(html)).toEqual([ID])
  })

  it('첨부가 없으면 빈 목록', () => {
    // 대부분의 문서가 여기다. 요청이 한 건도 안 나가야 한다.
    expect(attachmentIds('<p>그냥 글</p>')).toEqual([])
  })

  it('UUID 가 아니면 안 센다', () => {
    expect(attachmentIds('<img src="attachment:not-a-uuid/a.png">')).toEqual([])
  })
})

describe('withAttachmentUrls', () => {
  it('받은 주소로 갈아 끼운다', () => {
    const html = `<img src="attachment:${ID}/a.png">`
    const urls = new Map([[ID, 'https://s3.example/signed?x=1']])
    expect(withAttachmentUrls(html, urls)).toBe('<img src="https://s3.example/signed?x=1">')
  })

  it('못 받은 것은 그대로 둔다', () => {
    // 빈 그림보다 낫다. 무엇을 가리키고 있었는지도 남는다.
    const html = `<img src="attachment:${ID}/a.png">`
    expect(withAttachmentUrls(html, new Map())).toBe(html)
  })

  it('대소문자가 달라도 같은 첨부다', () => {
    const html = `<img src="attachment:${ID.toUpperCase()}/a.png">`
    const urls = new Map([[ID, 'https://s3.example/signed']])
    expect(withAttachmentUrls(html, urls)).toBe('<img src="https://s3.example/signed">')
  })
})
