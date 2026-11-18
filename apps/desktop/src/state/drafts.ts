import type { ArtifactRef } from '@/transport/operations'

const key = (scope: string, spaceId: string) => `amsg.draft.${scope}.${spaceId}`

export interface Draft {
  text: string
  artifacts: ArtifactRef[]
}

export function loadDraft(scope: string, spaceId: string): Draft {
  try {
    const raw = localStorage.getItem(key(scope, spaceId))
    if (raw) return JSON.parse(raw)
  } catch {
    /* Nothing stored. */
  }
  return { text: '', artifacts: [] }
}

export function saveDraft(scope: string, spaceId: string, draft: Draft): void {
  try {
    if (!draft.text && !draft.artifacts.length) localStorage.removeItem(key(scope, spaceId))
    else localStorage.setItem(key(scope, spaceId), JSON.stringify(draft))
  } catch {
    /* A full store only loses the draft on restart. */
  }
}
