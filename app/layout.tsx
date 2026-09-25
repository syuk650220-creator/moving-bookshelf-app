import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "移動本棚ロボ",
  description: "研究室の本を、移動本棚ロボとWebアプリで管理するシステム",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ja"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        {/* セルフホスト版（Pi）で開いているときだけ出す。Vercel 版と取り違えて呼出がロボに届かない事故を防ぐ */}
        {process.env.NEXT_PUBLIC_SUPABASE_URL === "same-origin" && (
          <div className="bg-amber-100 px-3 py-1 text-center text-xs text-amber-900">
            セルフホスト版（ロボの Pi で動作中）
          </div>
        )}
        {children}
      </body>
    </html>
  );
}
