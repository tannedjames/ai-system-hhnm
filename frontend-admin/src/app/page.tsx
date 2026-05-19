'use client';

import { useState, useEffect, useRef, useCallback } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface Document {
  id: number;
  name: string;
  size_mb: number;
  type: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  chunks_count: number;
  error: string | null;
  uploaded_at: string;
  processed_at: string | null;
}

interface User {
  id: number;
  username: string;
  email: string;
  role: string;
  created_at: string;
}

export default function AdminPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState('');
  const [apiStatus, setApiStatus] = useState<'checking' | 'ok' | 'error'>('checking');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [activeTab, setActiveTab] = useState<'documents' | 'users'>('documents');
  const [users, setUsers] = useState<User[]>([]);
  const [newUsername, setNewUsername] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newRole, setNewRole] = useState<'admin' | 'user'>('user');
  const [userMsg, setUserMsg] = useState('');

  // Health check
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_URL}/health`, { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw new Error('Health check failed');
        return r.json();
      })
      .then(() => setApiStatus('ok'))
      .catch(() => setApiStatus('error'));
    return () => controller.abort();
  }, []);

  // Load documents on mount + poll every 3 seconds for status updates
  const loadDocuments = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/documents`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setDocuments(data.documents || []);
    } catch (err) {
      console.error('Failed to load documents:', err);
    }
  }, []);

  useEffect(() => {
    loadDocuments();
    const interval = setInterval(loadDocuments, 3000);
    return () => clearInterval(interval);
  }, [loadDocuments]);

  // Load users
  const loadUsers = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/users`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setUsers(data.users || []);
    } catch (err) {
      console.error('Failed to load users:', err);
    }
  }, []);

  useEffect(() => {
    if (activeTab === 'users') {
      loadUsers();
    }
  }, [activeTab, loadUsers]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    setUploading(true);
    setUploadMsg('');

    for (const file of Array.from(files)) {
      const formData = new FormData();
      formData.append('file', file);

      try {
        const res = await fetch(`${API_URL}/api/documents/upload`, {
          method: 'POST',
          body: formData,
        });

        const data = await res.json();
        if (res.ok) {
          setUploadMsg(`✓ Uploaded ${file.name} — processing in background`);
        } else {
          setUploadMsg(`✗ ${file.name}: ${data.message || 'Upload failed'}`);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Upload failed';
        setUploadMsg(`✗ ${file.name}: ${msg}`);
      }
    }

    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  async function handleDelete(filename: string) {
    if (!confirm(`Delete "${filename}"? This removes it from MinIO, PostgreSQL, and Qdrant.`)) return;

    try {
      const res = await fetch(`${API_URL}/api/documents/${encodeURIComponent(filename)}`, {
        method: 'DELETE',
      });

      if (res.ok) {
        loadDocuments();
      } else {
        const data = await res.json();
        alert(`Delete failed: ${data.message}`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Delete failed';
      alert(`Error: ${msg}`);
    }
  }

  async function handleCreateUser(e: React.FormEvent) {
    e.preventDefault();
    if (!newUsername.trim()) return;
    setUserMsg('');

    try {
      const res = await fetch(`${API_URL}/api/users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: newUsername, email: newEmail, role: newRole }),
      });
      const data = await res.json();
      if (res.ok) {
        setNewUsername('');
        setNewEmail('');
        setNewRole('user');
        setUserMsg(`✓ Created user ${data.username}`);
        loadUsers();
      } else {
        setUserMsg(`✗ ${data.message || 'Create failed'}`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Create failed';
      setUserMsg(`✗ ${msg}`);
    }
  }

  async function handleDeleteUser(userId: number, username: string) {
    if (!confirm(`Delete user "${username}"?`)) return;

    try {
      const res = await fetch(`${API_URL}/api/users/${userId}`, { method: 'DELETE' });
      if (res.ok) {
        loadUsers();
      } else {
        const data = await res.json();
        alert(`Delete failed: ${data.message}`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Delete failed';
      alert(`Error: ${msg}`);
    }
  }

  const stats = {
    total: documents.length,
    completed: documents.filter((d) => d.status === 'completed').length,
    processing: documents.filter((d) => d.status === 'processing' || d.status === 'pending').length,
    failed: documents.filter((d) => d.status === 'failed').length,
    totalChunks: documents.reduce((sum, d) => sum + (d.chunks_count || 0), 0),
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">RAG Admin Dashboard</h1>
            <p className="text-sm text-gray-500">Manage documents and system settings</p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                apiStatus === 'ok' ? 'bg-green-500' : apiStatus === 'error' ? 'bg-red-500' : 'bg-yellow-500'
              }`}
            />
            <span className="text-sm text-gray-600">
              {apiStatus === 'ok' ? 'Backend connected' : apiStatus === 'error' ? 'Backend offline' : 'Checking...'}
            </span>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8">
        {/* Tabs */}
        <div className="flex gap-4 mb-6 border-b border-gray-200">
          <button
            onClick={() => setActiveTab('documents')}
            className={`pb-2 text-sm font-medium ${
              activeTab === 'documents'
                ? 'text-indigo-600 border-b-2 border-indigo-600'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Documents
          </button>
          <button
            onClick={() => setActiveTab('users')}
            className={`pb-2 text-sm font-medium ${
              activeTab === 'users'
                ? 'text-indigo-600 border-b-2 border-indigo-600'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Users & Permissions
          </button>
        </div>

        {activeTab === 'documents' && (
          <>
            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-8">
              <StatCard label="Total documents" value={stats.total} color="gray" />
              <StatCard label="Completed" value={stats.completed} color="green" />
              <StatCard label="Processing" value={stats.processing} color="yellow" />
              <StatCard label="Failed" value={stats.failed} color="red" />
              <StatCard label="Total chunks" value={stats.totalChunks} color="indigo" />
            </div>

            {/* Upload */}
            <section className="bg-white rounded-lg border border-gray-200 p-6 mb-8">
              <h2 className="text-lg font-medium mb-4">Upload documents</h2>
              <div
                onClick={() => fileInputRef.current?.click()}
                className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center cursor-pointer hover:border-indigo-500 transition"
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".pdf,.txt,.docx,.md"
                  onChange={handleUpload}
                  disabled={uploading || apiStatus !== 'ok'}
                  className="hidden"
                />
                <p className="text-gray-600">
                  {uploading ? 'Uploading...' : 'Click to select files (PDF, TXT, DOCX, MD)'}
                </p>
                <p className="text-sm text-gray-400 mt-1">Max 100 MB per file</p>
              </div>
              {uploadMsg && (
                <p className="mt-3 text-sm text-gray-700">{uploadMsg}</p>
              )}
            </section>

            {/* Documents table */}
            <section className="bg-white rounded-lg border border-gray-200">
              <div className="px-6 py-4 border-b border-gray-200">
                <h2 className="text-lg font-medium">Documents</h2>
              </div>

              {documents.length === 0 ? (
                <div className="px-6 py-12 text-center text-gray-500">
                  No documents uploaded yet.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">File</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Size</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Chunks</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Uploaded</th>
                        <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {documents.map((doc) => (
                        <tr key={doc.id}>
                          <td className="px-6 py-4 text-sm text-gray-900">{doc.name}</td>
                          <td className="px-6 py-4 text-sm text-gray-500 uppercase">{doc.type}</td>
                          <td className="px-6 py-4 text-sm text-gray-500">{doc.size_mb} MB</td>
                          <td className="px-6 py-4">
                            <StatusBadge status={doc.status} />
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-500">{doc.chunks_count || '-'}</td>
                          <td className="px-6 py-4 text-sm text-gray-500">
                            {new Date(doc.uploaded_at).toLocaleString()}
                          </td>
                          <td className="px-6 py-4 text-right">
                            <button
                              onClick={() => handleDelete(doc.name)}
                              className="text-red-600 hover:text-red-800 text-sm"
                            >
                              Delete
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}

        {activeTab === 'users' && (
          <>
            {/* Add user */}
            <section className="bg-white rounded-lg border border-gray-200 p-6 mb-8">
              <h2 className="text-lg font-medium mb-4">Add user</h2>
              <form onSubmit={handleCreateUser} className="flex flex-wrap gap-3 items-end">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Username</label>
                  <input
                    type="text"
                    value={newUsername}
                    onChange={(e) => setNewUsername(e.target.value)}
                    placeholder="username"
                    required
                    className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Email</label>
                  <input
                    type="email"
                    value={newEmail}
                    onChange={(e) => setNewEmail(e.target.value)}
                    placeholder="user@example.com"
                    className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Role</label>
                  <select
                    value={newRole}
                    onChange={(e) => setNewRole(e.target.value as 'admin' | 'user')}
                    className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  >
                    <option value="user">User</option>
                    <option value="admin">Admin</option>
                  </select>
                </div>
                <button
                  type="submit"
                  disabled={apiStatus !== 'ok'}
                  className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 disabled:bg-gray-300"
                >
                  Add
                </button>
              </form>
              {userMsg && <p className="mt-3 text-sm text-gray-700">{userMsg}</p>}
            </section>

            {/* Users table */}
            <section className="bg-white rounded-lg border border-gray-200">
              <div className="px-6 py-4 border-b border-gray-200">
                <h2 className="text-lg font-medium">Users</h2>
              </div>

              {users.length === 0 ? (
                <div className="px-6 py-12 text-center text-gray-500">
                  No users yet.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">ID</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Username</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Email</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                        <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {users.map((u) => (
                        <tr key={u.id}>
                          <td className="px-6 py-4 text-sm text-gray-500">{u.id}</td>
                          <td className="px-6 py-4 text-sm text-gray-900 font-medium">{u.username}</td>
                          <td className="px-6 py-4 text-sm text-gray-500">{u.email || '-'}</td>
                          <td className="px-6 py-4">
                            <span
                              className={`inline-block px-2 py-1 rounded text-xs font-medium ${
                                u.role === 'admin'
                                  ? 'bg-purple-100 text-purple-800'
                                  : 'bg-gray-100 text-gray-700'
                              }`}
                            >
                              {u.role}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-500">
                            {u.created_at ? new Date(u.created_at).toLocaleString() : '-'}
                          </td>
                          <td className="px-6 py-4 text-right">
                            <button
                              onClick={() => handleDeleteUser(u.id, u.username)}
                              className="text-red-600 hover:text-red-800 text-sm"
                            >
                              Delete
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  const colorMap: Record<string, string> = {
    gray: 'text-gray-900',
    green: 'text-green-600',
    yellow: 'text-yellow-600',
    red: 'text-red-600',
    indigo: 'text-indigo-600',
  };

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <p className="text-xs text-gray-500 uppercase mb-1">{label}</p>
      <p className={`text-2xl font-semibold ${colorMap[color] ?? ''}`}>{value}</p>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: 'bg-gray-100 text-gray-700',
    processing: 'bg-yellow-100 text-yellow-800',
    completed: 'bg-green-100 text-green-800',
    failed: 'bg-red-100 text-red-800',
  };

  return (
    <span className={`inline-block px-2 py-1 rounded text-xs font-medium ${styles[status] || styles.pending}`}>
      {status}
    </span>
  );
}
