/**
 * 메일 채널 폼의 저장 가드 (feature-map C6).
 *
 * 서버가 거절하는 것을 여기서 먼저 막는다 — 저장을 눌러 거절당하고 나서
 * 여섯 칸 중 어디가 문제인지 찾게 두지 않는다.
 *
 * **고칠 때는 비밀번호를 비워 둘 수 있다.** 서버가 값을 안 주므로 폼은 그
 * 칸을 비워 두고 저장하고, 서버는 안 보낸 것을 "그대로 둔다" 로 읽는다.
 * 그 규칙이 여기서도 같아야 한다 — 안 그러면 이름 하나 고치려고 비밀번호를
 * 다시 적어야 한다.
 */

import { describe, expect, it } from 'vitest'

import { channelReady } from './EmailChannelsScreen'

const GOOD = {
  address: 'help@ours.example',
  outboundFrom: 'help@ours.example',
  host: 'imap.example',
  user: 'help',
  port: '993',
  folder: 'INBOX',
  useSsl: true,
  password: 'secret',
  portalId: 'p1',
  requestTypeId: 'r1',
}

describe('channelReady', () => {
  it('다 채우면 저장할 수 있다', () => {
    expect(channelReady(GOOD, { editing: false })).toBe(true)
  })

  it.each([
    ['address', ''],
    ['outboundFrom', ''],
    ['host', ''],
    ['user', ''],
    ['requestTypeId', ''],
  ] as const)('%s 가 비면 막는다', (key, value) => {
    expect(channelReady({ ...GOOD, [key]: value }, { editing: false })).toBe(false)
  })

  it('공백만 적은 것은 채운 것이 아니다', () => {
    expect(channelReady({ ...GOOD, host: '   ' }, { editing: false })).toBe(false)
  })

  it.each(['0', '65536', '', 'abc', '99.5'])('포트가 %s 면 막는다', (port) => {
    expect(channelReady({ ...GOOD, port }, { editing: false })).toBe(false)
  })

  it('만들 때는 비밀번호가 있어야 한다', () => {
    expect(channelReady({ ...GOOD, password: '' }, { editing: false })).toBe(false)
  })

  it('고칠 때는 비밀번호를 비워 둘 수 있다', () => {
    // **이 시험이 이 파일의 이유다.** 서버가 값을 안 주므로 폼은 비워 두고
    // 저장한다 — 여기서 막으면 이름 하나 고치려고 비밀번호를 다시 적어야 한다.
    expect(channelReady({ ...GOOD, password: '' }, { editing: true })).toBe(true)
  })

  it('포털을 안 골라도 요청 유형만 있으면 된다', () => {
    // 고치기로 열면 포털은 비어 있고 요청 유형만 서버에서 온다.
    expect(channelReady({ ...GOOD, portalId: '' }, { editing: true })).toBe(true)
  })
})
