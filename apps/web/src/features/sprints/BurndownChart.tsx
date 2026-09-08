import { useTranslation } from 'react-i18next'

import type { BurndownPoint } from '@ieum/api-client'

import { plot, toPolyline } from './burndown'

const WIDTH = 560
const HEIGHT = 200
const PAD = { left: 34, right: 12, top: 12, bottom: 26 }

interface Props {
  points: BurndownPoint[]
  /** 끝나기로 한 날 (`YYYY-MM-DD`). 없으면 계획선을 그리지 않는다. */
  endsOn?: string | undefined
}

/**
 * 번다운 그림.
 *
 * 라이브러리를 안 쓴다 — 선 세 개와 눈금 몇 개다. 차트 라이브러리를 넣으면
 * 번들이 커지고, 이 그림이 정확히 무엇을 그리는지가 옵션 뒤로 숨는다.
 *
 * **그림만으로 끝내지 않는다.** 아래 표가 같은 값을 글자로 반복한다:
 * 스크린리더는 `<polyline>` 을 읽을 수 없고, 색으로만 나뉜 두 선은 색을 못
 * 가리는 사람에게 한 덩어리다.
 */
export function BurndownChart({ points, endsOn }: Props) {
  const { t, i18n } = useTranslation(['sprints'])
  const box = { width: WIDTH - PAD.left - PAD.right, height: HEIGHT - PAD.top - PAD.bottom }
  const found = plot(points, { ...box, endsOn })
  if (found === null) {
    return <p className="text-sm text-muted">{t('sprints:burndown.empty')}</p>
  }

  const dates = new Intl.DateTimeFormat(i18n.language, { month: 'numeric', day: 'numeric' })
  const short = (iso: string) => dates.format(new Date(`${iso}T00:00:00Z`))

  return (
    <div className="flex flex-col gap-3">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full"
        role="img"
        aria-label={t('sprints:burndown.title')}
      >
        <g transform={`translate(${PAD.left},${PAD.top})`}>
          {/* y 축 눈금: 0 과 꼭대기만. 촘촘한 격자는 선을 읽기 어렵게 한다. */}
          {[0, found.maxY].map((value) => (
            <g key={value}>
              <line
                x1={0}
                x2={box.width}
                y1={value === 0 ? box.height : 0}
                y2={value === 0 ? box.height : 0}
                className="stroke-border"
                strokeWidth={1}
              />
              <text
                x={-6}
                y={value === 0 ? box.height : 0}
                dy="0.32em"
                textAnchor="end"
                className="fill-muted text-[10px]"
              >
                {value}
              </text>
            </g>
          ))}

          {found.ideal === null ? null : (
            <polyline
              points={toPolyline(found.ideal)}
              fill="none"
              strokeDasharray="4 4"
              className="stroke-border"
              strokeWidth={1.5}
            />
          )}
          <polyline
            points={toPolyline(found.total)}
            fill="none"
            className="stroke-muted"
            strokeWidth={1.5}
          />
          <polyline
            points={toPolyline(found.remaining)}
            fill="none"
            className="stroke-accent"
            strokeWidth={2}
          />
          {found.remaining.map((mark) => (
            <circle key={mark.date} cx={mark.x} cy={mark.y} r={2.5} className="fill-accent">
              <title>
                {t('sprints:burndown.point', {
                  date: mark.date,
                  remaining: mark.remaining,
                  total: mark.total,
                })}
              </title>
            </circle>
          ))}

          <text x={0} y={box.height + 16} className="fill-muted text-[10px]">
            {short(found.firstDate)}
          </text>
          {found.lastDate === found.firstDate ? null : (
            <text
              x={box.width}
              y={box.height + 16}
              textAnchor="end"
              className="fill-muted text-[10px]"
            >
              {short(found.lastDate)}
            </text>
          )}
        </g>
      </svg>

      {/*
        같은 값을 글자로. 그림을 못 보는 사람에게도 번다운이 있어야 한다 —
        `aria-label` 하나로는 "몇 건이 남았는지" 를 말할 수 없다.
      */}
      <details className="text-sm">
        <summary className="cursor-pointer text-muted">{t('sprints:burndown.title')}</summary>
        <table className="mt-2 w-full text-left text-xs">
          <thead>
            <tr className="text-muted">
              <th scope="col" className="py-1 pr-3 font-medium">
                {t('sprints:burndown.axisDate')}
              </th>
              <th scope="col" className="py-1 pr-3 font-medium">
                {t('sprints:burndown.remaining')}
              </th>
              <th scope="col" className="py-1 font-medium">
                {t('sprints:burndown.total')}
              </th>
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr key={p.on_date}>
                <td className="py-1 pr-3">{p.on_date}</td>
                <td className="py-1 pr-3">{p.remaining_issues}</td>
                <td className="py-1">{p.total_issues}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      <p className="text-xs text-muted">{t('sprints:burndown.hint')}</p>
    </div>
  )
}
