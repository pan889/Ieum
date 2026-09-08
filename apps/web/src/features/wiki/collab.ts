/**
 * 동시 편집 클라이언트 — Yjs 문서를 소켓에 잇는다 (B16).
 *
 * ## `y-websocket` 을 쓰지 않은 이유
 *
 * 표(ticket)는 **한 번만 쓴다**(서버 `collab_router.py`). `y-websocket` 의
 * 프로바이더는 끊기면 같은 URL·같은 파라미터로 스스로 다시 붙는데, 그 표는
 * 이미 쓴 표다 — 서버가 1008 로 끊고, 프로바이더는 다시 같은 표로 붙고,
 * 그것이 영원히 반복된다. **끊기면 표를 새로 받아야 한다**는 것이 우리 모양
 * 이므로 붙는 부분은 직접 쓴다. 프로토콜 자체는 `y-protocols` 가 하므로
 * 직접 쓰는 것은 "언제 어떻게 붙나" 뿐이다.
 *
 * ## 서버는 프레즌스의 뜻을 모른다
 *
 * 커서와 사람 정보는 awareness 로 오가고 서버는 그대로 중계한다. 그래서
 * 여기에 무엇을 담을지는 화면이 혼자 정할 수 있다 — 색깔 하나 바꾸는 데
 * 서버 배포가 필요하지 않다.
 */

import { Awareness, applyAwarenessUpdate, encodeAwarenessUpdate } from 'y-protocols/awareness'
import {
  messageYjsSyncStep2,
  readSyncMessage,
  writeSyncStep1,
  writeUpdate,
} from 'y-protocols/sync'
import * as decoding from 'lib0/decoding'
import * as encoding from 'lib0/encoding'
import * as Y from 'yjs'

/**
 * API 의 오리진. REST 클라이언트가 쓰는 것과 **같은 값**이다 — 두 벌로 두면
 * 한쪽만 바뀌는 날이 오고, 그날 소켓만 조용히 안 붙는다.
 */
const API_BASE: string = import.meta.env.VITE_API_BASE_URL ?? ''

/** 서버와 같은 이름. 다르면 두 쪽이 서로 다른 칸을 본다. */
export const BODY_KEY = 'body'

const MESSAGE_SYNC = 0
const MESSAGE_AWARENESS = 1

/** 다시 붙기까지 기다리는 시간. 끊길 때마다 늘리고 붙으면 되돌린다. */
const RETRY_MIN_MS = 500
const RETRY_MAX_MS = 10_000

export type CollabStatus = 'connecting' | 'live' | 'offline'

export interface Peer {
  /** awareness 의 client id. 같은 사람이 창을 둘 열면 둘이다. */
  clientId: number
  userId: string
  name: string
  /** 지금 커서가 있는 글자 위치. 아직 안 보냈으면 `null`. */
  caret: number | null
}

export interface CollabHandlers {
  /** 본문이 바뀌었다(내 편집이든 남의 편집이든). */
  onText: (text: string, remote: boolean) => void
  /**
   * 공유 문서를 **다 받았다.** 이제부터 이 문서가 본문의 임자다.
   *
   * `onText` 로는 이 순간을 알 수 없다: 빈 문서는 받아도 바뀐 것이 없어서
   * 업데이트 이벤트가 아예 안 난다. 그것을 "아직 안 붙었다" 로 읽으면 빈
   * 문서에서는 **영원히** 안 붙은 것이 되고, 화면은 조용히 혼자 편집한다 —
   * 남의 화면에는 아무것도 안 뜨고, 에러도 없다. 실제로 그렇게 막혔다.
   */
  onSynced: (text: string) => void
  onPeers: (peers: Peer[]) => void
  onStatus: (status: CollabStatus) => void
}

export interface Me {
  userId: string
  name: string
}

/** 표를 받아 오는 함수. 시험이 갈아끼울 수 있게 밖에서 받는다. */
export type TicketSource = () => Promise<{ url: string }>

