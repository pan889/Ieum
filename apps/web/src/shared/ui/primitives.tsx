import clsx from 'clsx'
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'
import { forwardRef, useId } from 'react'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  loading?: boolean
}

const VARIANTS: Record<NonNullable<ButtonProps['variant']>, string> = {
  primary: 'bg-accent text-accent-fg hover:opacity-90',
  secondary: 'bg-surface-raised text-fg border border-border hover:bg-bg',
  ghost: 'text-muted hover:text-fg hover:bg-surface-raised',
  danger: 'bg-danger text-white hover:opacity-90',
}

export function Button({
  variant = 'primary',
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
        'inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2',
        'text-sm font-medium transition-opacity',
        'disabled:cursor-not-allowed disabled:opacity-50',
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
}

export const Field = forwardRef<HTMLInputElement, FieldProps>(function Field(
  { label, hint, error, className, ...rest },
  ref,
) {
  const id = useId()
  const describedBy = [hint ? `${id}-hint` : null, error ? `${id}-error` : null]
    .filter(Boolean)
    .join(' ')

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-fg">
        {label}
      </label>
      <input
        ref={ref}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={clsx(
          'rounded-md border bg-surface px-3 py-2 text-sm text-fg',
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

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={clsx(
        'rounded-card border border-border bg-surface p-6 shadow-sm',
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
        className={clsx(
          'rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg',
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
      className={clsx(
        'rounded-full border px-2.5 py-1 text-xs font-medium transition-colors',
        pressed
          ? 'border-accent bg-accent text-accent-fg'
          : 'border-border bg-surface text-muted hover:text-fg',
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
  const TONES: Record<string, string> = {
    neutral: 'bg-surface-raised text-muted',
    todo: 'bg-surface-raised text-muted',
    in_progress: 'bg-accent/15 text-accent',
    done: 'bg-success/15 text-success',
    // 워크플로우 카테고리가 아닌 자리에도 배지가 필요하다(계정 상태 등).
    // `in_progress` 를 재사용하면 색은 맞지만 이름이 거짓말을 한다.
    info: 'bg-accent/15 text-accent',
    danger: 'bg-danger/15 text-danger',
  }
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium',
        TONES[tone] ?? TONES['neutral'],
        className,
      )}
      {...rest}
    />
  )
}
