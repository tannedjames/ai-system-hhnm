import './globals.css';

export const metadata = {
  title: 'RAG Academic Search',
  description: 'Ask questions about your documents',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}