export class CollabSession {
  readonly doc = new Y.Doc()
  readonly text: Y.Text
  private readonly awareness: Awareness
  private socket: WebSocket | null = null
  private retry = RETRY_MIN_MS
  /** 공유 문서를 다 받았나. 받기 전에는 `live` 라고 말하지 않는다. */
  private synced = false
  private timer: ReturnType<typeof setTimeout> | null = null
  private closed = false

  constructor(
    private readonly ticket: TicketSource,
    me: Me,
    private readonly handlers: CollabHandlers,
  ) {
    this.text = this.doc.getText(BODY_KEY)
    this.awareness = new Awareness(this.doc)
    this.awareness.setLocalStateField('user', { id: me.userId, name: me.name })

    // 문서가 바뀌면 화면에 알리고, **내가 만든 변경만** 서버로 보낸다.
    // 서버에서 받은 것을 되돌려 보내면 둘이 영원히 주고받는다.
    this.doc.on('update', (update: Uint8Array, origin: unknown) => {
      this.handlers.onText(_textOf(this.text), origin === 'remote')
      if (origin !== 'remote') this.sendSync((out) => { writeUpdate(out, update) })
    })
    this.awareness.on('change', () => { this.handlers.onPeers(this.peers()) })
  }

  /** 붙는다. 표를 받아 소켓을 열고, 끊기면 **새 표로** 다시 붙는다. */
  connect(): void {
    if (this.closed) return
    this.handlers.onStatus('connecting')
    void this.ticket()
      .then(({ url }) => {
        if (this.closed) return
        const socket = new WebSocket(
          _absolute(url, API_BASE, window.location.origin),
        )
        socket.binaryType = 'arraybuffer'
        this.socket = socket
        socket.onopen = () => {
          this.retry = RETRY_MIN_MS
          // **아직 `live` 가 아니다.** 소켓이 열린 것과 문서를 받은 것은
          // 다르고, 그 사이에 친 글자는 공유 문서에 안 들어간다.
          // 내 상태 벡터를 보내 서로 모르는 것만 주고받게 한다.
          this.sendSync((out) => { writeSyncStep1(out, this.doc) })
          this.sendAwareness([this.doc.clientID])
        }
        socket.onmessage = (event: MessageEvent<ArrayBuffer>) => {
          this.receive(new Uint8Array(event.data))
        }
        socket.onclose = () => { this.fell() }
        socket.onerror = () => { socket.close() }
      })
      .catch(() => { this.fell() })
  }

  /** 커서 위치를 알린다. 서버는 이 값의 뜻을 모르고 중계만 한다. */
  announceCaret(caret: number): void {
    this.awareness.setLocalStateField('caret', caret)
    this.sendAwareness([this.doc.clientID])
  }

  /** 본문을 이 문자열로 만든다. **한 번의 편집으로** 넣는다. */
  edit(at: number, remove: number, insert: string): void {
    this.doc.transact(() => {
      if (remove > 0) this.text.delete(at, remove)
      if (insert) this.text.insert(at, insert)
    })
  }

  close(): void {
    this.closed = true
    if (this.timer !== null) clearTimeout(this.timer)
    // 나간다고 알린다. 안 알리면 남의 화면에 내 커서가 30초 더 남는다.
    this.awareness.setLocalState(null)
    this.sendAwareness([this.doc.clientID])
    this.socket?.close()
    this.doc.destroy()
  }

  peers(): Peer[] {
    const found: Peer[] = []
    this.awareness.getStates().forEach((state, clientId) => {
      if (clientId === this.doc.clientID) return
      const user = (state as { user?: { id?: string; name?: string } }).user
      if (user?.id === undefined) return
      const caret = (state as { caret?: number }).caret
      found.push({
        clientId,
        userId: user.id,
        name: user.name ?? user.id,
        caret: typeof caret === 'number' ? caret : null,
      })
    })
    return found
  }

