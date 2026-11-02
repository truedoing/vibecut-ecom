import { useEffect, useState } from 'react'
import { api } from './lib/api'
import Assets from './pages/Assets'
import Tasks from './pages/Tasks'

export default function App() {
  const [tab, setTab] = useState('assets')
  const [projects, setProjects] = useState([])
  const [project, setProject] = useState('')
  const [newProject, setNewProject] = useState('')
  const [error, setError] = useState('')

  async function loadProjects() {
    try {
      const data = await api.projects()
      setProjects(data.projects)
      if (!project && data.projects.length) setProject(data.projects[0].name)
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => { loadProjects() }, [])

  async function createProject(e) {
    e.preventDefault()
    const name = newProject.trim()
    if (!name) return
    try {
      await api.createProject(name)
      setNewProject('')
      await loadProjects()
      setProject(name)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="dot" />
          <strong>VibeCut</strong>
          <span className="muted">电商素材台</span>
        </div>

        <nav className="tabs">
          <button className={tab === 'assets' ? 'on' : ''} onClick={() => setTab('assets')}>素材库</button>
          <button className={tab === 'tasks' ? 'on' : ''} onClick={() => setTab('tasks')}>任务</button>
        </nav>

        <div className="spacer" />

        <select value={project} onChange={e => setProject(e.target.value)}>
          {projects.map(p => <option key={p.id} value={p.name}>{p.name}</option>)}
        </select>

        <form className="new-project" onSubmit={createProject}>
          <input value={newProject} onChange={e => setNewProject(e.target.value)}
                 placeholder="新建项目" />
          <button type="submit">+</button>
        </form>
      </header>

      {error && <div className="error" onClick={() => setError('')}>{error}</div>}

      <main>
        {!project && <div className="empty">还没有项目，先在上方新建一个。</div>}
        {project && tab === 'assets' && <Assets project={project} />}
        {project && tab === 'tasks' && <Tasks project={project} />}
      </main>
    </div>
  )
}
