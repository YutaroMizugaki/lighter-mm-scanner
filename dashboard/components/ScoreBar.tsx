import { fmt } from "@/lib/format";
import { TOOLTIPS } from "@/lib/public";

type Props = {
  score: number | null | undefined;
  digits?: number;
};

export default function ScoreBar({ score, digits = 1 }: Props) {
  if (score === null || score === undefined || Number.isNaN(score)) {
    return <span className="tabular">—</span>;
  }
  const width = Math.max(0, Math.min(100, score));
  return (
    <div className="score-cell" title={TOOLTIPS.score}>
      <span className="score-num tabular">{fmt(score, digits)}</span>
      <span className="score-bar" aria-hidden="true">
        <span className="score-bar-fill" style={{ width: `${width}%` }} />
      </span>
    </div>
  );
}
