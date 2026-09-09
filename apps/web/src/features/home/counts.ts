/**
 * 첫 화면의 요약 숫자 (M5 대시보드).
 *
 * 목록을 훑지 않아도 **"늦은 게 있나"** 를 알 수 있어야 한다. 그 답이 목록
 * 아래쪽에 묻혀 있으면 첫 화면이 첫 화면 구실을 못 한다.
 *
 * 순수 함수로 빼는 이유는 `due.ts` 와 같다: 날짜 세기는 조용히 틀린다.
 * 오늘이 기한인 것을 "지났다" 로 세면 사람은 이미 늦은 줄 알고, 반대로
 * 세면 오늘 해야 할 것을 내일로 미룬다. 화면만 봐서는 못 알아챈다.
 *
 * **이슈와 문서 태스크를 같은 자로 센다.** 두 곳에서 각자 세면 한쪽만
 * 고쳐지는 날이 오고, 그때 합이 안 맞는다.
 */

import { urgencyOf } from '@/features/wiki/due'

export interface DueCounts {
  /** 기한이 지난 것. */
  overdue: number
  /** 기한이 오늘인 것. */
  today: number
}

/**
 * 기한 목록에서 지난 것과 오늘인 것을 센다.
 *
 * 기한 없는 것(`null`)은 **어느 쪽도 아니다.** 오늘로 세면 첫 화면이 늘
 * 급해 보이고, 사람은 곧 그 숫자를 안 믿는다.
 */
export function countDue(dues: (string | null)[], today: string): DueCounts {
  let overdue = 0
  let due = 0
  for (const value of dues) {
    const urgency = urgencyOf(value, today)
    if (urgency === 'overdue') overdue += 1
    else if (urgency === 'today') due += 1
  }
  return { overdue, today: due }
}

/** 셀 것이 하나도 없나. 요약 줄을 아예 안 그릴지 정한다. */
export function isCalm(counts: DueCounts): boolean {
  return counts.overdue === 0 && counts.today === 0
}
