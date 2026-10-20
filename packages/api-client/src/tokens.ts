/**
 * 토큰 보관.
 *
 * 액세스 토큰은 메모리에만 둔다 — localStorage 에 두면 XSS 한 방에 털린다.
 * 리프레시 토큰은 새로고침을 견뎌야 해서 sessionStorage 를 쓴다. 완벽하진
 * 않지만 탭을 닫으면 사라지고, 서버가 로테이션·재사용 탐지로 받쳐준다.
 * (진짜 해법은 httpOnly 쿠키이고 M1 에서 전환한다 — auth.md 1절)
 */
const REFRESH_KEY = 'ieum.refresh'

export interface TokenPair {
  accessToken: string
  refreshToken: string
}

let accessToken: string | null = null

export const tokenStore = {
  get access(): string | null {
    return accessToken
  },

  get refresh(): string | null {
    try {
      return sessionStorage.getItem(REFRESH_KEY)
    } catch {
      return null
    }
  },

  set({ accessToken: access, refreshToken }: TokenPair): void {
    accessToken = access
    try {
      sessionStorage.setItem(REFRESH_KEY, refreshToken)
    } catch {
      /* 프라이빗 모드 등. 메모리 토큰만으로도 현재 탭은 동작한다. */
    }
  },

  clear(): void {
    accessToken = null
    try {
      sessionStorage.removeItem(REFRESH_KEY)
    } catch {
      /* 무시 */
    }
  },
}

// ── 복제된 탭 ───────────────────────────────────────────────────

/**
 * **복제된 탭은 물려받은 리프레시 토큰을 쓰지 않는다.**
 *
 * 브라우저의 "탭 복제" 는 sessionStorage 를 통째로 베낀다. 그러면 두 탭이 같은
 * 리프레시 토큰을 들고 시작하고, 먼저 리프레시한 쪽이 토큰을 돌린 뒤 나머지가
 * 옛 토큰을 내밀면 서버는 그것을 **재사용(탈취)으로 보고 세션 계열을 통째로
 * 폐기한다** — 두 탭이 함께 로그아웃된다. 서버의 판단은 옳다: 그 둘은 서버가
 * 보기에 구별되지 않는다. 그래서 브라우저 쪽에서 둘을 갈라야 한다.
 *
 * 탭마다 이름(`TAB_KEY`)을 sessionStorage 에 둔다. 새로 연 탭은 이름이 없고,
 * 새로고침한 탭과 복제된 탭은 **물려받은 이름**이 있다. 둘을 가르는 것은 "그
 * 이름을 지금 쓰고 있는 살아 있는 탭이 있는가" 다 — 새로고침이면 앞 문서는
 * 이미 죽어 대답할 이가 없고, 복제면 원본이 대답한다. 그래서 물려받은 이름을
 * 채널에 외치고 잠깐 기다린다. 대답이 오면 복제다: 토큰을 버리고 새 이름을
 * 쓴다. 그 탭은 로그인부터 다시 하고, 원본은 아무 일도 겪지 않는다.
 *
 * `performance.getEntriesByType('navigation')` 의 `type` 으로 가르지 않는다.
 * 복제를 따로 가리키는 값이 없고(브라우저마다 `reload` 나 `back_forward` 로
 * 찍힌다), 같은 값이 새로고침·뒤로 가기에도 나온다.
 *
 * **못 가르면 예전처럼 둔다.** 채널이 없거나, 원본이 얼어 있어(백그라운드에서
 * 오래 지난 탭) 시간 안에 대답하지 못하면 물려받은 토큰을 그대로 쓴다 — 그때
 * 생기는 일은 이 장치가 없던 때와 같다. 거꾸로 새로고침을 복제로 잘못 볼 일은
 * 없다: 앞 문서는 이미 죽었거나(새로고침) 얼어 있어(뒤로 가기 캐시) 대답하지
 * 못한다. 잘못 보면 새로고침마다 로그아웃되므로, 틀릴 거면 이쪽으로 틀린다.
 */
const TAB_KEY = 'ieum.tab'
const TAB_CHANNEL = 'ieum.tabs'

/**
 * 원본의 대답을 기다리는 시간.
 *
 * 같은 브라우저 안의 채널은 몇 밀리초면 닿는다. 기다리는 것은 **첫 리프레시
 * 하나**뿐이고(부팅 때 `me` 가 401 을 받은 뒤), 그 사이 `me` 가 오가는 시간과
 * 겹치므로 실제로 늘어나는 시간은 대개 이보다 짧다.
 */
