'use client';

import { useState, useRef, useEffect, useCallback } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

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

interface Document {
  id: number;
  name: string;
  size_mb: number;
  type: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  chunks_count: number;
  uploaded_at: string;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [apiStatus, setApiStatus] = useState<'checking' | 'ok' | 'error'>('checking');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const [documents, setDocuments] = useState<Document[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

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

  // Load documents
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
    const interval = setInterval(loadDocuments, 5000);
    return () => clearInterval(interval);
  }, [loadDocuments]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

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
          setUploadMsg(`✓ Uploaded ${file.name}`);
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
    loadDocuments();
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const question = input.trim();
    setInput('');
    setLoading(true);

    const userMsg: Message = {
      role: 'user',
      content: question,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const res = await fetch(`${API_URL}/api/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, top_k: 5 }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.message || `HTTP ${res.status}`);
      }

      const data = await res.json();

      const assistantMsg: Message = {
        role: 'assistant',
        content: data.answer || data.message || 'No response',
        sources: data.sources || [],
        timestamp: data.timestamp || new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      const errorMsg: Message = {
        role: 'assistant',
        content: `Error: ${msg}`,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  }

  const completedDocs = documents.filter((d) => d.status === 'completed');

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">RAG Academic Search</h1>
            <p className="text-sm text-gray-500">Ask questions about your documents</p>
          </div>
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
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Left sidebar */}
        <aside className="w-72 bg-white border-r border-gray-200 flex flex-col overflow-hidden">
          <div className="p-4 border-b border-gray-200">
            <h2 className="text-sm font-semibold text-gray-900 mb-3">Upload documents</h2>
            <div
              onClick={() => fileInputRef.current?.click()}
              className="border-2 border-dashed border-gray-300 rounded-lg p-4 text-center cursor-pointer hover:border-indigo-500 transition"
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
              <p className="text-sm text-gray-600">
                {uploading ? 'Uploading...' : 'Click to upload'}
              </p>
              <p className="text-xs text-gray-400 mt-1">PDF, TXT, DOCX, MD</p>
            </div>
            {uploadMsg && (
              <p className="mt-2 text-xs text-gray-700">{uploadMsg}</p>
            )}
          </div>

          <div className="flex-1 overflow-y-auto p-4">
            <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">
              Your documents ({completedDocs.length})
            </h3>
            {documents.length === 0 ? (
              <p className="text-sm text-gray-400">No documents yet.</p>
            ) : (
              <div className="space-y-2">
                {documents.map((doc) => (
                  <div
                    key={doc.id}
                    className="flex items-center justify-between p-2 rounded-lg border border-gray-100 bg-gray-50"
                  >
                    <div className="min-w-0">
                      <p className="text-sm text-gray-900 truncate" title={doc.name}>
                        {doc.name}
                      </p>
                      <p className="text-xs text-gray-500">
                        {doc.size_mb} MB · {doc.status}
                      </p>
                    </div>
                    <span
                      className={`ml-2 w-2 h-2 rounded-full flex-shrink-0 ${
                        doc.status === 'completed'
                          ? 'bg-green-500'
                          : doc.status === 'failed'
                          ? 'bg-red-500'
                          : 'bg-yellow-500'
                      }`}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>

        {/* Main chat */}
        <main className="flex-1 flex flex-col overflow-hidden bg-gray-50">
          <div className="flex-1 overflow-y-auto px-6 py-8">
            <div className="max-w-3xl mx-auto space-y-6">
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
                </div>
              )}

              {messages.map((msg, idx) => (
                <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div
                    className={`max-w-2xl rounded-lg px-4 py-3 ${
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
                                  📄 {src.file} {src.page > 0 && `· page ${src.page}`}
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
          </div>

          {/* Input */}
          <footer className="bg-white border-t border-gray-200 px-6 py-4">
            <form onSubmit={handleSubmit} className="max-w-3xl mx-auto flex gap-2">
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
        </main>
      </div>
    </div>
  );
}
