/**
 * 동시 편집 세션이 **다시 붙을 때 갈아타기를 또 하지 않는가.**
 *
 * `onSynced` 는 "붙기 전의 내 글" 과 "받은 공유 문서" 를 맞추는 자리다. 끊겼다
 * 다시 붙을 때 이것을 또 부르면, 화면은 내 글(이미 공유 문서에 들어간 편집)과
 * 공유 문서가 둘 다 게시본과 다르니 "둘 다 고쳤다" 로 읽고 충돌 경고를 띄웠다
 * — 와이파이가 한 번 흔들릴 때마다.
 */

import * as encoding from 'lib0/encoding'
import { writeSyncStep2 } from 'y-protocols/sync'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Y from 'yjs'

import { BODY_KEY, CollabSession, type CollabHandlers } from './collab'

/** 소켓을 손으로 움직인다. 열고, 서버 말을 넣고, 끊는다. */
class FakeSocket {
  static readonly OPEN = 1
  static opened: FakeSocket[] = []
  readyState = 0
  binaryType = ''
  sent: Uint8Array[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: ArrayBuffer }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(readonly url: string) {
    FakeSocket.opened.push(this)
  }

  send(message: Uint8Array) {
    this.sent.push(message)
  }

  close() {
    this.readyState = 3
    this.onclose?.()
  }

  open() {
    this.readyState = FakeSocket.OPEN
    this.onopen?.()
  }

  /** 서버의 step2 — "네가 모르는 것은 이것이 전부다". 이것이 "다 받았다" 다. */
  receiveStep2(server: Y.Doc) {
    const out = encoding.createEncoder()
    encoding.writeVarUint(out, 0) // MESSAGE_SYNC
    writeSyncStep2(out, server)
    const bytes = encoding.toUint8Array(out)
    const data = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
    this.onmessage?.({ data })
  }
}

function handlers(): CollabHandlers & { [K in keyof CollabHandlers]: ReturnType<typeof vi.fn> } {
  return { onText: vi.fn(), onSynced: vi.fn(), onPeers: vi.fn(), onStatus: vi.fn() }
}

const latest = () => {
  const socket = FakeSocket.opened.at(-1)
  if (socket === undefined) throw new Error('소켓이 안 열렸다')
  return socket
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeSocket.opened = []
  vi.stubGlobal('WebSocket', FakeSocket)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('CollabSession / 다시 붙기', () => {
  it('처음 붙을 때만 갈아탄다 — 다시 붙으면 `live` 라고만 말한다', async () => {
    const server = new Y.Doc()
    server.getText(BODY_KEY).insert(0, '게시본')
    const on = handlers()
    const session = new CollabSession(() => Promise.resolve({ url: '/collab/1' }), {
      userId: 'u1',
      name: 'me',
    }, on)

    session.connect()
    await vi.advanceTimersByTimeAsync(0) // 표를 받는다
    latest().open()
    latest().receiveStep2(server)
    expect(on.onSynced).toHaveBeenCalledTimes(1)
    expect(on.onSynced).toHaveBeenLastCalledWith('게시본')

    // 붙은 뒤에 쓴 글. 이미 공유 문서에 들어 있다.
    session.edit(3, 0, ' + 내 편집')

    // 끊겼다가 새 표로 다시 붙는다.
    latest().close()
    expect(on.onStatus).toHaveBeenLastCalledWith('offline')
    await vi.advanceTimersByTimeAsync(10_000)
    expect(FakeSocket.opened).toHaveLength(2)
    latest().open()
    latest().receiveStep2(server)

    expect(on.onStatus).toHaveBeenLastCalledWith('live')
    // 두 번째 동기화에서 갈아타기를 또 부르면 화면이 거짓 충돌을 띄운다.
    expect(on.onSynced).toHaveBeenCalledTimes(1)
    // 끊긴 사이에도 내 편집은 문서에 그대로 있다 — 다시 실을 것이 없다.
    expect(session.text.toJSON()).toBe('게시본 + 내 편집')

    session.close()
  })

  it('다시 붙기 전에는 `live` 라고 말하지 않는다', async () => {
    const on = handlers()
    const session = new CollabSession(() => Promise.resolve({ url: '/collab/1' }), {
      userId: 'u1',
      name: 'me',
    }, on)

    session.connect()
    await vi.advanceTimersByTimeAsync(0)
    latest().open()
    latest().receiveStep2(new Y.Doc())
    latest().close()
    await vi.advanceTimersByTimeAsync(10_000)
    latest().open()

    // 소켓이 열린 것과 문서를 받은 것은 다르다.
    expect(on.onStatus).toHaveBeenLastCalledWith('connecting')

    session.close()
  })
})
