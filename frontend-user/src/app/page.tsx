'use client';

import { useState, useRef, useEffect } from 'react';

const CHAT_HISTORY_KEY = 'rag_chat_history_v1';
const MAX_CHAT_SESSIONS = 20;

function getApiUrl() {
  const configured = process.env.NEXT_PUBLIC_API_URL || '';
  if (configured) return configured;
  if (typeof window !== 'undefined' && window.location.port === '3000') {
    return 'http://localhost:8000';
  }
  return '';
}

interface Source {
  file: string;
  page: number;
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

interface ChatSession {
  id: string;
  title: string;
  messages: Message[];
  updatedAt: string;
}

function makeSessionId() {
  return `chat-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [chatSessions, setChatSessions] = useState<ChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState('');
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [apiStatus, setApiStatus] = useState<'checking' | 'ok' | 'error'>('checking');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  function cleanMarkdownText(text: string) {
    return (text || '')
      .replace(/[*_`#>\[\]{}]/g, '')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  // Check API health on mount
  useEffect(() => {
    fetch(`${getApiUrl()}/health`)
      .then((r) => r.json())
      .then(() => setApiStatus('ok'))
      .catch(() => setApiStatus('error'));

    try {
      const saved = JSON.parse(localStorage.getItem(CHAT_HISTORY_KEY) || '[]') as ChatSession[];
      if (saved.length > 0) {
        setChatSessions(saved);
        setCurrentSessionId(saved[0].id);
        setMessages(saved[0].messages || []);
      } else {
        setCurrentSessionId(makeSessionId());
      }
    } catch {
      setCurrentSessionId(makeSessionId());
    }
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!currentSessionId || messages.length === 0) return;

    setChatSessions((prev) => {
      const firstQuestion = messages.find((msg) => msg.role === 'user')?.content || 'New chat';
      const nextSession: ChatSession = {
        id: currentSessionId,
        title: firstQuestion.slice(0, 56),
        messages,
        updatedAt: new Date().toISOString(),
      };
      const next = [nextSession, ...prev.filter((session) => session.id !== currentSessionId)]
        .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))
        .slice(0, MAX_CHAT_SESSIONS);
      localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(next));
      return next;
    });
  }, [currentSessionId, messages]);

  function startNewChat() {
    setCurrentSessionId(makeSessionId());
    setMessages([]);
    setInput('');
  }

  function openChat(session: ChatSession) {
    setCurrentSessionId(session.id);
    setMessages(session.messages || []);
    setInput('');
  }

  function deleteChat(sessionId: string) {
    const next = chatSessions.filter((session) => session.id !== sessionId);
    setChatSessions(next);
    localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(next));
    if (sessionId !== currentSessionId) return;
    if (next.length > 0) {
      setCurrentSessionId(next[0].id);
      setMessages(next[0].messages || []);
    } else {
      startNewChat();
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const question = input.trim();
    setInput('');
    setLoading(true);

    // Add user message
    const userMsg: Message = {
      role: 'user',
      content: question,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const res = await fetch(`${getApiUrl()}/api/ask/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, top_k: 5 }),
      });
      if (!res.body) throw new Error('No stream body');
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let content = '';
      let sources: Source[] = [];
      let timestamp = new Date().toISOString();
      setMessages((prev) => [...prev, { role: 'assistant', content: '', timestamp }]);
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        for (const part of parts) {
          const line = part.split('\n').find((ln) => ln.startsWith('data: '));
          if (!line) continue;
          const payload = JSON.parse(line.slice(6));
          if (payload.type === 'token') content += payload.text;
          if (payload.type === 'end') {
            sources = payload.sources || [];
            timestamp = payload.timestamp || timestamp;
          }
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === 'assistant') {
              next[next.length - 1] = {
                ...last,
                content: cleanMarkdownText(content),
                sources: (sources || []).map((src) => ({
                  ...src,
                  preview: cleanMarkdownText(src.preview || ''),
                })),
                timestamp,
              };
            }
            return next;
          });
        }
      }
    } catch (err: any) {
      const errorMsg: Message = {
        role: 'assistant',
        content: `Error: ${err.message}`,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen bg-gray-50 text-gray-900 dark:bg-slate-950 dark:text-slate-100">
      <aside className="hidden w-72 shrink-0 border-r border-gray-200 bg-white px-4 py-4 dark:border-slate-700 dark:bg-slate-900 md:flex md:flex-col">
        <div className="mb-4 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-gray-900 dark:text-slate-100">Chat history</h2>
          <button
            type="button"
            onClick={startNewChat}
            className="rounded bg-gray-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-gray-700 dark:bg-slate-700 dark:hover:bg-slate-600"
          >
            New
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
          {chatSessions.length === 0 && (
            <p className="rounded border border-dashed border-gray-200 p-3 text-sm text-gray-500 dark:border-slate-700 dark:text-slate-300">No saved chats yet.</p>
          )}
          {chatSessions.map((session) => (
            <div
              key={session.id}
              className={`group rounded border p-2 ${
                session.id === currentSessionId
                  ? 'border-indigo-200 bg-indigo-50 dark:border-indigo-800 dark:bg-indigo-950/50'
                  : 'border-gray-200 bg-white hover:bg-gray-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800'
              }`}
            >
              <button type="button" onClick={() => openChat(session)} className="block w-full text-left">
                <span className="line-clamp-2 text-sm font-medium text-gray-900 dark:text-slate-100">{session.title}</span>
                <span className="mt-1 block text-xs text-gray-500 dark:text-slate-400">{new Date(session.updatedAt).toLocaleString()}</span>
              </button>
              <button
                type="button"
                onClick={() => deleteChat(session.id)}
                className="mt-2 text-xs text-red-600 transition hover:text-red-700 dark:text-rose-400 dark:hover:text-rose-300"
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      </aside>

      <div className="flex min-h-screen flex-1 flex-col">
      {/* Header */}
      <header className="border-b border-gray-200 bg-white px-6 py-4 dark:border-slate-700 dark:bg-slate-900">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900 dark:text-slate-100">RAG Academic Search</h1>
            <p className="text-sm text-gray-500 dark:text-slate-300">Ask questions about your documents</p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                apiStatus === 'ok' ? 'bg-green-500' : apiStatus === 'error' ? 'bg-red-500' : 'bg-yellow-500'
              }`}
            />
            <span className="text-sm text-gray-600 dark:text-slate-300">
              {apiStatus === 'ok' ? 'Connected' : apiStatus === 'error' ? 'Disconnected' : 'Checking...'}
            </span>
          </div>
        </div>
      </header>

      {/* Messages */}
      <main className="flex-1 overflow-y-auto px-6 py-8">
        <div className="max-w-4xl mx-auto space-y-6">
          {messages.length === 0 && (
            <div className="py-16 text-center text-gray-500 dark:text-slate-300">
              <h2 className="text-lg font-medium mb-2">Welcome 👋</h2>
              <p>Ask anything about your uploaded documents.</p>
              <p className="text-sm mt-2">Examples:</p>
              <ul className="text-sm mt-1 space-y-1">
                <li>"What is the main topic of the documents?"</li>
                <li>"Summarize chapter 3"</li>
                <li>"What does the document say about X?"</li>
              </ul>
            </div>
          )}

          {messages.map((msg, idx) => (
            <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-3xl rounded-lg px-4 py-3 ${
                  msg.role === 'user'
                    ? 'bg-indigo-600 text-white dark:bg-indigo-500'
                    : 'border border-gray-200 bg-white text-gray-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100'
                }`}
              >
                <div className="whitespace-pre-wrap">{msg.content}</div>

                {msg.sources && msg.sources.length > 0 && (
                  <div className="mt-3 border-t border-gray-200 pt-3 dark:border-slate-700">
                    <p className="mb-2 text-xs font-medium text-gray-700 dark:text-slate-200">Sources:</p>
                    <div className="space-y-2">
                      {msg.sources.map((src, i) => (
                        <div key={i} className="rounded bg-gray-50 p-2 text-xs dark:bg-slate-800">
                          <div className="flex justify-between items-start mb-1">
                            <span className="font-medium text-gray-900 dark:text-slate-100">
                              📄 {src.file} {src.page > 0 && `· page ${src.page}`}
                            </span>
                            <span className="text-gray-500 dark:text-slate-300">
                              {(Math.max(0, Math.min(1, src.relevance_score)) * 100).toFixed(0)}% match
                            </span>
                          </div>
                          <p className="italic text-gray-600 dark:text-slate-300">{src.preview}</p>
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
              <div className="rounded-lg border border-gray-200 bg-white px-4 py-3 dark:border-slate-700 dark:bg-slate-900">
                <div className="flex gap-1">
                  <div className="h-2 w-2 animate-bounce rounded-full bg-gray-400 dark:bg-slate-400" style={{ animationDelay: '0ms' }} />
                  <div className="h-2 w-2 animate-bounce rounded-full bg-gray-400 dark:bg-slate-400" style={{ animationDelay: '150ms' }} />
                  <div className="h-2 w-2 animate-bounce rounded-full bg-gray-400 dark:bg-slate-400" style={{ animationDelay: '300ms' }} />
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </main>

      {/* Input */}
      <footer className="border-t border-gray-200 bg-white px-6 py-4 dark:border-slate-700 dark:bg-slate-900">
        <form onSubmit={handleSubmit} className="max-w-4xl mx-auto flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask a question..."
            disabled={loading || apiStatus !== 'ok'}
            className="flex-1 rounded-lg border border-gray-300 bg-white px-4 py-2 text-gray-900 outline-none transition focus:ring-2 focus:ring-indigo-500 disabled:bg-gray-100 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:placeholder:text-slate-400 dark:disabled:bg-slate-800/70"
          />
          <button
            type="submit"
            disabled={loading || !input.trim() || apiStatus !== 'ok'}
            className="rounded-lg bg-indigo-600 px-6 py-2 text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-gray-300 dark:disabled:bg-slate-700"
          >
            {loading ? 'Thinking...' : 'Ask'}
          </button>
        </form>
      </footer>
      </div>
    </div>
  );
}
