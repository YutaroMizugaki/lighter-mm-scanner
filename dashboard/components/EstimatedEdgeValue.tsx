import { ESTIMATED_EDGE_TOOLTIP } from "@/lib/marketMetrics";
import { fmt } from "@/lib/format";

type Props = {
  edgeBps: number | null | undefined;
  feeIncluded?: boolean | null;
  digits?: number;
  showFeeLabel?: boolean;
  title?: string;
  className?: string;
};

/** Display Estimated Maker Edge (signed bps; optional fee incl/excl label). */
export function EstimatedEdgeValue({
  edgeBps,
  feeIncluded,
  digits = 2,
  showFeeLabel = false,
  title = ESTIMATED_EDGE_TOOLTIP,
  className,
}: Props) {
  const feeLabel =
    showFeeLabel && feeIncluded === false
      ? " (fee excl.)"
      : showFeeLabel && feeIncluded === true
        ? " (fee incl.)"
        : "";
  return (
    <span className={className} title={title}>
      {fmt(edgeBps, digits, true)}
      {feeLabel ? ` bp${feeLabel}` : ""}
    </span>
  );
}
