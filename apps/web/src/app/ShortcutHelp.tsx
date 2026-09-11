/**
 * `?` 단축키 도움말.
 *
 * **숨은 단축키는 없는 것과 같다.** 이 목록이 없으면 `c` 나 `/` 를 아는 사람은
 * 만든 사람뿐이다. 그래서 도움말은 이 계층의 부속이 아니라 절반이다.
 *
 * 목록은 **지금 살아 있는 묶음**에서 그대로 나온다 — 손으로 적지 않고,
 * 목록 화면에서만 도는 `j`/`k` 는 목록 화면에서만 보인다. 어디서나 보여 주면
 * 그것이 이 저장소가 이미 저지른 잘못(없는 키를 있는 것처럼 적어 두기)의
 * 화면판이 된다.
 */

import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import { formatCombo, isMac, type Shortcut } from '@/shared/keys/catalog'
import { swallow } from '@/shared/keys/keys'
import { useActiveShortcutGroups, useHotkeys } from '@/shared/keys/useHotkeys'
import { Button, Card } from '@/shared/ui/primitives'

interface Props {
  onClose: () => void
}

/** 같은 일을 하는 조합을 한 줄로 모은다 — `O / Enter` 처럼. */
function byAction(shortcuts: readonly Shortcut[]): { labelKey: string; combos: string[] }[] {
  const rows: { labelKey: string; combos: string[] }[] = []
  for (const shortcut of shortcuts) {
    const found = rows.find((row) => row.labelKey === shortcut.labelKey)
    if (found) found.combos.push(shortcut.combo)
    else rows.push({ labelKey: shortcut.labelKey, combos: [shortcut.combo] })
  }
  return rows
}

export function ShortcutHelp({ onClose }: Props) {
  const { t } = useTranslation(['common'])
  const groups = useActiveShortcutGroups()
  const mac = isMac()
  const card = useRef<HTMLDivElement>(null)

  /**
   * **키로 연 것은 키로 닫혀야 한다.** 여기 오는 길은 `?` 하나뿐인데 닫는
   * 길이 마우스뿐이었다 — 키보드만 쓰는 사람은 자기가 연 덮개에 갇힌다.
   *
   * 모달이라 아래(전역 `c`·`/`)를 통째로 덮는다. 안 그러면 도움말을 띄운 채
   * `c` 로 새 이슈 화면에 끌려가고 **덮개는 그 위에 남는다.**
   */
  useHotkeys({ escape: onClose, 'mod+k': swallow }, { modal: true, whileTyping: ['mod+k'] })

  // 초점을 덮개 안으로 옮기고, 닫을 때 있던 자리로 돌려준다. 목록에서 열었으면
  // 짚고 있던 줄로 돌아간다 — 도움말을 한 번 본 값으로 자리를 잃지 않는다.
  useEffect(() => {
    const cameFrom = document.activeElement
    card.current?.focus()
    return () => {
      if (cameFrom instanceof HTMLElement && cameFrom.isConnected) cameFrom.focus()
    }
  }, [])

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4"
      onMouseDown={onClose}
    >
      <Card
        ref={card}
        role="dialog"
        aria-modal="true"
        aria-label={t('common:keys.help')}
        // 초점은 받되 Tab 순서에는 안 들어간다. 안이 비어 보이는 테두리를
        // 그리지 않으려고 outline 을 끈다 — 초점의 목적이 낭독이지 표시가 아니다.
        tabIndex={-1}
        className="w-full max-w-sm outline-none"
        onMouseDown={(event) => {
          event.stopPropagation()
        }}
      >
        <h2 className="text-sm font-medium">{t('common:keys.help')}</h2>

        {groups.map((group) => (
          <section key={group.titleKey} className="mt-3">
            <h3 className="text-xs font-medium text-muted">{t(`common:${group.titleKey}`)}</h3>
            <dl className="mt-1.5 flex flex-col gap-2">
              {byAction(group.shortcuts).map((row) => (
                <div key={row.labelKey} className="flex items-center justify-between gap-4">
                  <dt className="text-sm text-fg">{t(`common:${row.labelKey}`)}</dt>
                  <dd className="flex shrink-0 gap-1">
                    {row.combos.map((combo) => (
                      <kbd
                        key={combo}
                        className="rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-xs text-muted"
                      >
                        {formatCombo(combo, mac)}
                      </kbd>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}

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
