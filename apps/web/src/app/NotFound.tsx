/**
 * 없는 주소.
 *
 * 라우터의 기본 화면은 **"Not Found" 네 글자**였다. 앱 껍데기 안에 그
 * 한 줄만 떠 있으면, 화면이 고장 났는지 주소를 잘못 친 것인지 알 길이
 * 없고 돌아갈 손잡이도 없다 — `/settings` 처럼 묶음 이름만 친 주소가
 * 바로 거기로 떨어진다.
 */

import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { EmptyState } from '@/shared/ui/primitives'

export function NotFound() {
  const { t } = useTranslation('common')
  return (
    <EmptyState
      title={t('common:state.notFound')}
      description={t('common:state.notFoundHint')}
      action={
        <Link
          to="/"
          className="inline-flex h-8 items-center rounded-md bg-accent px-3 text-sm font-medium text-accent-fg shadow-raised hover:brightness-110"
        >
          {t('common:action.goHome')}
        </Link>
      }
      className="mt-6"
    />
  )
}