const TAB_WAIT_MS = 150

type TabMessage =
  | { type: 'claim'; tab: string; from: string }
  | { type: 'taken'; tab: string; to: string }

/** `claimTab` 이 쓰는 것들. 시험이 두 탭을 흉내 낼 수 있게 밖에서 받는다. */
export interface TabDeps {
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
  channel: {
    postMessage: (message: TabMessage) => void
    onmessage: ((event: MessageEvent<TabMessage>) => void) | null
  }
  newId: () => string
  waitMs: number
}

/**
 * 이 문서가 어느 탭 이름을 쓸지 정하고, 복제면 물려받은 리프레시 토큰을 버린다.
 *
 * 정한 뒤에도 채널을 계속 듣는다 — **이 탭이 원본이 될 차례**가 언제든 온다.
 */
export function claimTab(deps: TabDeps): Promise<'fresh' | 'kept' | 'duplicate'> {
  const { storage, channel } = deps
  const me = deps.newId()
  //: 이 문서가 대답해 주는 이름. 정하기 전에는 아무에게도 대답하지 않는다 —
  //: 새로고침과 복제가 겹친 드문 경우에 둘이 서로를 복제로 보지 않게 한다.
  let owned: string | null = null
  const own = (id: string) => {
    owned = id
    try {
      storage.setItem(TAB_KEY, id)
    } catch {
      /* 못 적어도 이 문서는 이 이름으로 대답한다. 다음 새로고침이 새 탭처럼 굴 뿐이다. */
    }
  }

  const inherited = storage.getItem(TAB_KEY)
  const inheritedRefresh = storage.getItem(REFRESH_KEY)

  return new Promise((resolve) => {
    let timer: ReturnType<typeof setTimeout> | undefined
    channel.onmessage = (event) => {
      const message = event.data as TabMessage | null | undefined
      if (typeof message !== 'object' || message === null) return
      if (message.type === 'claim' && message.tab === owned && message.from !== me) {
        channel.postMessage({ type: 'taken', tab: message.tab, to: message.from })
        return
      }
      if (message.type === 'taken' && message.to === me && owned === null) {
        clearTimeout(timer)
        try {
          // 기다리는 사이에 이 탭에서 로그인했으면 그 토큰은 이 탭의 것이다.
          // 물려받은 것일 때만 버린다.
          if (inheritedRefresh !== null && storage.getItem(REFRESH_KEY) === inheritedRefresh) {
            storage.removeItem(REFRESH_KEY)
          }
        } catch {
          /* 못 지우면 예전처럼 된다. 기다림은 반드시 끝나야 한다 — 리프레시가 이것을 기다린다. */
        }
        own(me)
        resolve('duplicate')
      }
    }

    if (inherited === null) {
      own(me)
      resolve('fresh')
      return
    }
    timer = setTimeout(() => {
      own(inherited)
      resolve('kept')
    }, deps.waitMs)
    channel.postMessage({ type: 'claim', tab: inherited, from: me })
  })
}

/** 탭 이름. 보안 맥락이 아니어도(사내망의 http 설치) 만들 수 있어야 한다. */
function randomId(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

let settled: Promise<unknown> | null = null

/**
 * 이 탭의 이름이 정해질 때까지 기다린다. **리프레시 토큰을 읽기 전에** 부른다.
 *
 * 처음 부를 때 한 번만 돈다. sessionStorage 나 채널이 없는 곳(채널이 없는
 * 오래된 브라우저, 저장소를 막은 프라이빗 모드)에서는 바로 끝난다 — 예전 동작
 * 그대로다.
 */
export function settleTab(): Promise<unknown> {
  settled ??= startClaim()
  return settled
}

function startClaim(): Promise<unknown> {
  try {
    if (typeof BroadcastChannel === 'undefined') return Promise.resolve()
    const storage = sessionStorage
    const channel = new BroadcastChannel(TAB_CHANNEL)
    // Node 의 채널은 열려 있는 한 프로세스를 붙잡는다. 브라우저에는 없는 메서드다.
    ;(channel as { unref?: () => void }).unref?.()
    return claimTab({ storage, channel, newId: randomId, waitMs: TAB_WAIT_MS }).catch(
      () => undefined,
    )
  } catch {
    return Promise.resolve()
  }
}
