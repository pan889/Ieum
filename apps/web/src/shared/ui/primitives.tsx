import clsx from 'clsx'
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  Ref,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'
import { forwardRef, useId } from 'react'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
}

const VARIANTS: Record<NonNullable<ButtonProps['variant']>, string> = {
  primary: 'bg-accent text-accent-fg shadow-raised hover:brightness-110 active:brightness-95',
  // 보조 단추는 **표면 위에 놓인 단추**로 보여야 한다. 전에는 배경보다
  // 밝은 회색이라 눌리는 것인지 그냥 칸인지 구별이 안 됐다.
  secondary:
    'bg-surface text-fg border border-border-strong shadow-raised hover:bg-surface-raised active:bg-sunken',
  ghost: 'text-muted hover:bg-surface-raised hover:text-fg active:bg-sunken',
  danger: 'bg-danger text-white shadow-raised hover:brightness-110 active:brightness-95',
}

const SIZES: Record<NonNullable<ButtonProps['size']>, string> = {
  sm: 'h-7 gap-1.5 px-2.5 text-xs',
  md: 'h-8 gap-2 px-3 text-sm',
  lg: 'h-10 gap-2 px-4 text-base',
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  disabled,
  className,
  children,
  // 폼 안의 button 은 기본이 submit 이다. 그래서 폼 안에 놓은 보조 버튼이
  // 자기 일도 하고 저장도 해 버린다 — 초안 "버리기" 가 버리면서 저장했다.
  // 제출하려는 버튼은 그렇다고 말하게 한다.
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      // 네이티브 button 을 쓴다. div+onClick 은 키보드로 못 쓴다.
      type={type}
      className={clsx(
        'inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-md',
        'font-medium transition-[background-color,filter,box-shadow] duration-100',
        'disabled:cursor-not-allowed disabled:opacity-45 disabled:shadow-none',
        SIZES[size],
        VARIANTS[variant],
        className,
      )}
      disabled={disabled === true || loading}
      aria-busy={loading}
      {...rest}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  )
}

function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
    />
  )
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string
  hint?: string
  error?: string | undefined
  /**
   * 라벨을 **접근성 트리에만** 둔다. 라벨을 아예 빼는 것과 다르다 — 낭독기
   * 사용자는 이름 없는 상자를 만나면 무엇을 적는 칸인지 알 수 없다.
   *
   * 도구 막대에 놓인 상자가 이것을 쓴다. 한 줄에 선 것들 사이에서 이 칸만
   * 라벨을 위로 세우면 그 글자가 아무것도 안 붙은 채 허공에 뜬다 — 이슈
   * 목록의 "Text" 가 정확히 그랬다.
   */
  labelHidden?: boolean
}

export const Field = forwardRef<HTMLInputElement, FieldProps>(function Field(
  { label, hint, error, labelHidden = false, className, ...rest },
  ref,
) {
  const id = useId()
  const describedBy = [hint ? `${id}-hint` : null, error ? `${id}-error` : null]
    .filter(Boolean)
    .join(' ')

  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={id}
        className={labelHidden ? 'sr-only' : 'text-sm font-medium text-fg'}
      >
        {label}
      </label>
      <input
        ref={ref}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={clsx(
          'h-8 rounded-md border bg-surface px-2.5 text-sm text-fg',
          'placeholder:text-subtle',
          error ? 'border-danger' : 'border-border-strong',
          className,
        )}
        {...rest}
      />
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  )
})

type CheckboxProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  label: string
  hint?: string
}

/**
 * 체크박스 하나.
 *
 * **힌트를 라벨 안에 넣지 않는다.** 손으로 조립하면 그렇게 되기 쉽고, 그러면
 * 접근성 이름이 "SSL 로 접속 끄면 비밀번호가 평문으로…" 처럼 두 문장을 이어
 * 붙인 것이 된다 — 이름으로 찾는 모든 것이(스크린 리더도, 브라우저 시험도)
 * 그 칸을 못 찾는다. `Field` 와 같은 방식으로 `aria-describedby` 로 잇는다.
 *
 * 이 프리미티브가 없어서 화면마다 손으로 짰고, 매번 조금씩 다르게 틀렸다.
 */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, hint, className, ...rest },
  ref,
) {
  const id = useId()
  return (
    <div className="flex flex-col gap-0.5">
      <span className="flex items-center gap-2">
        <input
          ref={ref}
          id={id}
          type="checkbox"
          aria-describedby={hint ? `${id}-hint` : undefined}
          className={className}
          {...rest}
        />
        <label htmlFor={id} className="text-sm text-fg">
          {label}
        </label>
      </span>
      {hint ? (
        <p id={`${id}-hint`} className="ml-6 text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  )
})

export function Alert({
  tone = 'danger',
  children,
}: {
  tone?: 'danger' | 'info'
  children: ReactNode
}) {
  return (
    <div
      role="alert"
      className={clsx(
        'rounded-md border px-3 py-2 text-sm',
        tone === 'danger'
          ? 'border-danger/40 bg-danger/10 text-danger'
          : 'border-border bg-surface-raised text-muted',
      )}
    >
      {children}
    </div>
  )
}

// `ref` 를 받는다 — 덮개가 열릴 때 **초점을 자기 안으로** 옮겨야 하기
// 때문이다. `aria-modal` 을 적어 두고 초점을 뒤에 남기면 화면 낭독기에는
// 아무 일도 안 일어난 것이 된다.
type CardProps = HTMLAttributes<HTMLDivElement> & { ref?: Ref<HTMLDivElement> }

export function Card({ className, ...rest }: CardProps) {
  return (
    <div
      // `p-6` 이었다. 업무 도구에서 카드 여섯 칸 여백은 한 화면에 들어가는
      // 정보를 절반으로 줄인다 — 읽을 것이 많은 화면일수록 숨 쉴 곳은
      // 카드 **사이**에 두고 안쪽은 조인다.
      className={clsx(
        'rounded-card border border-border bg-surface p-4 shadow-raised',
        className,
      )}
      {...rest}
    />
  )
}

type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  label?: string
  hint?: string
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, hint, className, children, ...rest },
  ref,
) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1.5">
      {label ? (
        <label htmlFor={id} className="text-sm font-medium text-fg">
          {label}
        </label>
      ) : null}
      <select
        ref={ref}
        id={id}
        aria-describedby={hint ? `${id}-hint` : undefined}
        // `Field` 와 **같은 높이·같은 테두리**여야 한다. 전에는 `py-2` 라
        // 40px 이었고 입력 칸은 32px 이었다 — 한 줄에 나란히 놓으면 눈에
        // 띄게 어긋났고, 이슈 상세의 오른쪽 칸이 그래서 들쭉날쭉했다.
        className={clsx(
          'h-8 rounded-md border border-border-strong bg-surface px-2 text-sm text-fg',
          className,
        )}
        {...rest}
      >
        {children}
      </select>
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  )
})

