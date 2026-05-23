'use client';

import { useState, useRef, useEffect } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const CHAT_STORAGE_KEY = 'rag-chat-history-v2';
const MAX_CONVERSATIONS = 50;
const MAX_MESSAGES_PER_CONVERSATION = 200;

interface Source {
  file: string;
  page: number;
  page_end?: number;
  chunk_index: number;
  relevance_score: number;
  preview: string;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  sources?: Source[];
  timestamp: string;
}

interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  created_at: string;
  updated_at: string;
}

interface ChatHistory {
  conversations: Conversation[];
  currentId: string | null;
}

interface UploadItem {
  name: string;
  status: 'uploading' | 'processing' | 'completed' | 'failed';
  message?: string;
}

function pageLabel(src: Source): string {
  if (!src.page) return '';
  if (src.page_end && src.page_end !== src.page) return `pp. ${src.page}–${src.page_end}`;
  return `p. ${src.page}`;
}

function generateId(): string {
  return `conv-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function titleFromMessage(content: string): string {
  const trimmed = content.trim().replace(/\s+/g, ' ');
  if (trimmed.length <= 40) return trimmed;
  return trimmed.slice(0, 40) + '…';
}

function newConversation(): Conversation {
  const now = new Date().toISOString();
  return {
    id: generateId(),
    title: 'New chat',
    messages: [],
    created_at: now,
    updated_at: now,
  };
}

function loadHistory(): ChatHistory {
  if (typeof window === 'undefined') return { conversations: [], currentId: null };
  try {
    const raw = window.localStorage.getItem(CHAT_STORAGE_KEY);
    if (!raw) return { conversations: [], currentId: null };
    const parsed = JSON.parse(raw);
    if (!parsed || !Array.isArray(parsed.conversations)) {
      return { conversations: [], currentId: null };
    }
    const conversations: Conversation[] = parsed.conversations.filter(
      (c: any) =>
        c &&
        typeof c.id === 'string' &&
        typeof c.title === 'string' &&
        Array.isArray(c.messages)
    );
    return {
      conversations,
      currentId: typeof parsed.currentId === 'string' ? parsed.currentId : null,
    };
  } catch {
    return { conversations: [], currentId: null };
  }
}

function saveHistory(history: ChatHistory) {
  if (typeof window === 'undefined') return;
  try {
    const sorted = [...history.conversations].sort((a, b) =>
      b.updated_at.localeCompare(a.updated_at)
    );
    const trimmed: ChatHistory = {
      conversations: sorted.slice(0, MAX_CONVERSATIONS).map((c) => ({
        ...c,
        messages: c.messages.slice(-MAX_MESSAGES_PER_CONVERSATION),
      })),
      currentId: history.currentId,
    };
    window.localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    // quota exceeded or storage disabled — silently drop
  }
}

export default function ChatPage() {
  // Initial render uses empty for SSR/hydration safety; localStorage read on mount.
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [apiStatus, setApiStatus] = useState<'checking' | 'ok' | 'error'>('checking');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Upload state
  const [showUpload, setShowUpload] = useState(false);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Derived: current conversation + its messages
  const currentConv = conversations.find((c) => c.id === currentId) || null;
  const messages: Message[] = currentConv?.messages ?? [];

  // Sort conversations for sidebar (most recent first)
  const sortedConversations = [...conversations].sort((a, b) =>
    b.updated_at.localeCompare(a.updated_at)
  );

  // API health check
  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((r) => r.json())
      .then(() => setApiStatus('ok'))
      .catch(() => setApiStatus('error'));
  }, []);

  // Hydrate from localStorage after mount
  useEffect(() => {
    const h = loadHistory();
    setConversations(h.conversations);
    setCurrentId(
      h.currentId && h.conversations.some((c) => c.id === h.currentId)
        ? h.currentId
        : h.conversations.length > 0
        ? [...h.conversations].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0].id
        : null
    );
    setHistoryLoaded(true);
  }, []);

  // Persist on change (after hydration)
  useEffect(() => {
    if (!historyLoaded) return;
    saveHistory({ conversations, currentId });
  }, [conversations, currentId, historyLoaded]);

  // Auto-scroll on new messages or conversation switch
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, currentId]);

  // Poll for upload status while any item is still processing
  useEffect(() => {
    const stillProcessing = uploads.some((u) => u.status === 'processing');
    if (!stillProcessing) return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/api/documents`);
        const data = await res.json();
        const docs = data.documents || [];
        setUploads((prev) =>
          prev.map((u) => {
            if (u.status !== 'processing') return u;
            const match = docs.find((d: any) => d.name === u.name);
            if (!match) return u;
            if (match.status === 'completed') {
              return { ...u, status: 'completed', message: `${match.chunks_count} chunks indexed` };
            }
            if (match.status === 'failed') {
              return { ...u, status: 'failed', message: match.error || 'Ingestion failed' };
            }
            return u;
          })
        );
      } catch {
        // silent — polling will retry
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [uploads]);

  // -------------------- Conversation handlers --------------------

  function handleNewChat() {
    const conv = newConversation();
    setConversations((prev) => [conv, ...prev]);
    setCurrentId(conv.id);
    setInput('');
  }

  function handleSwitchChat(id: string) {
    setCurrentId(id);
    setInput('');
  }

  function handleDeleteChat(id: string) {
    const conv = conversations.find((c) => c.id === id);
    const ok = window.confirm(
      `Delete "${conv?.title || 'this chat'}"? This cannot be undone.`
    );
    if (!ok) return;
    setConversations((prev) => {
      const remaining = prev.filter((c) => c.id !== id);
      if (currentId === id) {
        const next = [...remaining].sort((a, b) =>
          b.updated_at.localeCompare(a.updated_at)
        );
        setCurrentId(next[0]?.id ?? null);
      }
      return remaining;
    });
  }

  function handleClearAll() {
    if (conversations.length === 0) return;
    const ok = window.confirm(
      'Delete ALL chat history? Cannot be undone. Documents and server-side logs are unaffected.'
    );
    if (!ok) return;
    setConversations([]);
    setCurrentId(null);
  }

  function handleRenameChat(id: string) {
    const conv = conversations.find((c) => c.id === id);
    if (!conv) return;
    const newTitle = window.prompt('Rename chat:', conv.title);
    if (!newTitle || newTitle.trim() === '') return;
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title: newTitle.trim().slice(0, 80) } : c))
    );
  }

  // -------------------- Upload --------------------

  async function uploadFiles(files: File[]) {
    if (files.length === 0) return;
    setUploading(true);
    setUploads((prev) => [
      ...files.map((f) => ({ name: f.name, status: 'uploading' as const })),
      ...prev,
    ]);

    for (const file of files) {
      const formData = new FormData();
      formData.append('file', file);
      try {
        const res = await fetch(`${API_URL}/api/documents/upload`, {
          method: 'POST',
          body: formData,
        });
        const data = await res.json();
        setUploads((prev) =>
          prev.map((u) =>
            u.name === file.name && u.status === 'uploading'
              ? res.ok
                ? { ...u, status: 'processing', message: 'Queued for indexing' }
                : { ...u, status: 'failed', message: data.message || `Upload failed (${res.status})` }
              : u
          )
        );
      } catch (err: any) {
        setUploads((prev) =>
          prev.map((u) =>
            u.name === file.name && u.status === 'uploading'
              ? { ...u, status: 'failed', message: err.message }
              : u
          )
        );
      }
    }

    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files) return;
    uploadFiles(Array.from(files));
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    if (apiStatus !== 'ok') return;
    const files = Array.from(e.dataTransfer.files);
    uploadFiles(files);
  }

  // -------------------- Ask --------------------

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const question = input.trim();
    setInput('');
    setLoading(true);

    // Ensure we have a current conversation; create one if needed.
    let activeId = currentId;
    if (!activeId) {
      const conv = newConversation();
      setConversations((prev) => [conv, ...prev]);
      setCurrentId(conv.id);
      activeId = conv.id;
    }

    const userMsg: Message = {
      role: 'user',
      content: question,
      timestamp: new Date().toISOString(),
    };

    // Append user message; auto-set title if this is the first user message.
    setConversations((prev) =>
      prev.map((c) => {
        if (c.id !== activeId) return c;
        const isFirst = c.messages.length === 0;
        return {
          ...c,
          messages: [...c.messages, userMsg],
          title: isFirst ? titleFromMessage(question) : c.title,
          updated_at: new Date().toISOString(),
        };
      })
    );

    try {
      const res = await fetch(`${API_URL}/api/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // Note: backend auto-widens top_k for broad/synthesis questions ("summarize", etc).
        body: JSON.stringify({ question, top_k: 8 }),
      });
      const data = await res.json();
      const assistantMsg: Message = {
        role: 'assistant',
        content: data.answer || data.message || 'No response',
        sources: data.sources || [],
        timestamp: data.timestamp || new Date().toISOString(),
      };
      setConversations((prev) =>
        prev.map((c) =>
          c.id === activeId
            ? {
                ...c,
                messages: [...c.messages, assistantMsg],
                updated_at: new Date().toISOString(),
              }
            : c
        )
      );
    } catch (err: any) {
      const errorMsg: Message = {
        role: 'assistant',
        content: `Error: ${err.message}`,
        timestamp: new Date().toISOString(),
      };
      setConversations((prev) =>
        prev.map((c) =>
          c.id === activeId
            ? {
                ...c,
                messages: [...c.messages, errorMsg],
                updated_at: new Date().toISOString(),
              }
            : c
        )
      );
    } finally {
      setLoading(false);
    }
  }

  // -------------------- Render --------------------

  return (
    <div className="min-h-screen flex bg-gray-50">
      {/* Sidebar */}
      <aside
        className={`${
          sidebarOpen ? 'w-64' : 'w-0'
        } transition-[width] duration-200 bg-gray-900 text-gray-100 flex flex-col overflow-hidden`}
      >
        <div className="p-3 border-b border-gray-800">
          <button
            onClick={handleNewChat}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 text-sm font-medium border border-gray-700 rounded-lg hover:bg-gray-800 transition"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19"></line>
              <line x1="5" y1="12" x2="19" y2="12"></line>
            </svg>
            New chat
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2 py-2">
          {sortedConversations.length === 0 ? (
            <p className="text-xs text-gray-500 px-2 py-3">No conversations yet</p>
          ) : (
            <>
              <p className="text-xs text-gray-500 px-2 py-2 uppercase tracking-wide">Recents</p>
              <div className="space-y-0.5">
                {sortedConversations.map((c) => (
                  <ConversationItem
                    key={c.id}
                    conv={c}
                    active={c.id === currentId}
                    onSwitch={() => handleSwitchChat(c.id)}
                    onDelete={() => handleDeleteChat(c.id)}
                    onRename={() => handleRenameChat(c.id)}
                  />
                ))}
              </div>
            </>
          )}
        </div>

        {sortedConversations.length > 0 && (
          <div className="p-3 border-t border-gray-800">
            <button
              onClick={handleClearAll}
              className="text-xs text-gray-400 hover:text-red-400 transition"
            >
              Clear all history
            </button>
          </div>
        )}
      </aside>

      {/* Main column */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="bg-white border-b border-gray-200 px-6 py-4">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3 min-w-0">
              <button
                onClick={() => setSidebarOpen((v) => !v)}
                className="p-1.5 text-gray-600 hover:bg-gray-100 rounded transition flex-shrink-0"
                title={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="3" y1="6" x2="21" y2="6"></line>
                  <line x1="3" y1="12" x2="21" y2="12"></line>
                  <line x1="3" y1="18" x2="21" y2="18"></line>
                </svg>
              </button>
              <div className="min-w-0">
                <h1 className="text-xl font-semibold text-gray-900 truncate">
                  {currentConv?.title || 'RAG Academic Search'}
                </h1>
                <p className="text-sm text-gray-500">
                  {currentConv ? 'Ask questions about your documents' : 'Start a new conversation'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3 flex-shrink-0">
              <button
                type="button"
                onClick={() => setShowUpload((v) => !v)}
                disabled={apiStatus !== 'ok'}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 disabled:bg-gray-100 disabled:cursor-not-allowed transition"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                  <polyline points="17 8 12 3 7 8"></polyline>
                  <line x1="12" y1="3" x2="12" y2="15"></line>
                </svg>
                {showUpload ? 'Hide upload' : 'Upload'}
              </button>
              <div className="flex items-center gap-2">
                <span
                  className={`w-2 h-2 rounded-full ${
                    apiStatus === 'ok' ? 'bg-green-500' : apiStatus === 'error' ? 'bg-red-500' : 'bg-yellow-500'
                  }`}
                />
                <span className="text-sm text-gray-600">
                  {apiStatus === 'ok' ? 'Connected' : apiStatus === 'error' ? 'Disconnected' : 'Checking...'}
                </span>
              </div>
            </div>
          </div>
        </header>

        {/* Upload panel */}
        {showUpload && (
          <section className="bg-gray-50 border-b border-gray-200 px-6 py-5">
            <div className="max-w-4xl mx-auto">
              <div
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleDrop}
                className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition ${
                  dragOver
                    ? 'border-indigo-500 bg-indigo-50'
                    : 'border-gray-300 bg-white hover:border-indigo-400'
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".pdf,.txt,.docx,.md"
                  onChange={handleFileSelect}
                  disabled={uploading || apiStatus !== 'ok'}
                  className="hidden"
                />
                <p className="text-gray-700 font-medium">
                  {uploading ? 'Uploading…' : 'Drag & drop or click to upload'}
                </p>
                <p className="text-sm text-gray-500 mt-1">PDF, TXT, DOCX, MD · Max 100 MB per file</p>
              </div>

              {uploads.length > 0 && (
                <div className="mt-4 space-y-2">
                  {uploads.slice(0, 8).map((u, idx) => (
                    <div
                      key={`${u.name}-${idx}`}
                      className="flex items-center justify-between bg-white rounded border border-gray-200 px-3 py-2 text-sm"
                    >
                      <div className="flex items-center gap-2 flex-1 min-w-0">
                        <UploadStatusIcon status={u.status} />
                        <span className="truncate text-gray-900">{u.name}</span>
                      </div>
                      <span
                        className={`text-xs ml-3 whitespace-nowrap ${
                          u.status === 'failed'
                            ? 'text-red-600'
                            : u.status === 'completed'
                            ? 'text-green-600'
                            : 'text-gray-500'
                        }`}
                      >
                        {u.message || statusLabel(u.status)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        )}

        {/* Messages */}
        <main className="flex-1 overflow-y-auto px-6 py-8">
          <div className="max-w-4xl mx-auto space-y-6">
            {messages.length === 0 && (
              <div className="text-center text-gray-500 py-16">
                <h2 className="text-lg font-medium mb-2">Welcome 👋</h2>
                <p>Ask anything about your uploaded documents.</p>
                <p className="text-sm mt-2">Examples:</p>
                <ul className="text-sm mt-1 space-y-1">
                  <li>"What is the main topic of the documents?"</li>
                  <li>"Summarize chapter 3"</li>
                  <li>"What does the document say about X?"</li>
                </ul>
                <p className="text-sm mt-4 text-gray-400">
                  Don't have any documents yet? Click <span className="font-medium">Upload</span> in the header to add one.
                </p>
              </div>
            )}

            {messages.map((msg, idx) => (
              <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div
                  className={`max-w-3xl rounded-lg px-4 py-3 ${
                    msg.role === 'user' ? 'bg-indigo-600 text-white' : 'bg-white border border-gray-200'
                  }`}
                >
                  <div className="whitespace-pre-wrap">{msg.content}</div>

                  {msg.sources && msg.sources.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-gray-200">
                      <p className="text-xs font-medium text-gray-700 mb-2">Sources:</p>
                      <div className="space-y-2">
                        {msg.sources.map((src, i) => (
                          <div key={i} className="text-xs bg-gray-50 rounded p-2">
                            <div className="flex justify-between items-start mb-1">
                              <span className="font-medium text-gray-900">
                                📄 {src.file} {src.page > 0 && `· ${pageLabel(src)}`}
                              </span>
                              <span className="text-gray-500">
                                {(Math.max(0, Math.min(1, src.relevance_score)) * 100).toFixed(0)}% match
                              </span>
                            </div>
                            <p className="text-gray-600 italic">{src.preview}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex justify-start">
                <div className="bg-white border border-gray-200 rounded-lg px-4 py-3">
                  <div className="flex gap-1">
                    <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                    <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </main>

        {/* Input */}
        <footer className="bg-white border-t border-gray-200 px-6 py-4">
          <form onSubmit={handleSubmit} className="max-w-4xl mx-auto flex gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask a question..."
              disabled={loading || apiStatus !== 'ok'}
              className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:bg-gray-100"
            />
            <button
              type="submit"
              disabled={loading || !input.trim() || apiStatus !== 'ok'}
              className="px-6 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
            >
              {loading ? 'Thinking...' : 'Ask'}
            </button>
          </form>
        </footer>
      </div>
    </div>
  );
}

function ConversationItem({
  conv,
  active,
  onSwitch,
  onDelete,
  onRename,
}: {
  conv: Conversation;
  active: boolean;
  onSwitch: () => void;
  onDelete: () => void;
  onRename: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div
      className={`group relative flex items-center gap-2 px-2 py-2 rounded text-sm cursor-pointer ${
        active ? 'bg-gray-800 text-white' : 'text-gray-300 hover:bg-gray-800/60'
      }`}
      onClick={onSwitch}
    >
      <span className="flex-1 truncate" title={conv.title}>
        {conv.title || 'New chat'}
      </span>

      <button
        onClick={(e) => {
          e.stopPropagation();
          setMenuOpen((v) => !v);
        }}
        className={`${
          active || menuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
        } text-gray-400 hover:text-white transition flex-shrink-0`}
        title="More options"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="5" r="1"></circle>
          <circle cx="12" cy="12" r="1"></circle>
          <circle cx="12" cy="19" r="1"></circle>
        </svg>
      </button>

      {menuOpen && (
        <>
          {/* Click-away catcher */}
          <div
            className="fixed inset-0 z-10"
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpen(false);
            }}
          />
          <div
            className="absolute right-0 top-9 z-20 bg-gray-800 border border-gray-700 rounded-lg shadow-lg py-1 min-w-[120px]"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => {
                setMenuOpen(false);
                onRename();
              }}
              className="w-full text-left px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700"
            >
              Rename
            </button>
            <button
              onClick={() => {
                setMenuOpen(false);
                onDelete();
              }}
              className="w-full text-left px-3 py-1.5 text-xs text-red-400 hover:bg-gray-700"
            >
              Delete
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function statusLabel(status: UploadItem['status']): string {
  switch (status) {
    case 'uploading':
      return 'Uploading…';
    case 'processing':
      return 'Indexing…';
    case 'completed':
      return 'Ready';
    case 'failed':
      return 'Failed';
  }
}

function UploadStatusIcon({ status }: { status: UploadItem['status'] }) {
  if (status === 'uploading' || status === 'processing') {
    return (
      <svg className="animate-spin h-4 w-4 text-indigo-600 flex-shrink-0" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3"></circle>
        <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round"></path>
      </svg>
    );
  }
  if (status === 'completed') {
    return (
      <svg className="h-4 w-4 text-green-600 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="20 6 9 17 4 12"></polyline>
      </svg>
    );
  }
  return (
    <svg className="h-4 w-4 text-red-600 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18"></line>
      <line x1="6" y1="6" x2="18" y2="18"></line>
    </svg>
  );
}
