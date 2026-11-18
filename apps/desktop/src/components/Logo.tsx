import { LOGO, LOGO_SMALL } from './logo.gen'

/** The a-msg symbol in the current text colour. Sizes of 32 px and below use the heavier small cut. */
export function Logo({ size = 44, label, className }: { size?: number; label?: string; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 256 256"
      fill="currentColor"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={{ flex: 'none' }}
    >
      <path fillRule="evenodd" d={size <= 32 ? LOGO_SMALL : LOGO} />
    </svg>
  )
}
