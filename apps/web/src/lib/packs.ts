import { api, type Schemas } from './api'

function bytes(b64: string): Uint8Array<ArrayBuffer> {
  const s = atob(b64)
  const out = new Uint8Array(new ArrayBuffer(s.length))
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i)
  return out
}

/** Fetch a pack's zip and save it. */
export async function downloadPack(id: string): Promise<void> {
  const d = await api<Schemas['PackDownload']>('GET', `/submission-packs/${id}/download`)
  const url = URL.createObjectURL(new Blob([bytes(d.zip_base64)], { type: 'application/zip' }))
  const a = document.createElement('a')
  a.href = url
  a.download = d.filename
  a.click()
  URL.revokeObjectURL(url)
}

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',', 2)[1] ?? '')
    r.onerror = () => reject(new Error('That file could not be read.'))
    r.readAsDataURL(file)
  })
}
