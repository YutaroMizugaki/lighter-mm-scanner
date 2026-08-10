import { fmtEstimatedFill, fmtSampleQuality } from "@/lib/format";
import { ESTIMATED_FILL_TOOLTIP } from "@/lib/marketMetrics";
import { TOOLTIPS } from "@/lib/public";
import QualityChip from "./QualityChip";

type Props = {
  rate: number | null | undefined;
  quality?: string | null;
  compact?: boolean;
  showQuality?: boolean;
  digits?: number;
  title?: string;
  className?: string;
};

/**
 * Preserves Estimated Fill semantics:
 * - quality insufficient → "—" + Insufficient (never "0%")
 * - rate null/undefined → "—"
 * - measured 0.0 → "0%"
 */
export function EstimatedFillValue({
  rate,
  quality,
  compact = false,
  showQuality = true,
  digits = 0,
  title,
  className,
}: Props) {
  const tooltip = title ?? TOOLTIPS.estimatedFill ?? ESTIMATED_FILL_TOOLTIP;
  const value = fmtEstimatedFill(rate, quality, digits);
  const display =
    quality === "insufficient"
      ? "—"
      : value === "Insufficient"
        ? "—"
        : value;

  if (compact) {
    return (
      <span className={`est-fill compact tabular${className ? ` ${className}` : ""}`} title={tooltip}>
        <span className="est-fill-value">{display}</span>
        {showQuality && (
          <span className="est-fill-quality muted">
            {" "}
            {fmtSampleQuality(quality)}
          </span>
        )}
      </span>
    );
  }

  return (
    <div className={`est-fill${className ? ` ${className}` : ""}`} title={tooltip}>
      <div className="est-fill-value tabular">{display}</div>
      {showQuality && <QualityChip quality={quality} />}
    </div>
  );
}

export default EstimatedFillValue;
