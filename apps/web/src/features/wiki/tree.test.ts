import { describe, expect, it } from 'vitest'

import type { PageNode } from '@ieum/api-client'

import { ancestorsOf, buildTree, flatten } from './tree'

function node(over: Partial<PageNode> & { id: string; path: string }): PageNode {
  return {
    parent_id: null,
    slug: over.path.split('/').pop() ?? '',
    title: over.id,
    status: 'published',
    position: 0,
    ...over,
  }
}

describe('buildTree', () => {
  it('부모-자식으로 엮는다', () => {
    const tree = buildTree([
      node({ id: 'a', path: 'a' }),
      node({ id: 'b', path: 'a/b', parent_id: 'a' }),
    ])
    expect(tree).toHaveLength(1)
    expect(tree[0]?.children.map((c) => c.id)).toEqual(['b'])
  })

  it('깊이를 매긴다', () => {
    const tree = buildTree([
      node({ id: 'a', path: 'a' }),
      node({ id: 'b', path: 'a/b', parent_id: 'a' }),
      node({ id: 'c', path: 'a/b/c', parent_id: 'b' }),
    ])
    expect(tree[0]?.depth).toBe(0)
    expect(tree[0]?.children[0]?.depth).toBe(1)
    expect(tree[0]?.children[0]?.children[0]?.depth).toBe(2)
  })

  it('부모가 없는 문서는 최상위로 올린다', () => {
    // 버리면 볼 수 있는 문서가 화면에서 사라진다.
    const tree = buildTree([node({ id: 'orphan', path: 'x/orphan', parent_id: 'missing' })])
    expect(tree.map((n) => n.id)).toEqual(['orphan'])
  })

  it('서버가 준 순서를 유지한다', () => {
    // 여기서 다시 정렬하면 사용자가 정한 순서가 무시된다.
    const tree = buildTree([
      node({ id: 'z', path: 'z', position: 0 }),
      node({ id: 'a', path: 'a', position: 1 }),
    ])
    expect(tree.map((n) => n.id)).toEqual(['z', 'a'])
  })

  it('빈 목록은 빈 트리다', () => {
    expect(buildTree([])).toEqual([])
  })
})

describe('flatten', () => {
  const tree = buildTree([
    node({ id: 'a', path: 'a' }),
    node({ id: 'b', path: 'a/b', parent_id: 'a' }),
    node({ id: 'c', path: 'c' }),
  ])

  it('그릴 순서대로 편다', () => {
    expect(flatten(tree, new Set()).map((n) => n.id)).toEqual(['a', 'b', 'c'])
  })

  it('접힌 가지는 건너뛴다', () => {
    expect(flatten(tree, new Set(['a'])).map((n) => n.id)).toEqual(['a', 'c'])
  })
})

describe('ancestorsOf', () => {
  const nodes = [
    node({ id: 'a', path: 'a' }),
    node({ id: 'b', path: 'a/b', parent_id: 'a' }),
    node({ id: 'c', path: 'a/b/c', parent_id: 'b' }),
  ]

  it('뿌리부터 자기 자신까지', () => {
    expect(ancestorsOf(nodes, 'c').map((n) => n.id)).toEqual(['a', 'b', 'c'])
  })

  it('최상위 문서는 자기 자신뿐이다', () => {
    expect(ancestorsOf(nodes, 'a').map((n) => n.id)).toEqual(['a'])
  })

  it('없는 문서는 빈 길이다', () => {
    expect(ancestorsOf(nodes, 'nope')).toEqual([])
  })

  it('데이터가 순환해도 멈추지 않는다', () => {
    const cyclic = [
      node({ id: 'x', path: 'x', parent_id: 'y' }),
      node({ id: 'y', path: 'y', parent_id: 'x' }),
    ]
    expect(ancestorsOf(cyclic, 'x').map((n) => n.id)).toEqual(['y', 'x'])
  })
})
