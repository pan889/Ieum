import clsx from 'clsx'
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
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
  ...rest
}: ButtonProps) {
  return (
    <button
      // 네이티브 button 을 쓴다. div+onClick 은 키보드로 못 쓴다.
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
