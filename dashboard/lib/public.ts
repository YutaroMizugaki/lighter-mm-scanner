/**
 * Public-facing presentation helpers.
 * Sanitizes operational details so they never appear in the public DOM.
 */

const SENSITIVE_PATTERNS: RegExp[] = [
  /gs:\/\/[^\s`'")\]]+/gi,
  /storage\.googleapis\.com\/[^\s`'")\]]+/gi,
  /\/(?:tmp|var|mnt|home|Users|opt|workspace|app)\/[^\s`'")\]]+/gi,
  /[A-Za-z]:\\[^\s`'")\]]+/gi,
  /\bNEXT_PUBLIC_[A-Z0-9_]+\b/g,
  /\b[A-Z][A-Z0-9_]*(?:_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIAL|_IAM)\b/g,
  /\b(?:GCS_BUCKET|BUCKET|CORS|IAM)\b/gi,
  /\blatest\.json\b/gi,
  /\bmarkets\.json\b/gi,
  /\bcollector_status\.json\b/gi,
  /\banalysis_status\.json\b/gi,
  /\bTraceback\b[\s\S]{0,2000}/gi,
  /\bat\s+\S+\s+\(.*?:\d+:\d+\)/gi,
  /\bhttps?:\/\/(?:127\.0\.0\.1|localhost|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[0-1])\.\d+\.\d+)[^\s`'")\]]*/gi,
  /\b(?:Bearer|token|secret|credential|password)\s*[:=]\s*\S+/gi,
];

export function containsSensitiveText(text: string | null | undefined): boolean {
  if (!text) return false;
  return SENSITIVE_PATTERNS.some((re) => {
    re.lastIndex = 0;
    return re.test(text);
  });
}

/** Strip paths, bucket names, env vars, stack traces from any public string. */
export function sanitizePublicText(
  text: string | null | undefined,
  fallback = "内部情報は表示していません。",
): string {
  if (!text) return fallback;
  let out = text;
  for (const re of SENSITIVE_PATTERNS) {
    re.lastIndex = 0;
    out = out.replace(re, "[redacted]");
  }
  out = out.replace(/\s{2,}/g, " ").trim();
  if (!out || out === "[redacted]" || /^[\[\]redacted\s.,;:-]+$/i.test(out)) {
    return fallback;
  }
  if (containsSensitiveText(out)) return fallback;
  return out;
}

export function publicDataUnavailableMessage(kind: "overview" | "markets" | "market" | "config" = "overview"): {
  title: string;
  body: string;
} {
  if (kind === "config") {
    return {
      title: "市場データを一時的に取得できません。",
      body: "最新の分析データを読み込めませんでした。しばらくしてから再度お試しください。",
    };
  }
  if (kind === "markets") {
    return {
      title: "市場データを一時的に取得できません。",
      body: "最新の分析データを読み込めませんでした。しばらくしてから再度お試しください。",
    };
  }
  if (kind === "market") {
    return {
      title: "この市場の詳細を取得できません。",
      body: "最新の分析データから市場情報を読み込めませんでした。しばらくしてから再度お試しください。",
    };
  }
  return {
    title: "市場データを一時的に取得できません。",
    body: "最新の分析データを読み込めませんでした。しばらくしてから再度お試しください。",
  };
}

export function publicAnalysisPendingMessage(): { title: string; body: string } {
  return {
    title: "分析データはまだありません。",
    body: "次回の分析結果が公開されるまでお待ちください。",
  };
}

export type FreshnessLevel = "current" | "delayed" | "unavailable";

const FRESHNESS_STALE_MINUTES = 30;

function analysisAgeMinutes(
  iso: string | null | undefined,
  now = Date.now(),
): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return (now - t) / 60_000;
}

export function analysisFreshnessLevel(
  status: string,
  lastAnalysisAt?: string | null,
  now = Date.now(),
  staleMinutes = FRESHNESS_STALE_MINUTES,
): FreshnessLevel {
  if (status === "RUNNING") {
    if (!lastAnalysisAt) return "unavailable";
    const age = analysisAgeMinutes(lastAnalysisAt, now);
    if (age === null) return "unavailable";
    if (age > staleMinutes) return "delayed";
    return "current";
  }
  if (status === "OK" || status === "DEGRADED" || status === "COMPLETED") {
    return "current";
  }
  if (status === "STALE") return "delayed";
  return "unavailable";
}

/** Map analysis freshness to public labels. Uses lastAnalysisAt age for RUNNING. */
export function publicFreshnessCopy(
  status: string,
  lastAnalysisAt: string | null,
  relative: string,
  now = Date.now(),
): { level: FreshnessLevel; label: string; detail: string } {
  const level = analysisFreshnessLevel(status, lastAnalysisAt, now);

  if (status === "RUNNING") {
    const runningNote = lastAnalysisAt ? "分析を更新中" : "分析を実行中です";
    if (level === "current") {
      return {
        level,
        label: "データは最新",
        detail: lastAnalysisAt
          ? `${relative}に更新 · ${runningNote}`
          : runningNote,
      };
    }
    if (level === "delayed") {
      return {
        level,
        label: "データ更新が遅延",
        detail: lastAnalysisAt
          ? `最新の分析は${relative} · ${runningNote}`
          : runningNote,
      };
    }
    return {
      level: "unavailable",
      label: "データを利用できません",
      detail: runningNote,
    };
  }

  if (level === "current") {
    return {
      level,
      label: "データは最新",
      detail: lastAnalysisAt ? `${relative}に更新` : "最新の分析結果を利用できます",
    };
  }
  if (level === "delayed") {
    return {
      level,
      label: "データ更新が遅延",
      detail: lastAnalysisAt
        ? `最新の分析は${relative}`
        : "最新の分析結果が通常より古くなっています",
    };
  }
  return {
    level,
    label: "データを利用できません",
    detail: "現在利用できる分析結果がありません",
  };
}

export function formatRelativeAge(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "不明";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "不明";
  const mins = Math.max(0, Math.round((now - t) / 60_000));
  if (mins < 1) return "たった今";
  if (mins === 1) return "1分前";
  if (mins < 60) return `${mins}分前`;
  const hours = Math.round(mins / 60);
  if (hours === 1) return "1時間前";
  if (hours < 48) return `${hours}時間前`;
  const days = Math.round(hours / 24);
  return days === 1 ? "1日前" : `${days}日前`;
}

export function formatUsdCompact(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(digits)}M`;
  if (abs >= 1_000) return `$${(n / 1_000).toFixed(digits)}k`;
  return `$${n.toFixed(0)}`;
}

export function formatDepth(n: number | null | undefined): string {
  return formatUsdCompact(n, 1);
}

export function formatActivity(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${n.toFixed(1)}/min`;
}

export function rankLabel(letter: string | null | undefined): string {
  switch (letter) {
    case "A":
      return "有望";
    case "B":
      return "良好";
    case "C":
      return "要確認";
    case "D":
      return "低評価";
    default:
      return "";
  }
}

export function rankTooltip(letter: string | null | undefined): string {
  switch (letter) {
    case "A":
      return "A — 調査候補として特に有望";
    case "B":
      return "B — 調査候補として良好";
    case "C":
      return "C — 評価材料にばらつきあり";
    case "D":
      return "D — 根拠が弱い、または不足";
    default:
      return "現在の調査スコアに基づくランク";
  }
}

export function rankSubtext(letter: string | null | undefined, isCandidate: boolean): string {
  if (isCandidate && letter === "A") {
    return "現在の観測期間では有望な候補です。";
  }
  if (isCandidate && letter === "B") {
    return "現在の観測期間では良好な候補です。";
  }
  if (isCandidate) {
    return "現在の観測期間で候補の基準を満たしています。";
  }
  if (letter === "C") {
    return "現在の観測期間では評価材料にばらつきがあります。";
  }
  return "現在の観測期間では根拠が弱い、または不足しています。";
}

export function publicCorruptFilesNotice(count: number): string {
  if (count <= 0) return "";
  if (count === 1) return "データファイル1件を処理できませんでした。";
  return `データファイル${count}件を処理できませんでした。`;
}

export function publicHealthWarning(raw: string): string {
  const lower = raw.toLowerCase();
  if (
    lower.includes("corrupt") ||
    lower.includes("parquet") ||
    lower.includes("skipped") ||
    containsSensitiveText(raw)
  ) {
    return "一部のデータを処理できませんでした。表示中の分析結果は、処理できたデータに基づいています。";
  }
  const translated: Record<string, string> = {
    "Some source data could not be processed. The latest valid analysis is still shown.": "一部のデータを処理できませんでした。表示中の分析結果は、処理できたデータに基づいています。",
    "The latest analysis run failed. Prior valid results may still be shown.": "最新の分析に失敗しました。直前の有効な分析結果を表示している場合があります。",
    "Market data has not been durably collected for >40m.": "市場データの保存を40分以上確認できていません。",
    "Latest durable market event is older than 20m.": "保存済みの最新市場データは20分以上前のものです。",
    "Collector degraded — check sync failures, durable event freshness, or WebSocket health.": "データ収集に問題があります。同期状況、市場データの更新時刻、接続状態を確認してください。",
    "Analyzer has not published a status yet.": "分析処理の状態はまだ公開されていません。",
    "No active collector run is available to analyze.": "分析対象となるデータ収集の実行がありません。",
    "Analysis results are older than 30m (expected cadence: 30m).": "分析結果は30分以上前のものです（通常の更新間隔は30分です）。",
  };
  if (translated[raw]) return translated[raw];
  return sanitizePublicText(raw, "データ品質に関する問題を検出しました。");
}

/** Translate public pros, cons, and warnings generated by scoring.build_narratives. */
export function publicAssessmentCopy(raw: string): string {
  const exact: Record<string, string> = {
    "SPREAD: n/a": "スプレッド: データなし",
    "DEPTH: n/a": "板の厚み: データなし",
    "ACTIVITY: n/a": "取引頻度: データなし",
    "MARKOUT: n/a": "約定後の価格変動: データなし",
    "EST. FILL: n/a": "推定約定率: データなし",
    "persistent/usable spread": "安定して利用しやすいスプレッド",
    "meaningful median spread": "中央値で十分なスプレッド",
    "spread >=5bp for substantial fraction of time": "観測時間の多くでスプレッドが5bp以上",
    "high trade frequency (market-level; not Estimated Maker Fill)": "市場全体の取引頻度が高い（推定メイカー約定率とは別指標）",
    "meaningful Estimated Maker Fill @30s conservative ($50)": "保守的条件（50ドル・30秒）で推定約定率が十分にある",
    "sufficient two-sided depth within ±10bp": "仲値±10bp以内に十分な買い・売り板がある",
    "positive 5s maker markout": "5秒後のメイカー約定後価格変動がプラス",
    "positive 30s maker markout": "30秒後のメイカー約定後価格変動がプラス",
    "tight spread — limited edge after fees/latency": "スプレッドが狭く、手数料や遅延を考慮すると優位性が限られる",
    "low trade activity": "取引頻度が低い",
    "Estimated Maker Fill ~0 @30s conservative (touch rarely clears)": "保守的条件（50ドル・30秒）で推定約定率がほぼ0%（最良気配で約定しにくい）",
    "thin two-sided depth": "買い・売り両側の板が薄い",
    "negative 5s maker markout (adverse selection)": "5秒後のメイカー約定後価格変動がマイナス（逆選択の可能性）",
    "elevated 5s volatility vs spread": "5秒間の価格変動がスプレッドに対して大きい",
    "funding relatively high": "ファンディング率が比較的高い",
    "data coverage below 95%": "データ充足率が95%未満",
    "strong penalty: extremely low trade count": "大幅減点: 取引件数が極めて少ない",
    "penalty: two-sided depth very thin": "減点: 買い・売り両側の板が非常に薄い",
    "penalty: median 5s maker markout < 0": "減点: 5秒後のメイカー約定後価格変動の中央値が0未満",
    "strong penalty: 30s markout largely negative": "大幅減点: 30秒後のメイカー約定後価格変動が大きくマイナス",
    "penalty: Estimated Maker Fill ~0 @30s conservative ($50)": "減点: 保守的条件（50ドル・30秒）で推定約定率がほぼ0%",
  };
  if (exact[raw]) return exact[raw];

  const dynamic: Array<[RegExp, (match: RegExpMatchArray) => string]> = [
    [/^SPREAD: median (.+)bp$/, ([, value]) => `スプレッド: 中央値 ${value}bp`],
    [/^DEPTH: \$(.+) within ±10bp$/, ([, value]) => `板の厚み: 仲値±10bp以内に $${value}`],
    [/^ACTIVITY: (.+) trades\/min \(market-level\)$/, ([, value]) => `取引頻度: 1分あたり${value}件（市場全体）`],
    [/^MARKOUT: (.+)bp @5s \/ (.+)bp @30s$/, ([, five, thirty]) => `約定後の価格変動: 5秒 ${five}bp / 30秒 ${thirty}bp`],
    [/^EST\. FILL: (.+)% @30s cons\. \(\$50\)$/, ([, value]) => `推定約定率: ${value}%（保守的条件・50ドル・30秒）`],
    [/^PERSISTENCE: spread >=5bp for (.+)% of observed time$/, ([, value]) => `スプレッドが5bp以上だった観測時間: ${value}%`],
    [/^Estimated Maker Fill sample insufficient \(<(\d+)\)$/, ([, count]) => `推定約定率のサンプル数が不足（${count}件未満）`],
    [/^strong penalty: observation coverage < (\d+)%$/, ([, pct]) => `大幅減点: 観測データの充足率が${pct}%未満`],
  ];
  for (const [pattern, render] of dynamic) {
    const match = raw.match(pattern);
    if (match) return render(match);
  }
  return sanitizePublicText(raw, "分析情報を表示できません。");
}

export function publicConfidenceBreakdownLabel(key: string): string {
  const labels: Record<string, string> = {
    markout: "約定後の価格変動（総合）",
    markout_5s: "約定後の価格変動（5秒）",
    markout_30s: "約定後の価格変動（30秒）",
    estimated_fill: "推定約定率",
    trades: "取引観測",
    coverage: "データ充足率",
    duration: "観測時間",
  };
  return labels[key] ?? sanitizePublicText(key, "その他の項目");
}

export function publicConfidenceReason(reason: string): string {
  const reasons: Record<string, string> = {
    low_markout_samples: "約定後の価格変動データが少ない",
    low_fill_samples: "推定約定率のサンプルが少ない",
    low_trade_observations: "取引観測が少ない",
    low_coverage: "データ充足率が低い",
    short_observation_duration: "観測期間が短い",
    missing_markout_samples: "約定後の価格変動データがありません",
    missing_fill_samples: "推定約定率のデータがありません",
    missing_trade_observations: "取引観測データがありません",
    missing_coverage: "データ充足率を算出できません",
    missing_observation_duration: "観測時間を確認できません",
  };
  return reasons[reason] ?? sanitizePublicText(reason, "信頼度の算出理由を表示できません。");
}

export const TOOLTIPS = {
  makerMarkout:
    "メイカー注文成立後の価格変動です。プラスはメイカーに有利です。",
  estimatedFill:
    "積極的な売買が最良気配の小口メイカー注文に到達するかを推定した値です。実際の約定確率ではありません。ランキングでは50ドル・30秒・保守的な条件を使用します。",
  estimatedEdge:
    "推定約定率 ×（メイカー約定後の価格変動 − メイカー手数料）。参考指標であり、期待利益ではありません。",
  depth10bp: "仲値から上下10ベーシスポイント以内にある、買い・売り両側の注文流動性です。",
  coverage: "観測予定時間のうち、利用可能なデータがある割合です。",
  sampleQuality:
    "推定約定率と価格変動のサンプル信頼度です。「データ不足」は観測数が足りない意味で、実測値0%を示すものではありません。",
  tradesPerMin: "市場ごとの1分あたり約定件数です。推定メイカー約定率とは異なります。",
  score: "市場間の相対的な調査ランクです。期待リターンの予測ではありません。",
  confidence:
    "データ信頼度は、サンプル数・データ充足率・観測時間に基づく信頼性指標です。統計上の信頼区間ではありません。",
  effectiveScore:
    "市場ランキングに使う信頼度調整後スコアです（基礎スコア × データ信頼度）。",
} as const;
