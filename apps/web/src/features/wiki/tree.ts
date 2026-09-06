/**
 * 평평한 문서 목록 → 트리.
 *
 * 서버는 경로 순으로 정렬된 평평한 목록을 준다. 깊이마다 질의를 내면 트리
 * 깊이만큼 왕복하므로 한 번에 받아 여기서 조립한다.
 */

import type { PageNode } from '@ieum/api-client'

export interface TreeNode extends PageNode {
  children: TreeNode[]
  depth: number
}

/**
 * 부모-자식으로 엮는다. 부모가 목록에 없는 문서는 **최상위로 올린다**.
 *
 * 제한 때문에 중간 가지가 빠질 수 있는데, 그때 자식을 버리면 볼 수 있는
 * 문서가 화면에서 사라진다. 서버는 가지째 빼 주지만(그 경우 자식도 없다),
 * 목록이 잘렸거나 하는 다른 이유로 부모가 없을 수도 있다.
 */
export function buildTree(nodes: PageNode[]): TreeNode[] {
  const byId = new Map<string, TreeNode>(
    nodes.map((node) => [node.id, { ...node, children: [], depth: 0 }]),
  )
  const roots: TreeNode[] = []

  for (const node of nodes) {
    const entry = byId.get(node.id)
    if (!entry) continue
    const parent = node.parent_id ? byId.get(node.parent_id) : undefined
    if (parent) parent.children.push(entry)
    else roots.push(entry)
  }

  const stamp = (list: TreeNode[], depth: number): void => {
    // 서버가 준 순서(position → title)를 유지한다. 여기서 다시 정렬하면
    // 사용자가 정한 순서가 무시된다.
    for (const node of list) {
      node.depth = depth
      stamp(node.children, depth + 1)
    }
  }
  stamp(roots, 0)
  return roots
}

/** 트리를 화면에 그릴 순서대로 편다. 접힌 가지는 건너뛴다. */
export function flatten(nodes: TreeNode[], collapsed: ReadonlySet<string>): TreeNode[] {
  const out: TreeNode[] = []
  const walk = (list: TreeNode[]): void => {
    for (const node of list) {
      out.push(node)
      if (!collapsed.has(node.id)) walk(node.children)
    }
  }
  walk(nodes)
  return out
}

/** 이 문서까지 오는 길. 빵부스러기가 쓴다. */
export function ancestorsOf(nodes: PageNode[], pageId: string): PageNode[] {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const chain: PageNode[] = []
  let current = byId.get(pageId)
  // 순환은 서버가 막지만, 데이터가 깨져도 화면이 멈추지는 않게 한다.
  const seen = new Set<string>()
  while (current && !seen.has(current.id)) {
    seen.add(current.id)
    chain.unshift(current)
    current = current.parent_id ? byId.get(current.parent_id) : undefined
  }
  return chain
}
