/**
 * 복제된 탭이 **원본과 같은 리프레시 토큰을 쓰지 않는가.**
 *
 * "탭 복제" 는 sessionStorage 를 베낀다. 두 탭이 같은 토큰으로 리프레시하면
 * 서버는 두 번째를 재사용(탈취)으로 보고 세션 계열을 폐기한다 — 탭을 하나
 * 복제했을 뿐인데 둘 다 로그아웃된다. 여기서는 탭 둘을 손으로 만든다: 각자의
 * sessionStorage(복제면 베낀 것)와, 같은 이름의 채널로 이어진 끝 하나씩.
 */

import { claimTab, type TabDeps } from '@ieum/api-client'
import { describe, expect, it } from 'vitest'

// 저장 키는 이미 사람들의 브라우저에 들어 있는 값이다. 바꾸면 모두가 로그아웃
// 되므로 시험이 글자 그대로 붙잡는다.
const REFRESH = 'ieum.refresh'
const TAB = 'ieum.tab'

class MemoryStorage {
  private readonly items = new Map<string, string>()
  constructor(from?: MemoryStorage) {
    if (from) for (const [key, value] of from.items) this.items.set(key, value)
  }
  getItem(key: string) {
    return this.items.get(key) ?? null
  }
  setItem(key: string, value: string) {
    this.items.set(key, value)
  }
  removeItem(key: string) {
    this.items.delete(key)
  }
}

/** 같은 이름의 `BroadcastChannel` 들. 보낸 쪽에는 안 돌아오고, 비동기로 닿는다. */
class Hub {
  private readonly ends: TabDeps['channel'][] = []
  open(): TabDeps['channel'] {
    const hub = this.ends
    const end: TabDeps['channel'] = {
      onmessage: null,
      postMessage(message) {
        for (const other of hub) {
          if (other === end) continue
          queueMicrotask(() => {
            other.onmessage?.(new MessageEvent('message', { data: structuredClone(message) }))
          })
        }
      },
    }
    hub.push(end)
    return end
  }
}

let counter = 0
const newId = () => `tab-${String((counter += 1))}`

function tab(hub: Hub, storage: MemoryStorage) {
  return claimTab({ storage, channel: hub.open(), newId, waitMs: 20 })
}

/** 로그인해서 토큰을 받은 탭 하나. */
async function loggedInTab(hub: Hub) {
  const storage = new MemoryStorage()
  expect(await tab(hub, storage)).toBe('fresh')
  storage.setItem(REFRESH, 'refresh-1')
  return storage
}

describe('claimTab', () => {
  it('새로 연 탭은 이름을 새로 정한다', async () => {
    const storage = new MemoryStorage()
    expect(await tab(new Hub(), storage)).toBe('fresh')
    expect(storage.getItem(TAB)).not.toBeNull()
  })

  it('새로고침한 탭은 토큰을 그대로 쓴다 — 대답할 앞 문서가 없다', async () => {
    const before = new Hub()
    const storage = await loggedInTab(before)
    const name = storage.getItem(TAB)

    // 새로고침: 같은 sessionStorage, 앞 문서는 죽었다(같은 허브에 아무도 없다).
    expect(await tab(new Hub(), storage)).toBe('kept')

    expect(storage.getItem(REFRESH)).toBe('refresh-1')
    expect(storage.getItem(TAB)).toBe(name)
  })

  it('복제된 탭은 물려받은 토큰을 버리고 새 이름을 쓴다', async () => {
    const hub = new Hub()
    const original = await loggedInTab(hub)

    const copy = new MemoryStorage(original)
    expect(await tab(hub, copy)).toBe('duplicate')

    expect(copy.getItem(REFRESH)).toBeNull()
    expect(copy.getItem(TAB)).not.toBe(original.getItem(TAB))
    // 원본은 아무 일도 겪지 않는다.
    expect(original.getItem(REFRESH)).toBe('refresh-1')
  })

  it('복제본도 원본이 된다 — 그것을 다시 복제하면 그것이 대답한다', async () => {
    const hub = new Hub()
    const original = await loggedInTab(hub)
    const copy = new MemoryStorage(original)
    await tab(hub, copy)
    copy.setItem(REFRESH, 'refresh-of-copy')

    const copyOfCopy = new MemoryStorage(copy)
    expect(await tab(hub, copyOfCopy)).toBe('duplicate')
    expect(copyOfCopy.getItem(REFRESH)).toBeNull()
    expect(copy.getItem(REFRESH)).toBe('refresh-of-copy')
  })

  it('기다리는 사이에 이 탭에서 받은 토큰은 버리지 않는다', async () => {
    const hub = new Hub()
    const original = await loggedInTab(hub)
    const copy = new MemoryStorage(original)

    const settled = tab(hub, copy)
    // 원본의 대답이 닿기 전에 이 탭에서 로그인했다.
    copy.setItem(REFRESH, 'refresh-of-copy')

    expect(await settled).toBe('duplicate')
    expect(copy.getItem(REFRESH)).toBe('refresh-of-copy')
  })
})
