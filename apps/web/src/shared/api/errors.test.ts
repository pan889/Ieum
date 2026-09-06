import { ApiError } from '@ieum/api-client'
import i18next from 'i18next'
import ICU from 'i18next-icu'
import { beforeAll, describe, expect, it } from 'vitest'

import errorsEn from '@ieum/i18n/en/errors.json'
import errorsKo from '@ieum/i18n/ko/errors.json'

import { describeError, hasCode } from './errors'

/** 실제 i18next + ICU 로 돌린다. 가짜 t 를 쓰면 ICU 포매팅이 검증되지 않는다. */
beforeAll(async () => {
  await i18next.use(ICU).init({
    lng: 'en',
    fallbackLng: 'en',
    ns: ['errors'],
    defaultNS: 'errors',
    resources: { en: { errors: errorsEn }, ko: { errors: errorsKo } },
    interpolation: { escapeValue: false },
  })
})

describe('describeError', () => {
  it('서버 message 가 아니라 code 를 번역해서 보여준다', async () => {
    const error = new ApiError(403, {
      code: 'auth.permission_denied',
      message: '권한이 없다 (서버 디버깅 문구)',
    })

    await i18next.changeLanguage('en')
    expect(describeError(error)).toBe("You don't have permission to do that.")

    await i18next.changeLanguage('ko')
    expect(describeError(error)).toBe('권한이 없습니다.')
  })

  it('details 를 ICU 변수로 넘긴다', async () => {
    const error = new ApiError(422, {
      code: 'identity.password_too_short',
      details: { min_length: 12 },
    })
    await i18next.changeLanguage('en')
    expect(describeError(error)).toBe('Use at least 12 characters.')
    await i18next.changeLanguage('ko')
    expect(describeError(error)).toBe('12자 이상 입력하세요.')
  })

  it('ICU plural 이 언어별로 다르게 적용된다', async () => {
    const one = new ApiError(429, {
      code: 'common.rate_limited',
      details: { retry_after_seconds: 1 },
    })
    const many = new ApiError(429, {
      code: 'common.rate_limited',
      details: { retry_after_seconds: 30 },
    })

    await i18next.changeLanguage('en')
    expect(describeError(one)).toContain('1 second')
    expect(describeError(many)).toContain('30 seconds')

    // 한국어는 복수형이 없다. 같은 형태로 나와야 한다.
    await i18next.changeLanguage('ko')
    expect(describeError(one)).toContain('1초')
    expect(describeError(many)).toContain('30초')
  })

  it('모르는 코드는 일반 오류 문구로 떨어진다', async () => {
    await i18next.changeLanguage('en')
    expect(describeError(new ApiError(500, { code: 'some.future.code' }))).toBe(
      errorsEn['internal.error'],
    )
  })

  it('ApiError 가 아닌 값도 안전하게 처리한다', async () => {
    await i18next.changeLanguage('en')
    expect(describeError(new TypeError('network down'))).toBe(errorsEn['internal.error'])
  })
})

describe('hasCode', () => {
  it('코드가 일치할 때만 참', () => {
    const error = new ApiError(403, { code: 'auth.mfa_required' })
    expect(hasCode(error, 'auth.mfa_required')).toBe(true)
    expect(hasCode(error, 'auth.permission_denied')).toBe(false)
    expect(hasCode(new Error('x'), 'auth.mfa_required')).toBe(false)
  })
})
