import './globals.css';

export const metadata = {
  title: 'RAG Admin',
  description: 'Manage documents and system settings',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}