type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label?: string
  hint?: string
  error?: string | undefined
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, hint, error, className, ...rest },
  ref,
) {
  const id = useId()
  const describedBy = [hint ? `${id}-hint` : null, error ? `${id}-error` : null]
    .filter(Boolean)
    .join(' ')

  return (
    <div className="flex flex-col gap-1.5">
      {label ? (
        <label htmlFor={id} className="text-sm font-medium text-fg">
          {label}
        </label>
      ) : null}
      <textarea
        ref={ref}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={clsx(
          'min-h-24 rounded-md border bg-surface px-3 py-2 text-sm text-fg',
          'placeholder:text-muted',
          error ? 'border-danger' : 'border-border',
          className,
        )}
        {...rest}
      />
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  )
})

/**
 * 토글 칩. `aria-pressed` 를 쓴다 — 눌린 상태를 색으로만 알리면
 * 스크린리더 사용자에게는 아무 정보도 없다.
 */
export function Chip({
  pressed,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { pressed: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      // 눌린 칩이 **꽉 찬 강조색**이었다. 필터 막대에 스무 개가 늘어서면
      // 화면에서 제일 시끄러운 것이 필터가 되고, 정작 데이터가 뒤로 밀린다.
      // 눌렸다는 것만 조용히 말한다 — 색 + 테두리 + 굵기.
      className={clsx(
        'inline-flex h-7 items-center rounded-md border px-2.5 text-xs transition-colors',
        pressed
          ? 'border-accent/35 bg-accent-soft font-semibold text-accent'
          : 'border-border bg-surface font-medium text-muted hover:border-border-strong hover:text-fg',
        className,
      )}
      {...rest}
    />
  )
}

export function Badge({
  tone = 'neutral',
  className,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & {
  tone?: 'neutral' | 'todo' | 'in_progress' | 'done' | 'info' | 'danger'
}) {
  // 투명도(`/15`)로 만든 배경은 어느 면 위에 놓이냐에 따라 색이 달라진다.
  // 표 줄 위와 카드 위에서 같은 배지가 다른 색으로 보였다. 단색 토큰을 쓴다.
  const TONES: Record<string, string> = {
    neutral: 'border-border bg-sunken text-muted',
    todo: 'border-border bg-sunken text-muted',
    in_progress: 'border-accent/25 bg-accent-soft text-accent',
    done: 'border-success/25 bg-success-soft text-success',
    // 워크플로우 카테고리가 아닌 자리에도 배지가 필요하다(계정 상태 등).
    // `in_progress` 를 재사용하면 색은 맞지만 이름이 거짓말을 한다.
    info: 'border-accent/25 bg-accent-soft text-accent',
    danger: 'border-danger/25 bg-danger-soft text-danger',
  }
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded border px-1.5 py-px text-2xs font-medium',
        TONES[tone] ?? TONES['neutral'],
        className,
      )}
      {...rest}
    />
  )
}

/**
 * 화면 머리. 제목·설명·조작이 **늘 같은 자리**에 온다.
 *
 * 전에는 화면마다 `<h1>` 을 각자 놓았고, 그래서 제목 크기도 아래 여백도
 * 조작 단추의 위치도 화면마다 달랐다. 옮겨 다니는 사람은 그 흔들림을
 * "만들다 만 것" 으로 읽는다.
 */
export function PageHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <header className={clsx('flex flex-wrap items-start gap-x-4 gap-y-2', className)}>
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-xl font-semibold text-fg">{title}</h1>
        {description ? <p className="mt-0.5 text-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  )
}

/**
 * 아무것도 없을 때 놓는 자리.
 *
 * **빈 화면을 그냥 비워 두지 않는다.** 고를 프로젝트가 없어서 비어 있는
 * 것인지, 안 불러와진 것인지, 내가 뭘 잘못 눌러서인지 — 비어 있기만 하면
 * 셋을 구별할 방법이 없다. 무엇이 없는지 말하고, 다음에 할 일을 준다.
 */
export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      className={clsx(
        'flex flex-col items-center justify-center gap-1 rounded-card',
        'border border-dashed border-border bg-surface/60 px-6 py-14 text-center',
        className,
      )}
    >
      <p className="text-md font-medium text-fg">{title}</p>
      {description ? (
        <p className="max-w-sm text-balance text-sm text-muted">{description}</p>
      ) : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  )
}
