import './globals.css';
import type { ReactNode } from 'react';

export const metadata = {
  title: 'RAG Academic Search',
  description: 'Ask questions about your documents',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}