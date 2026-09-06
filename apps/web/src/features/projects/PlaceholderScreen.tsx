import { useTranslation } from 'react-i18next'

import { Card } from '@/shared/ui/primitives'

/** M1·M2 에서 채운다. 지금은 셸의 라우팅이 도는지 보여주는 자리다. */
export function PlaceholderScreen({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation(['common'])
  return (
    <section className="mx-auto max-w-3xl">
      <h1 className="text-xl font-semibold">{t(titleKey)}</h1>
      <Card className="mt-6 text-center text-sm text-muted">{t('common:state.empty')}</Card>
    </section>
  )
}
