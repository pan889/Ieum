import { useTranslation } from 'react-i18next'

import { Alert, Button, Chip, Field, Select } from '@/shared/ui/primitives'

import type { IssueFilters } from './iql'
import { EMPTY_FILTERS, isEmptyFilters, matchesChips, toIql, toggle } from './iql'
import { useIssueTypes, useProjects } from './hooks'

const CATEGORIES = ['todo', 'in_progress', 'done'] as const
const PRIORITIES = [1, 2, 3, 4, 5] as const
const ASSIGNEES = ['any', 'me', 'unassigned'] as const

export interface FilterBarProps {
  filters: IssueFilters
  onFiltersChange: (next: IssueFilters) => void
  /** IQL 모드에서 편집 중인 질의. 칩 모드면 null. */
  iqlDraft: string | null
  onIqlDraftChange: (next: string | null) => void
  onRun: (iql: string) => void
  invalid?: string | undefined
}

export function FilterBar({
  filters,
  onFiltersChange,
  iqlDraft,
  onIqlDraftChange,
  onRun,
  invalid,
}: FilterBarProps) {
  const { t } = useTranslation(['issues', 'common'])
  const projects = useProjects()

  const project = projects.data?.items.find((p) => p.key === filters.projectKey) ?? null
  const types = useIssueTypes(project?.id ?? null)

  const patch = (next: Partial<IssueFilters>) => {
    onFiltersChange({ ...filters, ...next })
  }

  if (iqlDraft !== null) {
    const canReturn = matchesChips(iqlDraft, filters)
    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-start gap-2">
          <textarea
            aria-label={t('issues:filter.iql')}
            className="min-h-16 flex-1 rounded-md border border-border bg-surface px-3 py-2 font-mono text-sm text-fg"
            placeholder={t('issues:filter.iqlPlaceholder')}
            value={iqlDraft}
            onChange={(e) => { onIqlDraftChange(e.target.value); }}
            onKeyDown={(e) => {
              // Ctrl/Cmd+Enter 로 실행. Enter 는 줄바꿈이어야 한다.
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault()
                onRun(iqlDraft)
              }
            }}
          />
          <div className="flex flex-col gap-2">
            <Button onClick={() => { onRun(iqlDraft); }}>{t('issues:filter.run')}</Button>
            <Button
              variant="secondary"
              disabled={!canReturn}
              title={canReturn ? undefined : t('issues:filter.chipsLockedHint')}
              onClick={() => { onIqlDraftChange(null); }}
            >
              {t('issues:filter.toChips')}
            </Button>
          </div>
        </div>
        {!canReturn ? (
          <p className="text-xs text-muted">{t('issues:filter.chipsLockedHint')}</p>
        ) : null}
        {invalid ? <Alert>{invalid}</Alert> : null}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-3">
        <Select
          label={t('issues:list.project')}
          value={filters.projectKey ?? ''}
          onChange={(e) => {
            // 프로젝트가 바뀌면 유형 칩은 의미를 잃는다 — 같이 비운다.
            patch({ projectKey: e.target.value || null, typeNames: [] })
          }}
        >
          <option value="">{t('issues:list.projectAll')}</option>
          {projects.data?.items.map((p) => (
            <option key={p.id} value={p.key}>
              {p.key} · {p.name}
            </option>
          ))}
        </Select>

        <div className="min-w-48 flex-1">
          <Field
            label={t('issues:filter.text')}
            placeholder={t('issues:filter.textPlaceholder')}
            value={filters.text}
            onChange={(e) => { patch({ text: e.target.value }); }}
          />
        </div>

        <Button variant="secondary" onClick={() => { onIqlDraftChange(toIql(filters)); }}>
          {t('issues:filter.toIql')}
        </Button>
        <Button
          variant="ghost"
          disabled={isEmptyFilters(filters)}
          onClick={() => { onFiltersChange(EMPTY_FILTERS); }}
        >
          {t('issues:filter.clear')}
        </Button>
      </div>

      <ChipRow label={t('issues:filter.status')}>
        {CATEGORIES.map((category) => (
          <Chip
            key={category}
            pressed={filters.statusCategories.includes(category)}
            onClick={() => { patch({ statusCategories: toggle<string>(filters.statusCategories, category) }); }}
          >
            {t(`issues:category.${category}`)}
          </Chip>
        ))}
      </ChipRow>

      <ChipRow label={t('issues:filter.assignee')}>
        {ASSIGNEES.map((value) => (
          <Chip
            key={value}
            pressed={filters.assignee === value}
            onClick={() => { patch({ assignee: value }); }}
          >
            {value === 'any'
              ? t('issues:filter.any')
              : value === 'me'
                ? t('issues:filter.assigneeMe')
                : t('issues:detail.unassigned')}
          </Chip>
        ))}
      </ChipRow>

      <ChipRow label={t('issues:filter.priority')}>
        {PRIORITIES.map((value) => (
          <Chip
            key={value}
            pressed={filters.priorities.includes(value)}
            onClick={() => { patch({ priorities: toggle<number>(filters.priorities, value) }); }}
          >
            {t(`issues:priority.${String(value)}`)}
          </Chip>
        ))}
      </ChipRow>

      {types.data && types.data.length > 0 ? (
        <ChipRow label={t('issues:filter.type')}>
          {types.data.map((type) => (
            <Chip
              key={type.id}
              pressed={filters.typeNames.includes(type.name)}
              onClick={() => { patch({ typeNames: toggle<string>(filters.typeNames, type.name) }); }}
            >
              {type.name}
            </Chip>
          ))}
        </ChipRow>
      ) : null}

      {invalid ? <Alert>{invalid}</Alert> : null}
    </div>
  )
}

function ChipRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="w-16 shrink-0 text-xs font-medium text-muted">{label}</span>
      {children}
    </div>
  )
}
