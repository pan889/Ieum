import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { Alert, Button, Chip, Field } from '@/shared/ui/primitives'

import { IqlEditor } from './IqlEditor'
import type { IssueFilters } from './iql'
import { EMPTY_FILTERS, isEmptyFilters, matchesChips, toIql, toggle } from './iql'
import { projectByKey, useIssueTypes, useProjectByKey } from './hooks'

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
  const queryClient = useQueryClient()
  // 고른 프로젝트만 물어본다. 예전에는 전체 목록을 받아 훑었는데, 그 목록이
  // 상한에서 잘리면 필터에서 프로젝트가 통째로 사라졌다(hooks.ts 참고).
  const project = useProjectByKey(filters.projectKey).data ?? null
  const types = useIssueTypes(project?.id ?? null)

  const patch = (next: Partial<IssueFilters>) => {
    onFiltersChange({ ...filters, ...next })
  }

  if (iqlDraft !== null) {
    const canReturn = matchesChips(iqlDraft, filters)
    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-start gap-2">
          <IqlEditor
            label={t('issues:filter.iql')}
            placeholder={t('issues:filter.iqlPlaceholder')}
            value={iqlDraft}
            onChange={onIqlDraftChange}
            onRun={() => { onRun(iqlDraft); }}
            invalid={invalid}
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
        <p className="text-xs text-muted">{t('issues:filter.suggestionsHint')}</p>
        {!canReturn ? (
          <p className="text-xs text-muted">{t('issues:filter.chipsLockedHint')}</p>
        ) : null}
      </div>
    )
  }

  return (
    // 상자는 화면이 씌운다(`IssuesScreen` 의 조작 상자). 여기서 또 테두리를
    // 그리면 상자 안에 상자가 생긴다.
    <div className="flex flex-col gap-2 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <ProjectPicker
          label={t('issues:list.project')}
          chosen={project}
          emptyLabel={t('issues:list.projectAll')}
          // 이름을 아직 못 읽었으면 **키라도** 보여 준다. "전체 프로젝트" 라고
          // 쓰면 목록은 걸러져 있는데 화면만 아니라고 말하는 셈이다.
          unresolvedLabel={filters.projectKey ?? undefined}
          // 프로젝트가 바뀌면 유형 칩은 의미를 잃는다 — 같이 비운다.
          onPick={(picked) => {
            // 피커가 방금 준 것을 캐시에 넣는다. 안 그러면 키만 보이다가
            // 한 박자 뒤에 이름으로 바뀐다 — 같은 답을 두 번 묻는 셈이다.
            queryClient.setQueryData(projectByKey(picked.key), picked)
            patch({ projectKey: picked.key, typeNames: [] })
          }}
          onClear={
            filters.projectKey === null
              ? undefined
              : () => { patch({ projectKey: null, typeNames: [] }) }
          }
        />

        {/*
          라벨은 접근성 트리에만 둔다. 도구 막대 한 줄에 선 것들 사이에서 이
          칸만 라벨을 위로 세우면 "Text" 라는 글자가 아무것도 안 붙은 채로
          허공에 떴다 — 자리표시자가 같은 말을 이미 하고 있다.
        */}
        <div className="min-w-48 flex-1">
          <Field
            label={t('issues:filter.text')}
            labelHidden
            placeholder={t('issues:filter.textPlaceholder')}
            value={filters.text}
            onChange={(e) => { patch({ text: e.target.value }); }}
            className="w-full"
          />
        </div>

        <Button
          variant="secondary"
          size="sm"
          onClick={() => { onIqlDraftChange(toIql(filters)); }}
        >
          {t('issues:filter.toIql')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
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

/**
 * 칩 한 줄. 라벨은 **고정 폭 도랑**에 세운다 — 줄마다 라벨 길이가 달라
 * 칩이 들쭉날쭉 시작하면, 눈은 매 줄마다 시작점을 다시 찾는다.
 */
function ChipRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="w-[4.5rem] shrink-0 text-2xs font-semibold uppercase tracking-wide text-subtle">
        {label}
      </span>
      {children}
    </div>
  )
}
