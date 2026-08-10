import type { Metadata } from "next";
import "./globals.css";
import AppFooter from "@/components/AppFooter";
import AppHeader from "@/components/AppHeader";

export const metadata: Metadata = {
  title: "Lighter MM Scanner — Market Making Research",
  description:
    "Research Lighter markets using spread, liquidity, estimated maker fill, maker markout and data quality.",
  openGraph: {
    title: "Lighter MM Scanner — Market Making Research",
    description:
      "Research Lighter markets using spread, liquidity, estimated maker fill, maker markout and data quality.",
    type: "website",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
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
