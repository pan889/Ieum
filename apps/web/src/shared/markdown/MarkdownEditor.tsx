import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Markdown } from './Markdown'

/**
 * 마크다운 입력 + 미리보기 탭.
 *
 * WYSIWYG 은 M2 다. 그전까지도 사용자는 자기가 쓴 표가 어떻게 보일지 알아야
 * 하므로, 소스 입력 옆에 미리보기를 붙인다 (ux-principles.md — 소스 모드
 * 토글은 상시 제공).
 */
export function MarkdownEditor({
  label,
  value,
  onChange,
  placeholder,
  rows = 8,
}: {
  label: string
  value: string
  onChange: (next: string) => void
  placeholder?: string
  rows?: number
}) {
  const { t } = useTranslation(['common'])
  const [tab, setTab] = useState<'write' | 'preview'>('write')
  const id = useId()

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-3">
        <label htmlFor={id} className="text-sm font-medium text-fg">
          {label}
        </label>
        {/* 탭 목록에 필드 이름을 주면 textarea 의 라벨과 이름이 겹친다.
            서로 다른 컨트롤이 같은 이름을 가지면 구분할 방법이 없다. */}
        <div
          role="tablist"
          aria-label={t('common:editor.mode')}
          className="ml-auto flex gap-1 text-xs"
        >
          {(['write', 'preview'] as const).map((name) => (
            <button
              key={name}
              id={`${id}-tab-${name}`}
              type="button"
              role="tab"
              aria-selected={tab === name}
              aria-controls={`${id}-panel`}
              className={clsx(
                'rounded px-2 py-1',
                tab === name ? 'bg-surface-raised font-medium text-fg' : 'text-muted hover:text-fg',
              )}
              onClick={() => { setTab(name); }}
            >
              {t(`common:editor.${name}`)}
            </button>
          ))}
        </div>
      </div>

      <div id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-tab-${tab}`}>
        {tab === 'write' ? (
          <textarea
            id={id}
            rows={rows}
            placeholder={placeholder}
            className="w-full rounded-md border border-border bg-surface px-3 py-2 font-mono text-sm text-fg placeholder:text-muted"
            value={value}
            onChange={(event) => { onChange(event.target.value); }}
          />
        ) : (
          <div className="min-h-24 rounded-md border border-border bg-surface px-3 py-2">
            {value.trim() ? (
              <Markdown source={value} className="text-sm" />
            ) : (
              <p className="text-sm text-muted">{t('common:editor.previewEmpty')}</p>
            )}
          </div>
        )}
      </div>

      <p className="text-xs text-muted">{t('common:editor.markdownHint')}</p>
    </div>
  )
}
