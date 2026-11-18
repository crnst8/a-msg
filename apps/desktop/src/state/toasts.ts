import { useSyncExternalStore } from 'react'

export interface Toast {
  id: number
  text: string
  tone?: 'error'
  action?: { label: string; run: () => void }
}

let toasts: Toast[] = []
let next = 1
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((listener) => listener())

export function dismissToast(id: number): void {
  toasts = toasts.filter((toast) => toast.id !== id)
  emit()
}

export function toast(text: string, options: Omit<Toast, 'id' | 'text'> = {}): void {
  const id = next++
  toasts = [...toasts.slice(-2), { id, text, ...options }]
  emit()
  setTimeout(() => dismissToast(id), options.action ? 7000 : 4000)
}

export function reportError(error: unknown): void {
  toast(error instanceof Error ? error.message : 'Something went wrong.', { tone: 'error' })
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => toasts,
  )
}
