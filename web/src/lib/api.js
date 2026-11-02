async function request(url, options = {}) {
  const res = await fetch(url, options)
  let data
  try {
    data = await res.json()
  } catch {
    data = { ok: false, error: await res.text() }
  }
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || data.detail || `请求失败 (${res.status})`)
  }
  return data
}

const json = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  projects: () => request('/api/projects'),

  createProject: (name) => request('/api/projects', json('POST', { name })),

  assets: ({ project, query = '', tags = '', scope = 'project', layer = '' } = {}) => {
    const params = new URLSearchParams()
    if (project) params.set('project', project)
    if (query) params.set('query', query)
    if (tags) params.set('tags', tags)
    if (layer) params.set('layer', layer)
    params.set('scope', scope)
    return request(`/api/assets?${params}`)
  },

  upload: (project, file, meta = {}) => {
    const form = new FormData()
    form.append('project', project)
    form.append('file', file)
    for (const [k, v] of Object.entries(meta)) form.append(k, v ?? '')
    return request('/api/assets', { method: 'POST', body: form })
  },

  patchAsset: (id, body) => request(`/api/assets/${id}`, json('PATCH', body)),

  board: (project) =>
    request(`/api/board?${new URLSearchParams({ project })}`),

  createTask: (project, name, brief = '') =>
    request('/api/tasks', json('POST', { project, name, brief })),

  setStatus: (project, name, status, by = '') =>
    request(`/api/tasks/${encodeURIComponent(project)}/${encodeURIComponent(name)}`,
      json('PATCH', { status, by })),
}
