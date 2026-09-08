import { describe, expect, it, vi } from 'vitest'

import type { Project, ProjectsApi } from '@ieum/api-client'

import { MAX_PAGES, PAGE_SIZE, fetchAllProjects } from './allProjects'

function project(key: string): Project {
  return {
    id: key,
    key,
    name: key,
    description: null,
    parent_id: null,
    lead_id: null,
    is_public: true,
    archived_at: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

/** `pages` 만큼 커서를 이어 주는 가짜 API. */
function api(pages: string[][]): { api: ProjectsApi; calls: () => number } {
  let calls = 0
  const list = vi.fn(async (params: { cursor?: string } = {}) => {
    const index = params.cursor ? Number(params.cursor) : 0
    calls += 1
    return Promise.resolve({
      items: (pages[index] ?? []).map(project),
      next_cursor: index + 1 < pages.length ? String(index + 1) : null,
      total: null,
    })
  })
  return { api: { list } as unknown as ProjectsApi, calls: () => calls }
}

describe('fetchAllProjects', () => {
  it('한 페이지면 한 번만 부른다', async () => {
    const { api: a, calls } = api([['A', 'B']])
    expect((await fetchAllProjects(a)).items.map((p) => p.key)).toEqual(['A', 'B'])
    expect(calls()).toBe(1)
  })

  it('커서를 끝까지 따라간다', async () => {
    // 잘린 목록은 "그 프로젝트가 없다" 와 구별되지 않는다. 101번째
    // 프로젝트를 가진 사람이 거기에 이슈를 못 만든다.
    const { api: a } = api([['A'], ['B'], ['C']])
    expect((await fetchAllProjects(a)).items.map((p) => p.key)).toEqual(['A', 'B', 'C'])
  })

  it('상한을 넘어가면 멈춘다', async () => {
    // 상한이 없으면 데이터가 이상할 때 화면이 요청을 끝없이 낸다.
    const { api: a, calls } = api(Array.from({ length: MAX_PAGES + 5 }, (_, i) => [`P${String(i)}`]))
    const all = await fetchAllProjects(a)
    expect(calls()).toBe(MAX_PAGES)
    expect(all.items).toHaveLength(MAX_PAGES)
  })

  it('상한에 닿으면 잘렸다고 말한다', async () => {
    // **이것이 이 파일의 요점이다.** 상한 자체는 안전장치로 맞지만, 닿은
    // 사실이 어디에도 안 남으면 막으려던 실패가 한 겹 안쪽에서 되살아난다 —
    // 2000개에서 잘린 목록도 "그 프로젝트가 없다" 와 구별되지 않는다.
    // 개발 DB 가 2334개가 되면서 E2E 가 실제로 그렇게 멈췄다.
    const { api: a } = api(Array.from({ length: MAX_PAGES + 1 }, (_, i) => [`P${String(i)}`]))
    expect((await fetchAllProjects(a)).truncated).toBe(true)
  })

  it('마지막 페이지에서 끝나면 잘리지 않았다', async () => {
    // 딱 상한만큼 있고 그 뒤가 없는 경우. 페이지 수만 보고 판단하면 여기서
    // 거짓 경고가 뜬다 — 커서가 끊긴 것과 상한에 닿은 것은 다르다.
    const { api: a, calls } = api(Array.from({ length: MAX_PAGES }, (_, i) => [`P${String(i)}`]))
    const all = await fetchAllProjects(a)
    expect(calls()).toBe(MAX_PAGES)
    expect(all.truncated).toBe(false)
  })

  it('한 페이지로 끝나도 잘리지 않았다', async () => {
    const { api: a } = api([['A', 'B']])
    expect((await fetchAllProjects(a)).truncated).toBe(false)
  })

  it('빈 결과도 빈 배열이다', async () => {
    const { api: a } = api([[]])
    expect(await fetchAllProjects(a)).toEqual({ items: [], truncated: false })
  })

  it('서버 상한과 같은 크기로 받는다', () => {
    expect(PAGE_SIZE).toBe(100)
  })
})
