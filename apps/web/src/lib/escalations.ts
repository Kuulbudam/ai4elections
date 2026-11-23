import type { Schemas } from './api'

export type Escalation = Schemas['Escalation']
export const STATUS: Record<string, string> = { open: 'Open', resolved: 'Resolved' }
export const EVENT: Record<string, string> = { raised: 'Escalated', sent_up: 'Sent up', resolved: 'Resolved', comment: 'Comment' }
export const areaText = (a: Record<string, string>) => [a.state, a.lga, a.ward, a.pu_code].filter(Boolean).join(' / ')
