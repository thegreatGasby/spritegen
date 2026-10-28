async function req(url, opts) {
  const r = await fetch(url, opts)
  if (!r.ok) {
    let detail = r.statusText
    try { detail = (await r.json()).detail || detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  return r.json()
}

const post = (url, body) => req(url, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
})

export const getPresets = () => req('/api/presets')
export const getCharacters = () => req('/api/characters')
export const getGallery = () => req('/api/gallery')
export const getJob = (id) => req(`/api/jobs/${id}`)
export const createCharacter = (body) => post('/api/characters', body)
export const createAction = (body) => post('/api/actions', body)
export const createBatch = (body) => post('/api/batch', body)
export const createParts = (body) => post('/api/parts', body)
