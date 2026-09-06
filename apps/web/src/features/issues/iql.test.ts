import { describe, expect, it } from 'vitest'

import { EMPTY_FILTERS, matchesChips, quote, toIql, toggle } from './iql'

describe('quote', () => {
  it('escapes backslashes before quotes', () => {
    // 순서를 뒤집으면 이스케이프한 역슬래시를 다시 이스케이프해 깨진다.
    expect(quote('a\\"b')).toBe('"a\\\\\\"b"')
  })
})

describe('toIql', () => {
  it('is empty when nothing is selected', () => {
    expect(toIql(EMPTY_FILTERS)).toBe('')
  })

  it('uses = for one value and IN for many', () => {
    expect(toIql({ ...EMPTY_FILTERS, statusCategories: ['todo'] })).toBe(
      'statusCategory = "todo"',
    )
    expect(toIql({ ...EMPTY_FILTERS, statusCategories: ['todo', 'done'] })).toBe(
      'statusCategory IN ("todo", "done")',
    )
  })

  it('ANDs every chip together', () => {
    expect(
      toIql({
        ...EMPTY_FILTERS,
        projectKey: 'ENG',
        statusCategories: ['in_progress'],
        assignee: 'me',
        text: 'login',
      }),
    ).toBe(
      'project = "ENG" AND statusCategory = "in_progress" AND assignee = currentUser() AND summary ~ "login"',
    )
  })

  it('renders unassigned as IS EMPTY, not = null', () => {
    expect(toIql({ ...EMPTY_FILTERS, assignee: 'unassigned' })).toBe('assignee IS EMPTY')
  })

  it('leaves numbers unquoted', () => {
    expect(toIql({ ...EMPTY_FILTERS, priorities: [1, 2] })).toBe('priority IN (1, 2)')
  })
})

describe('matchesChips', () => {
  const filters = { ...EMPTY_FILTERS, projectKey: 'ENG' }

  it('accepts the query the chips generated', () => {
    expect(matchesChips(toIql(filters), filters)).toBe(true)
  })

  it('rejects a hand-edited query so switching back cannot discard it', () => {
    expect(matchesChips('project = "ENG" AND priority < 3', filters)).toBe(false)
  })
})

describe('toggle', () => {
  it('adds then removes', () => {
    expect(toggle<string>([], 'a')).toEqual(['a'])
    expect(toggle(['a', 'b'], 'a')).toEqual(['b'])
  })
})
