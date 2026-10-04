import { Vazirmatn } from 'next/font/google';
import './globals.css';

const vazirmatn = Vazirmatn({
  subsets: ['arabic', 'latin'],
  weight: ['400', '500', '600', '700', '800'],
  variable: '--font-vazirmatn',
});

export const metadata = {
  title: 'RastiChat Operator',
  description: 'Workspace Operator Dashboard',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fa" dir="rtl" className={vazirmatn.variable}>
      <body className="font-sans">{children}</body>
    </html>
  );
}
