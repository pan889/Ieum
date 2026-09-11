/**
 * `?` 단축키 도움말.
 *
 * **숨은 단축키는 없는 것과 같다.** 이 목록이 없으면 `c` 나 `/` 를 아는 사람은
 * 만든 사람뿐이다. 그래서 도움말은 이 계층의 부속이 아니라 절반이다.
 *
 * 목록은 `GLOBAL_SHORTCUTS` 에서 그대로 나온다 — 여기에 손으로 적지 않는다.
 */

import { useTranslation } from 'react-i18next'

import { formatCombo, isMac, GLOBAL_SHORTCUTS } from '@/shared/keys/catalog'
import { Button, Card } from '@/shared/ui/primitives'

interface Props {
  onClose: () => void
}

export function ShortcutHelp({ onClose }: Props) {
  const { t } = useTranslation(['common'])
  const mac = isMac()

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4"
      onMouseDown={onClose}
    >
      <Card
        role="dialog"
        aria-modal="true"
        aria-label={t('common:keys.help')}
        className="w-full max-w-sm"
        onMouseDown={(event) => {
          event.stopPropagation()
        }}
      >
        <h2 className="text-sm font-medium">{t('common:keys.help')}</h2>

        <dl className="mt-3 flex flex-col gap-2">
          {GLOBAL_SHORTCUTS.map((shortcut) => (
            <div key={shortcut.id} className="flex items-center justify-between gap-4">
              <dt className="text-sm text-fg">{t(`common:${shortcut.labelKey}`)}</dt>
              <dd>
                <kbd className="rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-xs text-muted">
                  {formatCombo(shortcut.combo, mac)}
                </kbd>
              </dd>
            </div>
          ))}
        </dl>

        <p className="mt-3 text-xs text-muted">{t('common:keys.helpTypingNote')}</p>

        <div className="mt-4 flex justify-end">
          <Button variant="secondary" onClick={onClose}>
            {t('common:action.close')}
          </Button>
        </div>
      </Card>
    </div>
  )
}
