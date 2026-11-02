import { useEffect, useState } from 'react'
import { api } from '../lib/api'

export default function Tasks({ project }) {
  const [board, setBoard] = useState(null)
  const [name, setName] = useState('')
  const [brief, setBrief] = useState('')
  const [error, setError] = useState('')

  async function load() {
    try {
      setBoard(await api.board(project))
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => { load() }, [project])

  async function create(e) {
    e.preventDefault()
    if (!name.trim()) return
    try {
      await api.createTask(project, name.trim(), brief.trim())
      setName('')
      setBrief('')
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function move(task, status) {
    try {
      await api.setStatus(project, task.name, status, '看板')
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  const statuses = board?.statuses || ['待素材', '待初剪', '待审', '待发', '已发']

  return (
    <div className="page">
      <form className="uploadbar" onSubmit={create}>
        <input value={name} onChange={e => setName(e.target.value)}
               placeholder="新任务名 (Task8)" required />
        <input value={brief} onChange={e => setBrief(e.target.value)}
               placeholder="一句话说明" className="grow" />
        <button type="submit">新建任务</button>
      </form>

      {error && <div className="error" onClick={() => setError('')}>{error}</div>}

      <div className="board">
        {statuses.map(status => (
          <section key={status} className="column">
            <h4>{status}<span>{(board?.columns?.[status] || []).length}</span></h4>
            {(board?.columns?.[status] || []).map(task => (
              <article key={task.id} className="task">
                <div className="task-name">{task.name}</div>
                {task.brief && <div className="task-brief">{task.brief}</div>}
                <div className="task-foot">
                  <span className="muted">{task.updated_by || '—'}</span>
                  <select value={task.status}
                          onChange={e => move(task, e.target.value)}>
                    {statuses.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
              </article>
            ))}
            {!(board?.columns?.[status] || []).length && (
              <div className="column-empty">—</div>
            )}
          </section>
        ))}
      </div>
    </div>
  )
}
