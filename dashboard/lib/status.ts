import type { AnalysisStatus, CollectorStatus, Overview } from "./types";

function ageMinutes(stamp: string | null | undefined): number | null {
  if (!stamp) return null;
  const ageMin = (Date.now() - new Date(stamp).getTime()) / 60_000;
  return Number.isNaN(ageMin) ? null : ageMin;
}

function wsIsDegraded(ws: CollectorStatus["ws"]): boolean {
  if (!ws) return false;
  const connected = ws.connected_shards ?? 0;
  const total = ws.total_shards ?? 0;
  const planned = ws.planned_channels ?? ws.subscribed_channels ?? 0;
  const acked = ws.acked_channels ?? ws.subscribed_channels ?? 0;
  if (total > 0 && connected < total) return true;
  if (planned > 0 && acked < planned) return true;
  return false;
}

/**
 * Recompute collector status from durable market-event freshness, sync health, and WS health.
 */
export function effectiveCollectorStatus(
  collector: CollectorStatus,
  okMinutes = 20,
  warnMinutes = 40,
): string {
  const baked = collector.status || "ERROR";
  if (baked === "COMPLETED" || baked === "ERROR") return baked;

  const syncDegraded =
    (collector.consecutive_sync_failures ?? 0) > 0 || Boolean(collector.last_sync_error);
  const operationalDegraded = syncDegraded || wsIsDegraded(collector.ws) || baked === "DEGRADED";

  const eventStamp = collector.last_durable_event_at || null;
  if (!eventStamp) {
    if (syncDegraded) return "DEGRADED";
    return baked === "DEGRADED" ? "DEGRADED" : "STALE";
  }

  const eventAge = ageMinutes(eventStamp);
  if (eventAge === null) return "ERROR";
  if (eventAge > warnMinutes) return "OFFLINE";
  if (eventAge > okMinutes) return "STALE";
  if (operationalDegraded) return "DEGRADED";
  if (baked === "COLLECTING" || baked === "STALE" || baked === "OFFLINE") return "COLLECTING";
  return baked;
}

/**
 * Analysis freshness from analysis_status.json (independent of collector).
 */
export function effectiveAnalysisStatus(
  analysis: AnalysisStatus | null,
  staleMinutes = 30,
  runningStaleMinutes = 30,
): { status: string; stale: boolean } {
  if (!analysis) return { status: "NOT_STARTED", stale: true };
  if (analysis.status === "NOT_STARTED") return { status: "NOT_STARTED", stale: true };
  if (analysis.status === "NO_ACTIVE_RUN") return { status: "NO_ACTIVE_RUN", stale: false };
  if (analysis.status === "ERROR") return { status: "ERROR", stale: false };

  if (analysis.status === "RUNNING") {
    const runningStamp = analysis.started_at || analysis.generated_at || null;
    const runningAge = ageMinutes(runningStamp);
    if (runningAge !== null && runningAge > runningStaleMinutes) {
      return { status: "STALE", stale: true };
    }
    return { status: "RUNNING", stale: false };
  }

  const stamp =
    analysis.last_successful_analysis_at || analysis.generated_at || null;
  const ageMin = ageMinutes(stamp);
  const stale = ageMin !== null && ageMin > staleMinutes;

  if (analysis.status === "DEGRADED") {
    if (stale) return { status: "STALE", stale: true };
    return { status: "DEGRADED", stale: false };
  }
  if (stale && analysis.status === "OK") {
    return { status: "STALE", stale: true };
  }
  if (analysis.status === "OK") return { status: "OK", stale: false };
  return { status: analysis.status, stale };
}

/** Timestamp to display for last successful analysis (OK or preserved after ERROR). */
export function analysisDisplayTimestamp(
  analysis: AnalysisStatus | null,
): string | null {
  if (!analysis) return null;
  return (
    analysis.last_successful_analysis_at ||
    (analysis.status === "OK" || analysis.status === "DEGRADED"
      ? analysis.generated_at
      : null)
  );
}

/**
 * Recompute status from analysis latest.json generated_at (legacy overview).
 */
export function effectiveStatus(
  overview: Overview,
  okMinutes = 20,
  warnMinutes = 40,
): string {
  const baked = overview.status || "ERROR";
  if (baked === "COMPLETED" || baked === "ERROR") return baked;
  const stamp = overview.last_successful_flush || overview.last_update || null;
  if (!stamp) return "ERROR";
  const ageMin = ageMinutes(stamp);
  if (ageMin === null) return "ERROR";
  const analyzed = overview.markets_analyzed ?? overview.markets ?? 0;
  const samples = overview.samples_written ?? 0;
  if (ageMin > warnMinutes) return "OFFLINE";
  if (ageMin > okMinutes) return "STALE";
  if (baked === "DEGRADED") return "DEGRADED";
  if (analyzed === 0 && samples > 0) return "DEGRADED";
  // Fresh flush with markets: trust COLLECTING even if baked status lagged.
  if (analyzed > 0 && (baked === "OFFLINE" || baked === "STALE")) return "COLLECTING";
  return baked;
}

export function statusHealthNote(
  status: string,
  scope: "collector" | "analysis" = "collector",
): string | null {
  if (scope === "collector") {
    if (status === "OFFLINE") {
      return "Market data has not been durably collected for >40m.";
    }
    if (status === "STALE") {
      return "Latest durable market event is older than 20m.";
    }
    if (status === "DEGRADED") {
      return "Collector degraded — check sync failures, durable event freshness, or WebSocket health.";
    }
    return null;
  }
  if (status === "NOT_STARTED") {
    return "Analyzer has not published a status yet.";
  }
  if (status === "NO_ACTIVE_RUN") {
    return "No active collector run is available to analyze.";
  }
  if (status === "ERROR") {
    return "The latest analysis run failed. Prior valid results may still be shown.";
  }
  if (status === "DEGRADED") {
    return "Some source data could not be processed. The latest valid analysis is still shown.";
  }
  if (status === "STALE") {
    return "Analysis results are older than 30m (expected cadence: 30m).";
  }
  return null;
}
