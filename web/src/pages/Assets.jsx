import { useEffect, useState } from 'react'
import { api } from '../lib/api'

export default function Assets({ project }) {
  const [rows, setRows] = useState([])
  const [query, setQuery] = useState('')
  const [tags, setTags] = useState('')
  const [layer, setLayer] = useState('')
  const [scope, setScope] = useState('project')
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const [uploadTags, setUploadTags] = useState('')
  const [uploadLayer, setUploadLayer] = useState('L1')
  const [uploadNote, setUploadNote] = useState('')

  async function load() {
    try {
      const data = await api.assets({ project, query, tags, layer, scope })
      setRows(data.assets)
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => { load() }, [project, scope])

  async function upload(file) {
    if (!file) return
    setBusy(true)
    try {
      await api.upload(project, file, {
        tags: uploadTags, layer: uploadLayer, note: uploadNote,
      })
      setUploadNote('')
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <div className="toolbar">
        <form className="search" onSubmit={e => { e.preventDefault(); load() }}>
          <input value={query} onChange={e => setQuery(e.target.value)}
                 placeholder="搜索备注 / 标签 / 文件名" />
          <input value={tags} onChange={e => setTags(e.target.value)}
                 placeholder="标签，逗号分隔" className="narrow" />
          <input value={layer} onChange={e => setLayer(e.target.value)}
                 placeholder="L1" className="tiny" />
          <button type="submit">搜索</button>
        </form>

        <label className="check">
          <input type="checkbox" checked={scope === 'all'}
                 onChange={e => setScope(e.target.checked ? 'all' : 'project')} />
          跨项目
        </label>

        <div className="spacer" />
        <span className="muted">{rows.length} 条</span>
      </div>

      <div className="uploadbar">
        <label className={`upload ${busy ? 'busy' : ''}`}>
          {busy ? '入库中…' : '＋ 上传素材'}
          <input type="file" hidden onChange={e => upload(e.target.files?.[0])} />
        </label>
        <input value={uploadTags} onChange={e => setUploadTags(e.target.value)}
               placeholder="标签 (实拍,包装)" />
        <input value={uploadLayer} onChange={e => setUploadLayer(e.target.value)}
               placeholder="成本层 (L1)" className="tiny" />
        <input value={uploadNote} onChange={e => setUploadNote(e.target.value)}
               placeholder="备注" className="grow" />
      </div>

      {error && <div className="error" onClick={() => setError('')}>{error}</div>}

      <div className="grid">
        {rows.map(a => (
          <article key={a.id} className="card" onClick={() => setSelected(a)}>
            <div className="thumb">
              {a.thumb_url
                ? <img src={a.thumb_url} alt="" loading="lazy" />
                : <span className="placeholder">{a.kind}</span>}
              <span className={`badge ${a.kind}`}>{a.kind}</span>
              {a.layer && <span className="badge layer">{a.layer}</span>}
            </div>
            <div className="meta">
              <div className="note">{a.note || a.rel_path.split('/').pop()}</div>
              <div className="tags">
                {(a.tags || []).map(t => <span key={t} className="tag">{t}</span>)}
              </div>
              {scope === 'all' && <div className="project">{a.project}</div>}
            </div>
          </article>
        ))}
        {!rows.length && <div className="empty">没有素材。上传一个，或换个搜索词。</div>}
      </div>

      {selected && (
        <div className="modal" onClick={() => setSelected(null)}>
          <div className="modal-body" onClick={e => e.stopPropagation()}>
            <button className="close" onClick={() => setSelected(null)}>×</button>
            <div className="preview">
              {selected.kind === 'image' && <img src={selected.file_url} alt="" />}
              {selected.kind === 'video' && <video src={selected.file_url} controls autoPlay />}
              {selected.kind === 'audio' && <audio src={selected.file_url} controls autoPlay />}
            </div>
            <div className="detail">
              <h3>{selected.note || selected.rel_path.split('/').pop()}</h3>
              <dl>
                <dt>项目</dt><dd>{selected.project}</dd>
                <dt>类型</dt><dd>{selected.kind} · {selected.layer || '—'} · {selected.grade || '—'}</dd>
                <dt>标签</dt><dd>{(selected.tags || []).join(' / ') || '—'}</dd>
                <dt>使用</dt><dd>{selected.usage_count} 次</dd>
                <dt>路径</dt><dd className="mono">{selected.abs_path}</dd>
              </dl>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