  // ── 안쪽 ──────────────────────────────────────────────────

  private receive(data: Uint8Array): void {
    const reader = decoding.createDecoder(data)
    const kind = decoding.readVarUint(reader)
    if (kind === MESSAGE_SYNC) {
      const out = encoding.createEncoder()
      encoding.writeVarUint(out, MESSAGE_SYNC)
      // **`'remote'` 를 원산지로 준다.** 이 표시가 없으면 방금 서버에서 받은
      // 것을 서버로 되돌려 보낸다.
      const kind2 = readSyncMessage(reader, out, this.doc, 'remote')
      if (encoding.length(out) > 1) this.send(encoding.toUint8Array(out))
      // 상대가 준 step2 를 받은 순간이 "다 받았다" 다.
      if (kind2 === messageYjsSyncStep2 && !this.synced) {
        this.synced = true
        this.handlers.onStatus('live')
        this.handlers.onSynced(_textOf(this.text))
      }
    } else if (kind === MESSAGE_AWARENESS) {
      applyAwarenessUpdate(this.awareness, decoding.readVarUint8Array(reader), 'remote')
    }
  }

  private sendSync(write: (out: encoding.Encoder) => void): void {
    const out = encoding.createEncoder()
    encoding.writeVarUint(out, MESSAGE_SYNC)
    write(out)
    this.send(encoding.toUint8Array(out))
  }

  private sendAwareness(clients: number[]): void {
    const out = encoding.createEncoder()
    encoding.writeVarUint(out, MESSAGE_AWARENESS)
    encoding.writeVarUint8Array(out, encodeAwarenessUpdate(this.awareness, clients))
    this.send(encoding.toUint8Array(out))
  }

  private send(message: Uint8Array): void {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(message)
  }

  private fell(): void {
    this.socket = null
    if (this.closed) return
    this.synced = false
    this.handlers.onStatus('offline')
    // **표를 새로 받아** 다시 붙는다. 같은 표로는 두 번 못 붙는다.
    this.timer = setTimeout(() => { this.connect() }, this.retry)
    this.retry = Math.min(this.retry * 2, RETRY_MAX_MS)
  }
}

/**
 * `Y.Text` 의 지금 값.
 *
 * `String(text)` 로 쓰면 eslint 가 "Object 의 기본 문자열화" 로 보고 막는다 —
 * 실제로는 Yjs 가 제대로 구현한 것이라 문제가 없지만, 그 사실을 한 자리에
 * 적어 두는 쪽이 곳곳에 예외 주석을 뿌리는 것보다 낫다.
 */
function _textOf(text: Y.Text): string {
  return text.toJSON()
}

/**
 * 서버가 준 경로를 소켓 주소로.
 *
 * **화면의 오리진이 아니라 API 의 오리진으로 붙는다.** 처음에
 * `window.location.host` 로 짰다가 개발 스택에서 통째로 막혔다: REST 는
 * 브라우저가 API(`:8000`)로 **직접** 가는데(같은 `VITE_API_BASE_URL`),
 * 소켓만 화면 서버(`:5173`)로 갔다. 화면 서버의 프록시 대상은 컨테이너
 * 안에서 `localhost:8000` 이라 자기 자신을 가리켰고, 로그에는
 * `ws proxy error: ECONNREFUSED 127.0.0.1:8000` 이 찍혔다 — 그런데 REST 는
 * 멀쩡하니 화면만 보면 "소켓만 안 붙는다" 로 보인다.
 *
 * 규칙은 하나로 줄인다: **소켓은 REST 가 가는 곳으로 간다.** 운영에서는
 * 앞단이 같은 오리진이라 이 값이 비어 있고, 그때는 화면의 오리진이 곧
 * API 의 오리진이다.
 */
export function _absolute(path: string, apiBase: string, pageOrigin: string): string {
  const base = apiBase === '' ? pageOrigin : apiBase
  return base.replace(/^http/, 'ws').replace(/\/$/, '') + path
}
