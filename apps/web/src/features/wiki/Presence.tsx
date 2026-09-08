/**
 * 같이 보고 있는 사람들 (B16).
 *
 * **혼자일 때도 상태를 말한다.** 붙어 있는지 아닌지를 안 보여 주면, 소켓이
 * 끊긴 채로 계속 타이핑하다 나중에 "내가 쓴 게 안 갔다" 를 겪는다. 그때는
 * 이미 늦었다 — 지금 안 붙어 있다는 사실이 타이핑하는 동안 보여야 한다.
 *
 * 커서 위치도 받아 두지만 여기서는 사람 이름만 보여 준다. 본문 위에 남의
 * 커서를 그리는 것은 textarea 안에 겹쳐 그려야 하는 일이라, 소스 모드의
 * 단순함(브라우저가 주는 그대로)을 깨뜨린다. 위치는 프로토콜에 이미 실려
 * 있으므로 서식 모드에 커서를 그릴 때 서버를 고칠 필요는 없다.
 */

import { useTranslation } from 'react-i18next'

import { Badge } from '@/shared/ui/primitives'

import type { CollabStatus, Peer } from './collab'

export function Presence({ status, peers }: { status: CollabStatus; peers: Peer[] }) {
  const { t } = useTranslation(['wiki'])

  return (
    <p className="flex flex-wrap items-center gap-2 text-xs" data-testid="collab-presence">
      <Badge tone={status === 'live' ? 'done' : status === 'connecting' ? 'neutral' : 'danger'}>
        {t(`wiki:collab.status.${status}`)}
      </Badge>
      {peers.length === 0 ? (
        <span className="text-muted">{t('wiki:collab.alone')}</span>
      ) : (
        <>
          <span className="text-muted">{t('wiki:collab.withMe', { count: peers.length })}</span>
          {peers.map((peer) => (
            <span
              key={peer.clientId}
              className="rounded bg-accent/15 px-1.5 py-0.5"
              data-testid="collab-peer"
            >
              {peer.name}
            </span>
          ))}
        </>
      )}
    </p>
  )
}
