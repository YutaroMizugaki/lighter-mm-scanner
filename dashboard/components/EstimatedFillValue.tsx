import { ESTIMATED_FILL_TOOLTIP } from "@/lib/marketMetrics";
import { fmtEstimatedFill } from "@/lib/format";

type Props = {
  rate: number | null | undefined;
  quality?: string | null;
  digits?: number;
  title?: string;
  className?: string;
};

/** Display Estimated Maker Fill rate (Insufficient / percent / em dash). */
export function EstimatedFillValue({
  rate,
  quality,
  digits = 0,
  title = ESTIMATED_FILL_TOOLTIP,
  className,
}: Props) {
  return (
    <span className={className} title={title}>
      {fmtEstimatedFill(rate, quality, digits)}
    </span>
  );
}
