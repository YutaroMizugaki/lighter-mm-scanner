import Link from "next/link";

export default function AppFooter() {
  return (
    <footer className="app-footer">
      <div className="app-footer-brand">
        <strong>Lighter MM Scanner</strong>
        <p>Independent, read-only market research dashboard.</p>
      </div>
      <ul className="app-footer-points">
        <li>No wallet connection.</li>
        <li>No trading execution.</li>
        <li>Not financial advice.</li>
        <li>Data and estimates may be incomplete or delayed.</li>
      </ul>
      <nav className="app-footer-links" aria-label="Footer">
        <Link href="/#methodology">Methodology</Link>
        <a href="https://apidocs.lighter.xyz/" target="_blank" rel="noopener noreferrer">
          Lighter
        </a>
      </nav>
    </footer>
  );
}
