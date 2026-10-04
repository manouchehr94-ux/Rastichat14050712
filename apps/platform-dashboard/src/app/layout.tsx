import './globals.css';

export const metadata = {
  title: 'RastiChat Platform',
  description: 'Platform Administration Dashboard',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fa" dir="rtl">
      <body className="font-sans">{children}</body>
    </html>
  );
}
