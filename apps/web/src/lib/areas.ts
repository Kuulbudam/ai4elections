export type Assignment = { id: string; user_id: string; level: 'state' | 'lga' | 'ward' | 'polling_unit'; state: string; lga: string | null; ward: string | null; pu_code: string | null }

export const LEVELS: Record<string, string> = { state: 'State', lga: 'LGA', ward: 'Ward', polling_unit: 'Polling unit' }
export const areaLabel = (a: Assignment) => [a.state, a.lga, a.ward, a.pu_code].filter(Boolean).join(' / ')
