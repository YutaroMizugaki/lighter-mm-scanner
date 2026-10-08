"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "ホーム" },
  { href: "/markets", label: "市場一覧" },
  { href: "/candidates", label: "候補" },
] as const;

export default function AppHeader() {
  const pathname = usePathname();
  return (
    <header className="app-header">
      <div className="app-header-brand">
        <div className="app-header-titles">
          <Link href="/" className="app-brand-link">
            <span className="app-brand"><span className="brand-mark" aria-hidden="true">L</span>Lighter <span className="brand-caption">市場リサーチ</span></span>
          </Link>
        </div>
      </div>
      <nav className="app-nav" aria-label="メインメニュー">
        {NAV.map((item) => (
          <Link key={item.href} href={item.href} aria-current={(item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)) ? "page" : undefined}>
            {item.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
