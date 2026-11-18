import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { dismissToast, useToasts } from '@/state/toasts'
import { Symbol } from './Symbol'
import type { SymbolName } from './symbols.gen'
import styles from './ui.module.css'

type Variant = 'primary' | 'danger' | 'outline' | 'quiet'

export function Button({
  variant = 'quiet',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  const tone = variant === 'quiet' ? '' : styles[variant]
  return <button type="button" className={`${styles.button} ${tone} ${className}`} {...props} />
}

export function IconButton({
  icon,
  label,
  size = 18,
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: SymbolName; label: string; size?: number }) {
  return (
    <Tooltip text={label}>
      <button type="button" aria-label={label} className={`${styles.iconButton} ${className}`} {...props}>
        <Symbol name={icon} size={size} />
      </button>
    </Tooltip>
  )
}

export function Field({ label, children, group }: { label: string; children: ReactNode; group?: boolean }) {
  const id = useId()
  if (group) {
    // A label element would pass clicks on its text to the first button inside.
    return (
      <div className={styles.field} role="group" aria-labelledby={id}>
        <span id={id} className={styles.fieldLabel}>
          {label}
        </span>
        {children}
      </div>
    )
  }
  return (
    <label className={styles.field}>
      <span className={styles.fieldLabel}>{label}</span>
      {children}
    </label>
  )
}

export const inputClass = styles.input
export const textareaClass = styles.textarea
export const selectClass = styles.select
export const monoClass = styles.mono

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div className={styles.segmented} role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          className={styles.segment}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export interface MenuEntry {
  label?: string
  icon?: SymbolName
  danger?: boolean
  disabled?: boolean
  checked?: boolean
  rule?: boolean
  onSelect?: () => void
}

export function Menu({
  anchor,
  items,
  label,
  onClose,
}: {
  anchor: { x: number; y: number; bottom?: number; right?: number }
  items: MenuEntry[]
  label: string
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [style, setStyle] = useState<React.CSSProperties>({ visibility: 'hidden', left: 0, top: 0 })

  useLayoutEffect(() => {
    const menu = ref.current!
    const { width, height } = menu.getBoundingClientRect()
    const below = anchor.bottom ?? anchor.y
    let left = anchor.right !== undefined ? anchor.right - width : anchor.x
    let top = below + 4
    if (top + height > innerHeight - 8) top = Math.max(8, anchor.y - height - 4)
    left = Math.max(8, Math.min(left, innerWidth - width - 8))
    setStyle({ left, top })
  }, [anchor])

  // Focus waits for the positioned render: a hidden element cannot take focus.
  const placed = style.visibility !== 'hidden'
  useEffect(() => {
    if (placed) ref.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus({ preventScroll: true })
  }, [placed])

  useEffect(() => {
    const away = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) onClose()
    }
    window.addEventListener('pointerdown', away, true)
    window.addEventListener('blur', onClose)
    return () => {
      window.removeEventListener('pointerdown', away, true)
      window.removeEventListener('blur', onClose)
    }
  }, [onClose])

  const onKey = (event: KeyboardEvent) => {
    const buttons = [...ref.current!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')]
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
    const go = (next: number) => {
      event.preventDefault()
      buttons[(next + buttons.length) % buttons.length]?.focus()
    }
    if (event.key === 'ArrowDown') go(index + 1)
    else if (event.key === 'ArrowUp') go(index - 1)
    else if (event.key === 'Home') go(0)
    else if (event.key === 'End') go(buttons.length - 1)
    else if (event.key === 'Tab') event.preventDefault()
    else if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      onClose()
    }
  }

  return createPortal(
    <div ref={ref} className={styles.menu} role="menu" aria-label={label} style={style} onKeyDown={onKey}>
      {items.map((item, index) =>
        item.rule ? (
          <div key={index} className={styles.menuRule} role="separator" />
        ) : (
          <button
            key={index}
            type="button"
            role={item.checked === undefined ? 'menuitem' : 'menuitemradio'}
            aria-checked={item.checked}
            disabled={item.disabled}
            className={`${styles.menuItem} ${item.danger ? styles.menuDanger : ''}`}
            onClick={() => {
              onClose()
              item.onSelect?.()
            }}
          >
            {item.icon && <Symbol name={item.icon} size={18} />}
            <span>{item.label}</span>
            {item.checked && (
              <span className={styles.menuCheck}>
                <Symbol name="check" size={16} />
              </span>
            )}
          </button>
        ),
      )}
    </div>,
    document.body,
  )
}

