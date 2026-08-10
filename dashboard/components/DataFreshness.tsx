import { fmtJst } from "@/lib/format";
import {
  formatRelativeAge,
  publicFreshnessCopy,
  type FreshnessLevel,
} from "@/lib/public";

type Props = {
  status: string;
  lastAnalysisAt: string | null;
  className?: string;
};

export default function DataFreshness({ status, lastAnalysisAt, className }: Props) {
  const relative = formatRelativeAge(lastAnalysisAt);
  const copy = publicFreshnessCopy(status, lastAnalysisAt, relative);
  const jst = fmtJst(lastAnalysisAt);

  return (
    <div
      className={`data-freshness freshness-${copy.level}${className ? ` ${className}` : ""}`}
      title={jst !== "—" ? jst : undefined}
      role="status"
      aria-live="polite"
    >
      <span className="freshness-dot" aria-hidden="true">
        ●
      </span>
      <span className="freshness-copy">
        <strong className="freshness-label">{copy.label}</strong>
        <span className="freshness-detail">{copy.detail}</span>
        {jst !== "—" && <span className="freshness-jst muted">{jst}</span>}
      </span>
      <span className="sr-only">Status level: {copy.level as FreshnessLevel}</span>
    </div>
  );
}
