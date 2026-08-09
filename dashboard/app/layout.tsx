import type { Metadata } from "next";
import "./globals.css";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Lighter MM Scanner",
  description: "Read-only Lighter market-making opportunity research dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <main>
          <header className="app">
            <h1>Lighter MM Scanner</h1>
            <nav>
              <Link href="/">Overview</Link>
              <Link href="/markets">All Markets</Link>
              <Link href="/candidates">Candidates</Link>
            </nav>
          </header>
          {children}
          <p className="note">
            読み取り専用のリサーチツールです。売買・ウォレット接続・APIキーは使用しません。表示されるスプレッドや取引回数は利益を保証するものではありません。実際のマーケットメイクでは、約定確率・逆選択・在庫リスクを別途検証してください。
          </p>
        </main>
      </body>
    </html>
  );
}
