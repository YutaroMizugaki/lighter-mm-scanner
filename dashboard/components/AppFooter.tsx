import Link from "next/link";

export default function AppFooter() {
  return (
    <footer className="app-footer">
      <div className="app-footer-brand">
        <strong>Lighter 市場リサーチ</strong>
      </div>
      <nav className="app-footer-links" aria-label="関連情報">
        <Link href="/#methodology">スコアについて</Link>
        <a href="https://apidocs.lighter.xyz/" target="_blank" rel="noopener noreferrer">
          Lighter 公式資料 ↗
        </a>
      </nav>
    </footer>
  );
}
