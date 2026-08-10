import Link from "next/link";

export default function Hero() {
  return (
    <section className="hero" aria-labelledby="hero-heading">
      <h1 id="hero-heading">Find markets worth researching for market making.</h1>
      <p className="hero-sub">
        Lighter MM Scanner ranks markets using spread, estimated maker fill, liquidity, trade
        activity, maker markout and data quality.
      </p>
      <p className="hero-support">
        Built from public market data. No wallet connection or API key required.
      </p>
      <div className="hero-cta">
        <Link className="btn btn-primary" href="/markets">
          Explore Markets
        </Link>
        <Link className="btn btn-secondary" href="/candidates">
          View Candidates
        </Link>
      </div>
    </section>
  );
}