export function useMenu() {
  const [anchor, setAnchor] = useState<{ x: number; y: number; bottom?: number; right?: number } | null>(null)
  const opener = useRef<HTMLElement | null>(null)
  const open = useCallback((event: React.MouseEvent<HTMLElement>, align: 'left' | 'right' = 'right') => {
    opener.current = event.currentTarget
    const rect = event.currentTarget.getBoundingClientRect()
    setAnchor(
      align === 'right'
        ? { x: rect.left, y: rect.top, bottom: rect.bottom, right: rect.right }
        : { x: rect.left, y: rect.top, bottom: rect.bottom },
    )
  }, [])
  const openAt = useCallback((x: number, y: number, target?: HTMLElement) => {
    opener.current = target ?? (document.activeElement as HTMLElement)
    setAnchor({ x, y })
  }, [])
  const close = useCallback(() => {
    setAnchor(null)
    opener.current?.focus?.()
  }, [])
  return { anchor, open, openAt, close }
}

export function Sheet({
  title,
  width,
  onClose,
  children,
  footer,
}: {
  title: ReactNode
  width?: number
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const dialog = ref.current!
    if (!dialog.open) dialog.showModal()
    return () => dialog.close()
  }, [])
  return (
    <dialog
      ref={ref}
      className={styles.sheetDialog}
      aria-labelledby={titleId}
      style={width ? ({ '--sheet-width': `${width}px` } as React.CSSProperties) : undefined}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
    >
      <div className={styles.sheetHead}>
        <h2 id={titleId} className={styles.sheetTitle}>
          {title}
        </h2>
        <IconButton icon="close" label="Close" onClick={onClose} />
      </div>
      <div className={styles.sheetBody}>{children}</div>
      {footer && <div className={styles.sheetFoot}>{footer}</div>}
    </dialog>
  )
}

export function Tooltip({
  text,
  detail,
  side = 'auto',
  children,
}: {
  text: string
  detail?: string
  side?: 'auto' | 'left' | 'right' | 'top' | 'bottom'
  children: React.ReactElement<any>
}) {
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const tip = useRef<HTMLDivElement>(null)
  const anchor = useRef<DOMRect | null>(null)
  const hide = () => {
    clearTimeout(timer.current)
    setPosition(null)
  }
  const show = (event: { currentTarget: HTMLElement }, delay: number) => {
    const target = event.currentTarget
    clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      anchor.current = target.getBoundingClientRect()
      setPosition({ left: -9999, top: -9999 })
    }, delay)
  }
  useEffect(() => () => clearTimeout(timer.current), [])
  useLayoutEffect(() => {
    if (!position || position.left !== -9999 || !anchor.current || !tip.current) return
    const a = anchor.current
    const t = tip.current.getBoundingClientRect()
    const gap = 8
    const where = side === 'auto' ? (a.top - t.height - gap > 8 ? 'top' : 'bottom') : side
    let left = a.left + a.width / 2 - t.width / 2
    let top = a.top - t.height - gap
    if (where === 'bottom') top = a.bottom + gap
    if (where === 'right') [left, top] = [a.right + gap, a.top + a.height / 2 - t.height / 2]
    if (where === 'left') [left, top] = [a.left - t.width - gap, a.top + a.height / 2 - t.height / 2]
    setPosition({
      left: Math.max(8, Math.min(left, innerWidth - t.width - 8)),
      top: Math.max(8, Math.min(top, innerHeight - t.height - 8)),
    })
  }, [position, side])
  const child = children
  return (
    <>
      <child.type
        {...child.props}
        ref={(child as any).ref}
        onPointerEnter={(event: React.PointerEvent<HTMLElement>) => {
          child.props.onPointerEnter?.(event)
          if (event.pointerType === 'mouse') show(event, 450)
        }}
        onPointerLeave={(event: React.PointerEvent<HTMLElement>) => {
          child.props.onPointerLeave?.(event)
          hide()
        }}
        onPointerDown={(event: React.PointerEvent<HTMLElement>) => {
          child.props.onPointerDown?.(event)
          hide()
        }}
        onFocus={(event: React.FocusEvent<HTMLElement>) => {
          child.props.onFocus?.(event)
          if (event.target.matches(':focus-visible')) show(event, 0)
        }}
        onBlur={(event: React.FocusEvent<HTMLElement>) => {
          child.props.onBlur?.(event)
          hide()
        }}
      />
      {position &&
        createPortal(
          <div ref={tip} className={styles.tip} role="tooltip" style={position}>
            {text}
            {detail && <span className={styles.tipDetail}>{detail}</span>}
          </div>,
          document.body,
        )}
    </>
  )
}

export function Toasts() {
  const toasts = useToasts()
  return (
    <div className={styles.toasts} role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`${styles.toast} ${toast.tone === 'error' ? styles.toastError : ''}`}>
          <span>{toast.text}</span>
          {toast.action && (
            <button
              type="button"
              className={styles.toastAction}
              onClick={() => {
                toast.action!.run()
                dismissToast(toast.id)
              }}
            >
              {toast.action.label}
            </button>
          )}
        </div>
      ))}
    </div>
  )
}
