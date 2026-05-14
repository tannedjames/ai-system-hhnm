'use client';

import { useState, useRef, useEffect } from 'react';

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

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [apiStatus, setApiStatus] = useState<'checking' | 'ok' | 'error'>('checking');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Check API health on mount
  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((r) => r.json())
      .then(() => setApiStatus('ok'))
      .catch(() => setApiStatus('error'));
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

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
      const res = await fetch(`${API_URL}/api/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, top_k: 5 }),
      });

      const data = await res.json();

      const assistantMsg: Message = {
        role: 'assistant',
        content: data.answer || data.message || 'No response',
        sources: data.sources || [],
        timestamp: data.timestamp || new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
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
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
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
                              📄 {src.file} {src.page > 0 && `· page ${src.page}`}
                            </span>
                            <span className="text-gray-500">
                              {(src.relevance_score * 100).toFixed(0)}% match
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
  );
}