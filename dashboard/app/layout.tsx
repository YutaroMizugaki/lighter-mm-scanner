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
            READ-ONLY research tool. No trading, wallet, or API keys. Displayed spread × trade
            count ≠ profit — validate fill probability, adverse selection, and inventory risk
            separately before any paper/live MM.
          </p>
        </main>
      </body>
    </html>
  );
}
