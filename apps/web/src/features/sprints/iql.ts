/**
 * 스프린트를 IQL 로 묻는 문장.
 *
 * 화면이 손으로 문자열을 붙이면 **이스케이프를 빠뜨리는 자리가 늘어난다.**
 * 여기 한 곳에 모으고 시험을 붙인다.
 */

/**
 * IQL 큰따옴표 문자열 안에 넣을 수 있게 만든다.
 *
 * **역슬래시를 먼저** 늘려야 한다. 따옴표를 먼저 처리하면 그때 붙인
 * 역슬래시까지 한 번 더 늘어나서, 값이 원래와 달라진다.
 *
 * 프로젝트 키와 달리 스프린트 이름에는 형식을 강제할 수 없다 — 사람이 짓는
 * 이름이라 따옴표가 들어온다.
 */
export function quote(value: string): string {
  return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`
}

/** 그 스프린트에 들어 있는 것. */
export function inSprint(projectKey: string, sprintName: string): string {
  return `project = ${projectKey} AND sprint = ${quote(sprintName)}`
}

/** 백로그 — 어느 스프린트에도 없는 것. */
export function inBacklog(projectKey: string): string {
  return `project = ${projectKey} AND sprint IS EMPTY`
}
