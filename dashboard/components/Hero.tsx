import Link from "next/link";

export default function Hero() {
  return (
    <section className="hero" aria-labelledby="hero-heading">
      <div className="hero-copy">
        <p className="hero-eyebrow">マーケットメイクの市場分析</p>
        <h1 id="hero-heading">市場の特徴を、ひと目で。</h1>
        <p className="hero-sub">スプレッドや流動性から、調べたい市場を見つけましょう。</p>
      </div>
      <div className="hero-cta">
        <Link className="btn btn-primary" href="/markets">
          市場一覧を見る <span aria-hidden="true">→</span>
        </Link>
      </div>
    </section>
  );
}
