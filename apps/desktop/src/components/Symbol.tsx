import { SYMBOLS, type SymbolName } from './symbols.gen'
import type { Icon } from '@/transport/operations'

export function Symbol({ name, size = 20 }: { name: SymbolName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" style={{ flex: 'none' }}>
      <path d={SYMBOLS[name]} />
    </svg>
  )
}

export function SpaceIcon({ icon, size = 24 }: { icon: Icon; size?: number }) {
  if (icon.kind === 'emoji') {
    return (
      <span aria-hidden="true" style={{ fontSize: size * 0.86, lineHeight: `${size}px`, width: size, textAlign: 'center' }}>
        {icon.value}
      </span>
    )
  }
  const name = (icon.value in SYMBOLS ? icon.value : 'thread') as SymbolName
  return <Symbol name={name} size={size} />
}
