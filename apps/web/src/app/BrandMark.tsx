export function BrandMark({ className = '' }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`grid size-8 shrink-0 place-items-center rounded-lg bg-accent text-white ${className}`}
    >
      <svg viewBox="0 0 32 32" className="size-5" fill="none">
        <path d="M13 11h-2.5a5 5 0 0 0 0 10H14a5 5 0 0 0 5-5m0 5h2.5a5 5 0 0 0 0-10H18a5 5 0 0 0-5 5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  )
}
