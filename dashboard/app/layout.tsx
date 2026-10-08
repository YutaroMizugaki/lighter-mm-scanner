import type { Metadata } from "next";
import "./globals.css";
import AppFooter from "@/components/AppFooter";
import AppHeader from "@/components/AppHeader";

export const metadata: Metadata = {
  title: "Lighter 市場リサーチ — マーケットメイク分析",
  description:
    "Lighterの市場をスプレッド、流動性、約定シミュレーションから比較。マーケットメイクの調査候補を見つける分析ツール。",
  openGraph: {
    title: "Lighter 市場リサーチ — マーケットメイク分析",
    description:
      "スプレッド、流動性、約定シミュレーションから、Lighterの市場を比較。",
    type: "website",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>
        <main className="app-shell">
          <AppHeader />
          {children}
          <AppFooter />
        </main>
      </body>
    </html>
  );
}
