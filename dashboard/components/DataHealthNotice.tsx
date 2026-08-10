import { fmtJst } from "@/lib/format";
import { publicCorruptFilesNotice, publicHealthWarning } from "@/lib/public";

type Props = {
  show: boolean;
  primaryMessages: string[];
  corruptSkipped?: number;
  lastAnalysisAt?: string | null;
  analysisError?: boolean;
  marketsFetchFailed?: boolean;
};

export default function DataHealthNotice({
  show,
  primaryMessages,
  corruptSkipped = 0,
  lastAnalysisAt = null,
  analysisError = false,
  marketsFetchFailed = false,
}: Props) {
  if (!show) return null;

  const messages = primaryMessages
    .map((m) => publicHealthWarning(m))
    .filter(Boolean);
  const unique = [...new Set(messages)];
  const corruptNotice = publicCorruptFilesNotice(corruptSkipped);
  const primary =
    corruptNotice ||
    unique[0] ||
    "Some source data could not be processed. The latest valid analysis is still shown.";
  const rest = unique.filter((m) => m !== primary);

  return (
    <section className="notice" aria-labelledby="data-health-heading">
      <h2 id="data-health-heading" className="notice-title">
        Data quality notice
      </h2>
      <p>{primary}</p>
      {rest.length > 0 && (
        <ul className="compact">
          {rest.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      )}
      {(analysisError || corruptSkipped > 0) && lastAnalysisAt && (
        <p className="muted notice-meta">
          Latest valid analysis: {fmtJst(lastAnalysisAt)}
        </p>
      )}
      {marketsFetchFailed && (
        <p className="muted notice-meta">
          Market aggregate data could not be loaded for this view.
        </p>
      )}
    </section>
  );
}
