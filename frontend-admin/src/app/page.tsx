'use client';

import { useState, useEffect, useRef } from 'react';

const MAX_UPLOAD_MB = 50;
const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024;
const ALLOWED_EXTENSIONS = ['pdf', 'txt', 'docx', 'md'];

function getApiUrl() {
  const configured = process.env.NEXT_PUBLIC_API_URL || '';
  if (configured) return configured;
  if (typeof window !== 'undefined' && window.location.port === '3001') {
    return 'http://localhost:8000';
  }
  return '';
}

interface Document {
  id: number;
  name: string;
  size_mb: number;
  type: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  chunks_count: number;
  pages_count: number | null;
  empty_pages_count: number | null;
  ocr_pages_count: number | null;
  chunks_dropped: number | null;
  ingest_duration_ms: number | null;
  embedding_model: string | null;
  embedding_dim: number | null;
  chunk_size: number | null;
  chunk_overlap: number | null;
  error: string | null;
  uploaded_at: string;
  processed_at: string | null;
}

interface User {
  id: number;
  username: string;
  role: 'admin' | 'user';
  is_active: boolean;
}

type RagSettings = Record<string, string>;

export default function AdminPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [ragSettings, setRagSettings] = useState<RagSettings>({});
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState('');
  const [settingsMsg, setSettingsMsg] = useState('');
  const [dragActive, setDragActive] = useState(false);
  const [apiStatus, setApiStatus] = useState<'checking' | 'ok' | 'error'>('checking');
  const [token, setToken] = useState('');
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('admin123');
  const [newUser, setNewUser] = useState({ username: '', password: '', role: 'user' });
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const saved = localStorage.getItem('admin_token') || '';
    if (saved) setToken(saved);
    fetch(`${getApiUrl()}/health`).then(() => setApiStatus('ok')).catch(() => setApiStatus('error'));
  }, []);

  useEffect(() => {
    if (!token) return;
    loadDocuments();
    loadUsers();
    loadRagSettings();
    const interval = setInterval(loadDocuments, 3000);
    return () => clearInterval(interval);
  }, [token]);

  async function authFetch(path: string, init: RequestInit = {}) {
    return fetch(`${getApiUrl()}${path}`, {
      ...init,
      headers: {
        ...(init.headers || {}),
        Authorization: `Bearer ${token}`,
      },
    });
  }

  async function login() {
    const form = new URLSearchParams();
    form.set('username', username);
    form.set('password', password);
    const res = await fetch(`${getApiUrl()}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form,
    });
    const data = await res.json();
    if (!res.ok) return alert(data.message || 'Login failed');
    setToken(data.access_token);
    localStorage.setItem('admin_token', data.access_token);
  }

  async function loadDocuments() {
    const res = await authFetch('/api/documents');
    const data = await res.json();
    if (res.ok) setDocuments(data.documents || []);
  }

  async function loadUsers() {
    const res = await authFetch('/api/admin/users');
    const data = await res.json();
    if (res.ok) setUsers(data.users || []);
  }

  async function loadRagSettings() {
    const res = await authFetch('/api/admin/rag-settings');
    const data = await res.json();
    if (res.ok) setRagSettings(data.settings || {});
  }

  function updateRagSetting(key: string, value: string) {
    setRagSettings((prev) => ({ ...prev, [key]: value }));
  }

  function validateFiles(files: File[]) {
    const accepted: File[] = [];
    const rejected: string[] = [];

    for (const file of files) {
      const extension = file.name.includes('.') ? file.name.split('.').pop()?.toLowerCase() || '' : '';
      if (!ALLOWED_EXTENSIONS.includes(extension)) {
        rejected.push(`${file.name}: unsupported file type`);
        continue;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        rejected.push(`${file.name}: max ${MAX_UPLOAD_MB} MB`);
        continue;
      }
      accepted.push(file);
    }

    return { accepted, rejected };
  }

  async function uploadFiles(files: File[]) {
    if (files.length === 0) return;
    const { accepted, rejected } = validateFiles(files);
    if (accepted.length === 0) {
      setUploadMsg(rejected.join('\n'));
      return;
    }

    setUploading(true);
    setUploadMsg(rejected.join('\n'));
    const messages = [...rejected];
    for (const file of accepted) {
      const formData = new FormData();
      formData.append('file', file);
      const res = await authFetch('/api/documents/upload', { method: 'POST', body: formData });
      const data = await res.json();
      messages.push(res.ok ? `Uploaded ${file.name}` : `${file.name}: ${data.message || 'Upload failed'}`);
      setUploadMsg(messages.join('\n'));
    }
    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = '';
    loadDocuments();
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    await uploadFiles(Array.from(files));
  }

  async function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (uploading || apiStatus !== 'ok') return;
    await uploadFiles(Array.from(e.dataTransfer.files || []));
  }

  async function handleDelete(filename: string) {
    if (!confirm(`Delete "${filename}"?`)) return;
    const res = await authFetch(`/api/documents/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    if (!res.ok) return alert('Delete failed');
    loadDocuments();
  }

  async function handleReindex(filename: string) {
    const res = await authFetch(`/api/documents/${encodeURIComponent(filename)}/reindex`, { method: 'POST' });
    if (!res.ok) return alert('Reindex failed');
    loadDocuments();
  }

  async function handleCreateUser() {
    const res = await authFetch('/api/admin/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify(newUser),
    });
    if (!res.ok) return alert('Create user failed');
    setNewUser({ username: '', password: '', role: 'user' });
    loadUsers();
  }

  async function handleUpdateUser(user: User) {
    const res = await authFetch(`/api/admin/users/${user.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ role: user.role, is_active: user.is_active }),
    });
    if (!res.ok) return alert('Update user failed');
    loadUsers();
  }

  async function handleDeleteUser(id: number) {
    const res = await authFetch(`/api/admin/users/${id}`, { method: 'DELETE' });
    if (!res.ok) return alert('Delete user failed');
    loadUsers();
  }

  async function handleSaveSettings() {
    const payload = {
      CHUNK_SIZE: Number(ragSettings.CHUNK_SIZE || 500),
      CHUNK_OVERLAP: Number(ragSettings.CHUNK_OVERLAP || 100),
      RAG_CHUNK_MIN_CHARS: Number(ragSettings.RAG_CHUNK_MIN_CHARS || 120),
      RAG_DEDUP_CHUNKS: String(ragSettings.RAG_DEDUP_CHUNKS).toLowerCase() === 'true',
      RETRIEVAL_TOP_K: Number(ragSettings.RETRIEVAL_TOP_K || 5),
      USE_RERANKER: String(ragSettings.USE_RERANKER).toLowerCase() === 'true',
      MAX_CONTEXT_CHUNKS: Number(ragSettings.MAX_CONTEXT_CHUNKS || 8),
      MAX_SOURCES_IN_ANSWER: Number(ragSettings.MAX_SOURCES_IN_ANSWER || 5),
      MIN_RELEVANCE_SCORE: Number(ragSettings.MIN_RELEVANCE_SCORE || 0),
      EMBEDDING_PROVIDER: ragSettings.EMBEDDING_PROVIDER || 'sentence-transformers',
      EMBEDDING_MODEL: ragSettings.EMBEDDING_MODEL || 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
      EMBEDDING_DIM: Number(ragSettings.EMBEDDING_DIM || 384),
      OCR_ENABLED: String(ragSettings.OCR_ENABLED).toLowerCase() === 'true',
      OCR_LANGS: ragSettings.OCR_LANGS || 'eng+vie',
      OCR_DPI: Number(ragSettings.OCR_DPI || 200),
      OCR_MIN_TEXT_CHARS: Number(ragSettings.OCR_MIN_TEXT_CHARS || 1),
    };
    const res = await authFetch('/api/admin/rag-settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) return setSettingsMsg(data.message || 'Save settings failed');
    setRagSettings(data.settings || {});
    setSettingsMsg(data.restart_required ? 'Saved. Restart backend/worker, then reindex all.' : data.reindex_required ? 'Saved. Reindex all documents.' : 'Saved.');
  }

  async function handleReindexAll() {
    if (!confirm('Queue reindex for all documents?')) return;
    const res = await authFetch('/api/documents/reindex-all', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) return alert(data.message || 'Reindex all failed');
    setSettingsMsg(`Queued ${data.total} documents for reindex`);
    loadDocuments();
  }

  const cardClass = 'rounded-lg border border-gray-200 bg-white/95 p-4 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/95';
  const fieldClass = 'mt-1 w-full rounded-md border border-gray-300 bg-white p-2 text-gray-900 shadow-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:placeholder:text-slate-400';
  const compactFieldClass = 'rounded-md border border-gray-300 bg-white p-1.5 text-gray-900 shadow-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100';
  const primaryButtonClass = 'rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500';
  const ghostButtonClass = 'rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white transition hover:bg-gray-700 dark:bg-slate-700 dark:hover:bg-slate-600';
  const deleteButtonClass = 'text-red-600 transition hover:text-red-500 dark:text-rose-400 dark:hover:text-rose-300';
  const linkButtonClass = 'text-indigo-600 transition hover:text-indigo-500 dark:text-indigo-300 dark:hover:text-indigo-200';

  if (!token) {
    return (
      <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white px-4 py-8 text-gray-900 dark:from-slate-950 dark:to-slate-900 dark:text-slate-100">
        <div className="mx-auto mt-24 w-full max-w-md rounded-xl border border-gray-200 bg-white/95 p-6 shadow-lg backdrop-blur dark:border-slate-700 dark:bg-slate-900/95">
          <h1 className="text-lg font-semibold">Admin login</h1>
          <div className="mt-4 space-y-3">
            <input className={fieldClass} value={username} onChange={(e) => setUsername(e.target.value)} placeholder="username" />
            <input className={fieldClass} type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="password" />
            <button className="w-full rounded-md bg-indigo-600 p-2 font-medium text-white transition hover:bg-indigo-500" onClick={login}>Login</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white p-4 text-gray-900 md:p-6 dark:from-slate-950 dark:to-slate-900 dark:text-slate-100">
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-xl font-semibold">RAG Admin</h1>
          <button
            onClick={() => {
              localStorage.removeItem('admin_token');
              setToken('');
            }}
            className={deleteButtonClass}
          >
            Logout
          </button>
        </div>

        <section className={`${cardClass} space-y-3`}>
          <h2 className="font-medium">Upload documents</h2>
          <div
            onDragEnter={(e) => {
              e.preventDefault();
              setDragActive(true);
            }}
            onDragOver={(e) => e.preventDefault()}
            onDragLeave={(e) => {
              e.preventDefault();
              setDragActive(false);
            }}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`cursor-pointer rounded-lg border-2 border-dashed p-8 text-center transition ${
              dragActive
                ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-950/50'
                : 'border-gray-300 bg-gray-50 hover:border-indigo-400 hover:bg-indigo-50 dark:border-slate-600 dark:bg-slate-800 dark:hover:bg-slate-700'
            } ${uploading || apiStatus !== 'ok' ? 'opacity-60' : ''}`}
          >
            <input
              ref={fileInputRef}
              className="hidden"
              type="file"
              multiple
              accept=".pdf,.txt,.docx,.md"
              onChange={handleUpload}
              disabled={uploading || apiStatus !== 'ok'}
            />
            <p className="font-medium text-gray-900 dark:text-slate-100">{uploading ? 'Uploading...' : 'Drop files here or click to browse'}</p>
            <p className="mt-1 text-sm text-gray-600 dark:text-slate-300">PDF, TXT, DOCX, MD up to {MAX_UPLOAD_MB} MB each</p>
          </div>
          {uploadMsg && <p className="whitespace-pre-line text-sm text-gray-700 dark:text-slate-300">{uploadMsg}</p>}
        </section>

        <section className={`${cardClass} space-y-3`}>
          <div className="flex items-center justify-between gap-3">
            <h2 className="font-medium">RAG settings</h2>
            <button className={ghostButtonClass} onClick={handleReindexAll}>Reindex all</button>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <label className="text-sm text-gray-700 dark:text-slate-300">Chunk size<input className={fieldClass} type="number" value={ragSettings.CHUNK_SIZE || ''} onChange={(e) => updateRagSetting('CHUNK_SIZE', e.target.value)} /></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">Overlap<input className={fieldClass} type="number" value={ragSettings.CHUNK_OVERLAP || ''} onChange={(e) => updateRagSetting('CHUNK_OVERLAP', e.target.value)} /></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">Min chars<input className={fieldClass} type="number" value={ragSettings.RAG_CHUNK_MIN_CHARS || ''} onChange={(e) => updateRagSetting('RAG_CHUNK_MIN_CHARS', e.target.value)} /></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">Dedup chunks<select className={fieldClass} value={ragSettings.RAG_DEDUP_CHUNKS || 'True'} onChange={(e) => updateRagSetting('RAG_DEDUP_CHUNKS', e.target.value)}><option value="True">on</option><option value="False">off</option></select></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">Top K<input className={fieldClass} type="number" value={ragSettings.RETRIEVAL_TOP_K || ''} onChange={(e) => updateRagSetting('RETRIEVAL_TOP_K', e.target.value)} /></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">Context chunks<input className={fieldClass} type="number" value={ragSettings.MAX_CONTEXT_CHUNKS || ''} onChange={(e) => updateRagSetting('MAX_CONTEXT_CHUNKS', e.target.value)} /></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">Sources<input className={fieldClass} type="number" value={ragSettings.MAX_SOURCES_IN_ANSWER || ''} onChange={(e) => updateRagSetting('MAX_SOURCES_IN_ANSWER', e.target.value)} /></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">Min score<input className={fieldClass} type="number" step="0.01" value={ragSettings.MIN_RELEVANCE_SCORE || ''} onChange={(e) => updateRagSetting('MIN_RELEVANCE_SCORE', e.target.value)} /></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">Reranker<select className={fieldClass} value={ragSettings.USE_RERANKER || 'False'} onChange={(e) => updateRagSetting('USE_RERANKER', e.target.value)}><option value="False">off</option><option value="True">on</option></select></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">Embedding dim<input className={fieldClass} type="number" value={ragSettings.EMBEDDING_DIM || ''} onChange={(e) => updateRagSetting('EMBEDDING_DIM', e.target.value)} /></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">OCR<select className={fieldClass} value={ragSettings.OCR_ENABLED || 'True'} onChange={(e) => updateRagSetting('OCR_ENABLED', e.target.value)}><option value="True">on</option><option value="False">off</option></select></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">OCR DPI<input className={fieldClass} type="number" value={ragSettings.OCR_DPI || ''} onChange={(e) => updateRagSetting('OCR_DPI', e.target.value)} /></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">OCR min text<input className={fieldClass} type="number" value={ragSettings.OCR_MIN_TEXT_CHARS || ''} onChange={(e) => updateRagSetting('OCR_MIN_TEXT_CHARS', e.target.value)} /></label>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-sm text-gray-700 dark:text-slate-300">Embedding provider<input className={fieldClass} value={ragSettings.EMBEDDING_PROVIDER || ''} onChange={(e) => updateRagSetting('EMBEDDING_PROVIDER', e.target.value)} /></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">Embedding model<input className={fieldClass} value={ragSettings.EMBEDDING_MODEL || ''} onChange={(e) => updateRagSetting('EMBEDDING_MODEL', e.target.value)} /></label>
            <label className="text-sm text-gray-700 dark:text-slate-300">OCR languages<input className={fieldClass} value={ragSettings.OCR_LANGS || ''} onChange={(e) => updateRagSetting('OCR_LANGS', e.target.value)} /></label>
          </div>
          <div className="flex items-center gap-3">
            <button className={primaryButtonClass} onClick={handleSaveSettings}>Save settings</button>
            {settingsMsg && <p className="text-sm text-gray-600 dark:text-slate-300">{settingsMsg}</p>}
          </div>
        </section>

        <section className={cardClass}>
          <h2 className="mb-3 font-medium">Documents</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-600 dark:text-slate-300">
                  <th>File</th>
                  <th>Status</th>
                  <th>Pages</th>
                  <th>Empty</th>
                  <th>OCR</th>
                  <th>Chunks</th>
                  <th>Dropped</th>
                  <th>Time</th>
                  <th>Model</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <tr key={doc.id} className="border-t border-gray-200 align-top dark:border-slate-700">
                    <td className="py-2">
                      {doc.name}
                      {doc.error && <div className="max-w-xs text-xs text-red-600 dark:text-rose-400">{doc.error}</div>}
                    </td>
                    <td>{doc.status}</td>
                    <td>{doc.pages_count || '-'}</td>
                    <td>{doc.empty_pages_count ?? '-'}</td>
                    <td>{doc.ocr_pages_count ?? '-'}</td>
                    <td>{doc.chunks_count || '-'}</td>
                    <td>{doc.chunks_dropped ?? '-'}</td>
                    <td>{doc.ingest_duration_ms ? `${Math.round(doc.ingest_duration_ms / 1000)}s` : '-'}</td>
                    <td className="max-w-xs truncate" title={doc.embedding_model || ''}>{doc.embedding_model || '-'}</td>
                    <td className="space-x-3">
                      <button className={linkButtonClass} onClick={() => handleReindex(doc.name)}>Reindex</button>
                      <button className={deleteButtonClass} onClick={() => handleDelete(doc.name)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className={`${cardClass} space-y-3`}>
          <h2 className="font-medium">Users</h2>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
            <input className={fieldClass} placeholder="username" value={newUser.username} onChange={(e) => setNewUser({ ...newUser, username: e.target.value })} />
            <input className={fieldClass} type="password" placeholder="password" value={newUser.password} onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} />
            <select className={fieldClass} value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}><option value="user">user</option><option value="admin">admin</option></select>
            <button className={primaryButtonClass} onClick={handleCreateUser}>Create</button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-600 dark:text-slate-300">
                  <th>Username</th>
                  <th>Role</th>
                  <th>Active</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="border-t border-gray-200 dark:border-slate-700">
                    <td className="py-2">{u.username}</td>
                    <td>
                      <select
                        className={compactFieldClass}
                        value={u.role}
                        onChange={(e) => setUsers((prev) => prev.map((item) => item.id === u.id ? { ...item, role: e.target.value as 'admin' | 'user' } : item))}
                      >
                        <option value="user">user</option>
                        <option value="admin">admin</option>
                      </select>
                    </td>
                    <td>
                      <select
                        className={compactFieldClass}
                        value={String(u.is_active)}
                        onChange={(e) => setUsers((prev) => prev.map((item) => item.id === u.id ? { ...item, is_active: e.target.value === 'true' } : item))}
                      >
                        <option value="true">true</option>
                        <option value="false">false</option>
                      </select>
                    </td>
                    <td className="space-x-3">
                      <button className={linkButtonClass} onClick={() => handleUpdateUser(u)}>Save</button>
                      <button className={deleteButtonClass} onClick={() => handleDeleteUser(u.id)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}
