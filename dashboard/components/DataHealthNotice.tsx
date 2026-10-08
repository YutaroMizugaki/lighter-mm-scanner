import { fmtJst } from "@/lib/format";
import { publicCorruptFilesNotice, publicHealthWarning } from "@/lib/public";

type Props = {
  show: boolean;
  primaryMessages: string[];
  corruptSkipped?: number;
  lastAnalysisAt?: string | null;
  analysisError?: boolean;
  marketDataFetchFailed?: boolean;
};

export default function DataHealthNotice({
  show,
  primaryMessages,
  corruptSkipped = 0,
  lastAnalysisAt = null,
  analysisError = false,
  marketDataFetchFailed = false,
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
    "一部のデータを処理できませんでした。表示中の分析結果は、処理できたデータに基づいています。";
  const rest = unique.filter((m) => m !== primary);

  return (
    <section className="notice" aria-labelledby="data-health-heading">
      <h2 id="data-health-heading" className="notice-title">
        データ品質のお知らせ
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
          最後に確認できた分析結果: {fmtJst(lastAnalysisAt)}
        </p>
      )}
      {marketDataFetchFailed && (
        <p className="muted notice-meta">
          この画面の市場集計データを読み込めませんでした。
        </p>
      )}
    </section>
  );
}
