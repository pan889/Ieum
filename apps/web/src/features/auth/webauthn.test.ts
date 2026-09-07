import { describe, expect, it } from 'vitest'

import { fromBase64Url, toBase64Url } from './webauthn'

describe('base64url', () => {
  it('되돌리면 그대로다', () => {
    const bytes = new Uint8Array([0, 1, 2, 250, 251, 252, 253, 254, 255])
    expect(fromBase64Url(toBase64Url(bytes.buffer))).toEqual(bytes)
  })

  it('일반 base64 의 글자를 쓰지 않는다', () => {
    // 이 바이트열은 표준 base64 에서 `+` 와 `/` 를 만든다. base64url 은 `-`·`_`.
    const bytes = new Uint8Array([0xfb, 0xff, 0xbf])
    const encoded = toBase64Url(bytes.buffer)
    expect(encoded).not.toMatch(/[+/=]/)
    expect(fromBase64Url(encoded)).toEqual(bytes)
  })

  it('패딩이 없어도 읽는다', () => {
    // 길이가 4의 배수가 아닌 값. 서버는 패딩을 떼고 보낸다.
    expect(fromBase64Url('AQ')).toEqual(new Uint8Array([1]))
    expect(fromBase64Url('AQI')).toEqual(new Uint8Array([1, 2]))
    expect(fromBase64Url('AQID')).toEqual(new Uint8Array([1, 2, 3]))
  })

  it('빈 값도 다룬다', () => {
    expect(toBase64Url(new Uint8Array([]).buffer)).toBe('')
    expect(fromBase64Url('')).toEqual(new Uint8Array([]))
  })
})
