/**
 * SLA 폼의 파서·포매터 (feature-map C4).
 *
 * **왕복만 보지 않는다.** `format(parse(x)) === x` 는 둘이 같은 방향으로
 * 틀려도 통과한다 — 예를 들어 `4h` 를 4분으로 읽고 4분을 `4h` 로 쓰면 왕복은
 * 성립하고 SLA 는 60배 틀린다. 그래서 **초 값을 직접 못 박는다.**
 *
 * 못 읽는 입력이 `null` 인지도 여기서 본다. 화면은 `null` 로 저장을 막는데,
 * 파서가 대충 읽어 버리면 막을 것이 없어진다.
 */

import { describe, expect, it } from 'vitest'

import {
  escalationsReady,
  formatDuration,
  formatGoals,
  formatWorkingHours,
  parseDuration,
  parseGoals,
  parseWorkingHours,
  pauseNames,
} from './SlaScreen'

describe('parseDuration', () => {
  it.each([
    ['30m', 1800],
    ['4h', 14400],
    ['2d', 172800],
    ['2d 4h', 187200],
    ['1d 2h 30m', 95400],
    ['  4H  ', 14400],
  ])('%s → %i초', (raw, seconds) => {
    expect(parseDuration(raw)).toBe(seconds)
  })

  it.each([
    [''],
    ['4'], // 단위가 없다
    ['h'], // 숫자가 없다
    ['0h'], // 0초 목표는 처음부터 위반이다
    ['4시간'],
    ['4h 그리고 조금'], // 사람이 적은 뜻을 잃는다
    ['4w'], // 주는 안 받는다 — 업무일과 헷갈린다
  ])('%s 는 읽지 않는다', (raw) => {
    expect(parseDuration(raw)).toBeNull()
  })
})

describe('formatDuration', () => {
  it.each([
    [1800, '30m'],
    [14400, '4h'],
    [187200, '2d 4h'],
    [95400, '1d 2h 30m'],
  ])('%i초 → %s', (seconds, text) => {
    expect(formatDuration(seconds)).toBe(text)
  })
})

describe('parseWorkingHours', () => {
  it('요일마다 여러 구간을 읽는다', () => {
    expect(parseWorkingHours('0 09:00-12:00 13:00-18:00\n1 09:00-18:00')).toEqual({
      '0': [
        ['09:00', '12:00'],
        ['13:00', '18:00'],
      ],
      '1': [['09:00', '18:00']],
    })
  })

  it('빈 줄은 넘긴다 — 사람은 줄을 띄운다', () => {
    expect(parseWorkingHours('\n0 09:00-18:00\n\n')).toEqual({ '0': [['09:00', '18:00']] })
  })

  it.each([
    [''],
    ['0'], // 구간이 없다
    ['7 09:00-18:00'], // 요일이 아니다
    ['0 9:00-18:00'], // 자리를 채워야 한다 — 서버 정규식도 같다
    ['0 09:00~18:00'],
    ['월 09:00-18:00'],
  ])('%s 는 읽지 않는다', (raw) => {
    expect(parseWorkingHours(raw)).toBeNull()
  })

  it('한 줄만 못 읽어도 전체를 거절한다', () => {
    // 조용히 버리면 관리자가 적은 요일이 사라진 채로 저장된다.
    expect(parseWorkingHours('0 09:00-18:00\n1 잘못')).toBeNull()
  })
})

describe('formatWorkingHours', () => {
  it('요일 순으로 되돌린다 — 사전 순이 아니다', () => {
    expect(
      formatWorkingHours({
        '2': [['09:00', '18:00']],
        '10': [['09:00', '18:00']], // 있을 수 없지만, 문자열 정렬이면 1 다음이다
        '1': [['09:00', '12:00']],
      }),
    ).toBe('1 09:00-12:00\n2 09:00-18:00\n10 09:00-18:00')
  })
})

describe('parseGoals', () => {
  it('조건 있는 줄과 없는 줄을 함께 읽는다', () => {
    expect(parseGoals('priority>=5 1h\n4h')).toEqual([
      { seconds: 3600, priority_min: 5 },
      { seconds: 14400 },
    ])
  })

  it.each([[''], ['priority>=5'], ['priority>=5 4시간'], ['priority>=abc 4h']])(
    '%s 는 읽지 않는다',
    (raw) => {
      expect(parseGoals(raw)).toBeNull()
    },
  )
})

describe('formatGoals', () => {
  it('조건을 붙여 되돌린다', () => {
    expect(formatGoals([{ seconds: 3600, priority_min: 5 }, { seconds: 14400 }])).toBe(
      'priority>=5 1h\n4h',
    )
  })
})

describe('pauseNames', () => {
  const options = [
    { id: 'a', name: '고객 답변 대기', category: 'todo', workflow_name: 'wf' },
    { id: 'b', name: '보류', category: 'todo', workflow_name: 'wf' },
  ]

  it('id 를 이름으로 바꿔 이어 붙인다', () => {
    expect(pauseNames(['a', 'b'], options)).toBe('고객 답변 대기, 보류')
  })

  it('이름을 못 찾으면 id 를 그대로 남긴다', () => {
    // 조용히 빼면 "3개 걸었는데 2개만 보인다" 가 되고, 어느 것이 사라졌는지
    // 알 수 없다.
    expect(pauseNames(['a', 'zzz'], options)).toBe('고객 답변 대기, zzz')
  })

  it('목록이 아직 안 왔으면 id 를 보여 준다 — 조용히 비우지 않는다', () => {
    expect(pauseNames(['a'], undefined)).toBe('a')
  })
})

describe('escalationsReady', () => {
  it('빈 목록은 저장할 수 있다 — 에스컬레이션 없는 정책이 대부분이다', () => {
    expect(escalationsReady([])).toBe(true)
  })

  it('대상을 고른 notify 는 저장할 수 있다', () => {
    expect(escalationsReady([{ at_percent: 75, action: 'notify', user_id: 'u1' }])).toBe(true)
  })

  it('부를 사람을 안 고른 notify 로는 저장을 막는다', () => {
    // 서버도 거절하지만, 여기서 막으면 어느 줄이 문제인지 그 자리에서 보인다.
    expect(escalationsReady([{ at_percent: 75, action: 'notify' }])).toBe(false)
  })

  it('우선순위를 안 정한 raise_priority 로는 저장을 막는다', () => {
    expect(escalationsReady([{ at_percent: 100, action: 'raise_priority' }])).toBe(false)
  })

  it.each([0, 501])('%i%% 는 서버가 거절하는 값이라 여기서 막는다', (at_percent) => {
    expect(escalationsReady([{ at_percent, action: 'raise_priority', priority: 5 }])).toBe(false)
  })

  it('한 줄만 모자라도 전체를 막는다', () => {
    expect(
      escalationsReady([
        { at_percent: 50, action: 'raise_priority', priority: 4 },
        { at_percent: 100, action: 'notify' },
      ]),
    ).toBe(false)
  })
})
