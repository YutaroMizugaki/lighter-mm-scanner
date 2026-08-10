import Link from "next/link";

const FACTORS = [
  { name: "Trade Activity", weight: "15%" },
  { name: "Estimated Maker Fill", weight: "20%" },
  { name: "Spread", weight: "20%" },
  { name: "Two-sided Depth", weight: "15%" },
  { name: "Maker Markout", weight: "20%" },
  { name: "Data Quality", weight: "10%" },
] as const;

export default function ScoreExplainer() {
  return (
    <section className="panel" id="methodology" aria-labelledby="score-heading">
      <h2 id="score-heading">How the score works</h2>
      <p className="section-lead">
        The score is a relative research ranking — not an expected return forecast.
      </p>
      <ul className="score-factors">
        {FACTORS.map((f) => (
          <li key={f.name}>
            <span>{f.name}</span>
            <span className="tabular muted">{f.weight}</span>
          </li>
        ))}
      </ul>
      <div className="method-callout">
        <h3>Estimated Maker Fill</h3>
        <p>
          Estimated Maker Fill simulates whether aggressive trade flow could clear a small
          maker quote at the touch.
        </p>
        <p className="muted">It is not actual fill probability.</p>
      </div>
      <p className="muted method-more">
        <Link href="/markets">Explore markets →</Link>
      </p>
    </section>
  );
}
