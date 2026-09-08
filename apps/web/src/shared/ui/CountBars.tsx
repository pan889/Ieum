/**
 * IQL 집계를 막대로 (A29 리포트, B9b 차트 디렉티브).
 *
 * 리포트 화면과 문서 안의 `::chart` 가 **같은 것을 그린다.** 각자 그리면
 * 아래 규칙 중 하나가 한쪽에서만 지켜지는 날이 오고, 그날 두 화면이 같은
 * 질의에 다른 뜻을 말한다.
 *
 * 규칙 셋:
 *
 * 1. **색만으로 뜻을 전하지 않는다** (ux-principles 5절). 칸마다 이름과 수를
 *    글자로 적는다. 막대는 크기를 눈으로 견주는 보조일 뿐이고
 *    `aria-hidden` 이다 — 화면 낭독기에는 표의 이름·수만 읽힌다.
 * 2. **합이 총계와 다를 수 있으면 그 이유를 적는다.** 라벨처럼 이슈 하나가
 *    여러 칸에 드는 기준이면 합이 크고, 칸 상한에서 잘리면 합이 작다.
 *    안 적으면 사람은 숫자가 안 맞는 것을 버그로 보고, 그다음부터 리포트를
 *    안 믿는다.
 * 3. **값이 없는 것들도 하나의 칸이다.** 빈 이름으로 그리면 "담당자 없음
 *    12건" 이 이름 없는 줄로 보인다.
 */

import { useTranslation } from 'react-i18next'

import type { CountReport, ReportBucket } from '@ieum/api-client'

import { Alert } from './primitives'

export interface CountBarsProps {
  report: CountReport
  /** 칸 이름. 담당자 칸의 키는 UUID 라서 부르는 쪽이 이름을 붙인다. */
  label: (bucket: ReportBucket) => string
  /**
   * 그릴 막대 수. 넘는 것은 안 그리고 **몇 개를 안 그렸는지 적는다.**
   *
   * 문서 안에서 필요하다: 담당자로 묶으면 사람 수만큼 막대가 나오고 문서
   * 한가운데가 몇 화면 밀린다. 리포트 화면은 이 값을 안 넘긴다 — 그 화면은
   * 집계를 보러 간 자리이므로 다 보여야 한다.
   */
  bars?: number | undefined
}

export function CountBars({ report, label, bars }: CountBarsProps) {
  const { t } = useTranslation(['reports'])
  const shown = bars === undefined ? report.buckets : report.buckets.slice(0, bars)
  const hidden = report.buckets.length - shown.length
  // 0 으로 나누지 않는다. 칸이 하나라도 있으면 그 칸이 100% 다.
  const biggest = Math.max(1, ...shown.map((b) => b.count))

  return (
    <div className="flex flex-col gap-2">
      {/*
        **칸이 없어도 총계는 적는다.** 칸이 0개인데 총계가 0이 아닌 경우가
        있다: 라벨로 묶으면 라벨이 없는 이슈는 집계에 행을 하나도 안 만든다.
        그때 "맞는 이슈가 없다" 만 보여 주면 5건이 0건으로 보인다.
      */}
      <p className="text-sm font-medium">{t('reports:total', { count: report.total })}</p>
      {report.multi_valued ? (
        <p className="text-xs text-muted">{t('reports:multiValued')}</p>
      ) : null}
      {report.truncated ? <Alert>{t('reports:truncated')}</Alert> : null}

      {shown.length === 0 ? (
        <p className="text-sm text-muted">{t('reports:empty')}</p>
      ) : (
        <table className="w-full text-sm">
          <tbody>
            {shown.map((bucket) => (
              <tr key={bucket.key ?? '∅'}>
                <th scope="row" className="w-40 py-1 pr-3 text-left font-normal">
                  {label(bucket)}
                </th>
                <td className="py-1">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-3 rounded bg-accent/40"
                      style={{ width: `${String((bucket.count / biggest) * 100)}%` }}
                      aria-hidden
                    />
                    <span className="tabular-nums text-xs text-muted">{bucket.count}</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {hidden > 0 ? (
        <p className="text-xs text-muted">{t('reports:moreBuckets', { count: hidden })}</p>
      ) : null}
    </div>
  )
}
