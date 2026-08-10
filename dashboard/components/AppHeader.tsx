import Link from "next/link";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/markets", label: "Markets" },
  { href: "/candidates", label: "Candidates" },
  { href: "/#methodology", label: "Methodology" },
] as const;

export default function AppHeader() {
  return (
    <header className="app-header">
      <div className="app-header-brand">
        <div className="app-header-titles">
          <Link href="/" className="app-brand-link">
            <span className="app-brand">Lighter MM Scanner</span>
          </Link>
          <p className="app-tagline">Market-making opportunity research for Lighter</p>
        </div>
        <span className="read-only-badge" title="No wallet connection or trading">
          Read-only research
        </span>
      </div>
      <nav className="app-nav" aria-label="Primary">
        {NAV.map((item) => (
          <Link key={item.href} href={item.href}>
            {item.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
