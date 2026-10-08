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
        <strong className="freshness-label">{copy.level === "current" ? "集計を更新済み" : copy.label}</strong>
        <span className="freshness-detail">{copy.detail}</span>
        {jst !== "—" && <span className="freshness-jst muted">{jst}</span>}
      </span>
      <span className="sr-only">データ状態: {{ current: "最新", delayed: "遅延", unavailable: "利用不可" }[copy.level as FreshnessLevel]}</span>
    </div>
  );
}
