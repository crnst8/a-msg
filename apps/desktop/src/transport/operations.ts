import type { components } from '@amsg/contracts'

type S = components['schemas']
export type Space = S['SpaceOut']
export type Message = S['MessageOut']
export type MessagePage = S['MessagePage']
export type Run = S['RunOut']
export type Receipt = S['Receipt']
export type Snapshot = S['Snapshot']
export type Connection = S['ConnectionOut']
export type Automation = S['AutomationSummary']
export type ArtifactRef = S['ArtifactRef']
export type Artifact = S['ArtifactOut']
export type ArtifactPage = S['ArtifactPage']
export type Operation = S['OperationOut']
export type SearchResult = S['SearchResult']
export type SearchHit = S['SearchHit']
export type Capabilities = S['Capabilities']
export type Health = S['Health']
export type Device = S['DeviceOut']
export type ConnectionTest = S['ConnectionTest']
export type ConfigFile = S['ConfigFile']
export type ConfigPatch = S['ConfigPatchOut']
export type Icon = S['Icon']

/** The only requests the renderer can make. The native core validates this same table. */
export const ROUTES = {
  health: ['GET', '/v1/health'],
  capabilities: ['GET', '/v1/capabilities'],
  snapshot: ['GET', '/v1/snapshot'],
  listSpaces: ['GET', '/v1/spaces'],
  createSpace: ['POST', '/v1/spaces'],
  patchSpace: ['PATCH', '/v1/spaces/{id}'],
  readSpace: ['POST', '/v1/spaces/{id}/read'],
  listMessages: ['GET', '/v1/spaces/{id}/messages'],
  fork: ['POST', '/v1/spaces/{id}/fork'],
  archiveSpace: ['POST', '/v1/spaces/{id}/archive'],
  restoreSpace: ['POST', '/v1/spaces/{id}/restore'],
  deleteSpace: ['POST', '/v1/spaces/{id}/delete'],
  getOperation: ['GET', '/v1/operations/{id}'],
  retryOperation: ['POST', '/v1/operations/{id}/retry'],
  dismissOperation: ['POST', '/v1/operations/{id}/dismiss'],
  cancelRun: ['POST', '/v1/runs/{id}/cancel'],
  retryRun: ['POST', '/v1/runs/{id}/retry'],
  getQueued: ['GET', '/v1/queued-messages/{id}'],
  patchQueued: ['PATCH', '/v1/queued-messages/{id}'],
  deleteQueued: ['DELETE', '/v1/queued-messages/{id}'],
  listArtifacts: ['GET', '/v1/artifacts'],
  getArtifact: ['GET', '/v1/artifacts/{id}'],
  hideArtifact: ['POST', '/v1/artifacts/{id}/hide'],
  unhideArtifact: ['POST', '/v1/artifacts/{id}/unhide'],
  deleteArtifact: ['DELETE', '/v1/artifacts/{id}'],
  search: ['GET', '/v1/search'],
  listConnections: ['GET', '/v1/connections'],
  createConnection: ['POST', '/v1/connections'],
  testConnection: ['POST', '/v1/connections/{id}/test'],
  readConfig: ['GET', '/v1/connections/{id}/config'],
  proposePatch: ['POST', '/v1/connections/{id}/config-patches'],
  applyPatch: ['POST', '/v1/config-patches/{id}/apply'],
  rollbackPatch: ['POST', '/v1/config-patches/{id}/rollback'],
  listAutomations: ['GET', '/v1/automations'],
  automationAction: ['POST', '/v1/automations/{id}/actions'],
  listDevices: ['GET', '/v1/devices'],
  revokeDevice: ['DELETE', '/v1/devices/{id}'],
} as const

export type RouteName = keyof typeof ROUTES

type Id = { id: string }
interface Calls {
  health: [undefined, Health]
  capabilities: [undefined, Capabilities]
  snapshot: [undefined, Snapshot]
  listSpaces: [{ query?: { include_archived?: boolean } } | undefined, Space[]]
  createSpace: [{ body: S['SpaceCreate'] }, Space]
  patchSpace: [Id & { body: S['SpacePatch'] }, Space]
  readSpace: [Id, Space]
  listMessages: [Id & { query: { before?: number; limit?: number } }, MessagePage]
  fork: [Id & { body: S['ForkRequest'] }, S['ForkResult']]
  archiveSpace: [Id, Space]
  restoreSpace: [Id, Space]
  deleteSpace: [Id & { body: S['DeletePolicy'] }, Operation]
  getOperation: [Id, Operation]
  retryOperation: [Id, Operation]
  dismissOperation: [Id, Operation]
  cancelRun: [Id, Run]
  retryRun: [Id, Run]
  getQueued: [Id, Message]
  patchQueued: [Id & { body: S['QueuedMessagePatch'] }, Message]
  deleteQueued: [Id, null]
  listArtifacts: [
    { query: { q?: string; type?: string; space_id?: string; include_hidden?: boolean; offset?: number; limit?: number } },
    ArtifactPage,
  ]
  getArtifact: [Id, Artifact]
  hideArtifact: [Id, Artifact]
  unhideArtifact: [Id, Artifact]
  deleteArtifact: [Id & { query?: { confirm?: boolean } }, Artifact]
  search: [{ query: { q: string; space_id?: string; type?: string; limit?: number } }, SearchResult]
  listConnections: [undefined, Connection[]]
  createConnection: [{ body: S['ConnectionCreate'] }, Connection]
  testConnection: [Id, ConnectionTest]
  readConfig: [Id, ConfigFile]
  proposePatch: [Id & { body: S['ConfigPatchCreate'] }, ConfigPatch]
  applyPatch: [Id, ConfigPatch]
  rollbackPatch: [Id, ConfigPatch]
  listAutomations: [undefined, Automation[]]
  automationAction: [Id & { body: S['AutomationAction'] }, Automation]
  listDevices: [undefined, Device[]]
  revokeDevice: [Id, null]
}

export type CallArgs<K extends RouteName> = Calls[K][0]
export type CallResult<K extends RouteName> = Calls[K][1]

export class ServiceError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable = false,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message)
  }
}

export function resolvePath(template: string, id?: string, query?: Record<string, unknown>): string {
  let path = id === undefined ? template : template.replace('{id}', encodeURIComponent(id))
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const text = params.toString()
  if (text) path += `?${text}`
  return path
}
