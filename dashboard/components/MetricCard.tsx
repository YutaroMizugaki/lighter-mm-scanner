import type { ReactNode } from "react";

type Props = {
  label: string;
  value: ReactNode;
  subtext?: ReactNode;
  title?: string;
  className?: string;
};

export default function MetricCard({ label, value, subtext, title, className }: Props) {
  return (
    <div className={`metric-card${className ? ` ${className}` : ""}`} title={title}>
      <div className="metric-label">{label}</div>
      <div className="metric-value tabular">{value}</div>
      {subtext != null && <div className="metric-sub muted">{subtext}</div>}
    </div>
  );
}
