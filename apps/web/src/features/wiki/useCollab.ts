/**
 * 동시 편집을 화면에 잇는 훅 (B16).
 *
 * 화면이 신경 쓸 것을 셋으로 줄인다: 지금 본문, 같이 보는 사람들, 붙었는가.
 *
 * ## 캐럿을 되돌려 놓는 것이 이 훅의 절반이다
 *
 * `<textarea>` 는 controlled 다. 남의 편집이 들어와 값이 바뀌면 브라우저는
 * 캐럿을 **끝으로** 보낸다. 위에서 누가 한 줄 쓸 때마다 내 커서가 문서 끝으로
 * 튀는 셈이고, 그건 같이 쓰는 자리를 가장 빨리 포기하게 되는 증상이다.
 *
 * 그래서 원격 편집이 들어오면 그 편집만큼 내 선택 구간을 옮겨 다시 놓는다
 * (`shiftRange`). 계산은 `ytext.ts` 의 순수 함수가 하고, 여기서는 **언제**
 * 되돌려 놓는지만 정한다.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { wikiApi } from '@/shared/api'

import { CollabSession, type CollabStatus, type Peer } from './collab'
import { shiftRange, singleEdit } from './ytext'

export interface Collab {
  status: CollabStatus
  peers: Peer[]
  /** 지금 본문. 붙기 전에는 `null` — 화면은 그동안 자기 상태를 쓴다. */
  text: string | null
  /** 사람이 고친 결과를 넣는다. 차이만 CRDT 편집으로 바뀐다. */
  write: (next: string) => void
  /** 소스 모드 textarea 를 물려 준다. 캐럿을 되돌려 놓는 데 쓴다. */
  bindSource: (element: HTMLTextAreaElement | null) => void
  announceCaret: (caret: number) => void
}

/**
 * 붙는 순간 무엇을 본문으로 삼을지 화면이 답한다.
 *
 * **이것이 없으면 사람이 쓴 글이 사라진다.** 붙기 전에는 화면이 자기 상태를
 * 본문으로 쓰고, 붙으면 공유 문서가 임자가 된다. 그 갈아타기에서 붙는 사이에
 * 친 글을 공유에 싣지 않으면 그냥 버려진다 — 느린 연결에서 문서를 열고 바로
 * 쓰면 그렇게 된다.
 */
export interface CollabHandoff {
  /**
   * 받은 공유 문서를 그대로 쓰려면 `null`, 내가 들고 있던 글을 실어야 하면
   * 그 글을 돌려준다. 무엇이 맞는지는 화면이 안다(`PageDetail`).
   */
  onAdopt: (shared: string) => string | null
}

export function useCollab(
  pageId: string,
  me: { userId: string; name: string } | null,
  enabled: boolean,
  handoff?: CollabHandoff,
): Collab {
  const [status, setStatus] = useState<CollabStatus>('connecting')
  const [peers, setPeers] = useState<Peer[]>([])
  const [text, setText] = useState<string | null>(null)
  const session = useRef<CollabSession | null>(null)
  const source = useRef<HTMLTextAreaElement | null>(null)
  //: 마지막으로 본 본문. 원격 편집이 무엇이었는지 알아야 캐럿을 옮길 수 있다.
  const seen = useRef('')
  /**
   * 갈아타기 판단을 **최신 클로저로** 들고 있는다.
   *
   * 소켓 effect 는 한 번만 돌지만 갈아타기는 그보다 훨씬 뒤에 일어난다. 인자를
   * 그대로 쓰면 첫 렌더의 클로저가 잡혀, 그 사이에 사람이 친 글을 못 본다.
   */
  const adopt = useRef<CollabHandoff | undefined>(handoff)
  useEffect(() => {
    adopt.current = handoff
  })

  useEffect(() => {
    if (!enabled || me === null) return

    const live = new CollabSession(
      () => wikiApi.pages.collabTicket(pageId),
      me,
      {
        onText: (next, remote) => {
          if (remote) _restoreCaret(source.current, seen.current, next)
          seen.current = next
          setText(next)
        },
        onSynced: (next) => {
          // **여기서 처음으로 공유 문서가 본문의 임자가 된다.** 빈 문서라도
          // 받았으면 받은 것이므로, 이 신호로 갈아탄다.
          seen.current = next
          setText(next)
          // 붙는 사이에 사람이 쓴 글이 있으면 그것을 공유에 싣는다. 안 싣고
          // 갈아타면 그 글은 그냥 사라진다 (`CollabHandoff` 주석).
          const mine = adopt.current?.onAdopt(next) ?? null
          if (mine === null || mine === next) return
          const edit = singleEdit(next, mine)
          if (edit === null) return
          live.edit(edit.at, edit.remove, edit.insert)
        },
        onPeers: setPeers,
        onStatus: setStatus,
      },
    )
    session.current = live
    live.connect()
    return () => {
      live.close()
      session.current = null
      setText(null)
      setPeers([])
    }
    // `me` 는 매 렌더 새 객체다. 의존에 통째로 넣으면 렌더마다 소켓을 끊고
    // 다시 붙는데, 표는 한 번만 쓰는 것이라 그때마다 새 표를 받는다 — 그리고
    // 그 사이의 편집이 사라진다. 실제로 달라졌는지는 두 칸으로 판단한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageId, enabled, me?.userId, me?.name])

  const write = useCallback((next: string) => {
    const live = session.current
    if (live === null) return
    const edit = singleEdit(seen.current, next)
    if (edit === null) return
    live.edit(edit.at, edit.remove, edit.insert)
  }, [])

  const announceCaret = useCallback((caret: number) => {
    session.current?.announceCaret(caret)
  }, [])

  const bindSource = useCallback((element: HTMLTextAreaElement | null) => {
    source.current = element
  }, [])

  return { status, peers, text, write, bindSource, announceCaret }
}

/**
 * 원격 편집 뒤 캐럿을 제자리에 놓는다.
 *
 * **값을 바꾸기 전에** 읽어 두고, 바뀐 뒤 다음 프레임에 다시 놓는다. React 가
 * 값을 반영하기 전에 놓으면 그 값과 함께 지워진다.
 */
function _restoreCaret(
  element: HTMLTextAreaElement | null,
  before: string,
  after: string,
): void {
  if (element === null || document.activeElement !== element) return
  const edit = singleEdit(before, after)
  if (edit === null) return
  const moved = shiftRange({ start: element.selectionStart, end: element.selectionEnd }, edit)
  requestAnimationFrame(() => {
    // 그 사이 사람이 다른 곳을 눌렀으면 건드리지 않는다.
    if (document.activeElement !== element) return
    element.setSelectionRange(moved.start, moved.end)
  })
}
