"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { CollectionHealth as Health } from "@/lib/dataHealth";
import { fmtJst } from "@/lib/format";
import Icon from "./Icon";

const labels = { ok: "正常", warning: "要確認", error: "問題あり", unknown: "不明" };

export default function CollectionHealth({ initial, children }: { initial: Health; children?: ReactNode }) {
  const [health, setHealth] = useState(initial);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const active = useRef<AbortController | null>(null);

  async function refresh() {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    const timeout = setTimeout(() => controller.abort(), 15_000);
    try {
      const response = await fetch("/api/data-health", { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error("Health unavailable");
      const result: Health = await response.json();
      setHealth(result);
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      clearTimeout(timeout);
      active.current = null;
      setLoading(false);
    }
  }

  useEffect(() => {
    const interval = setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 60_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
      active.current?.abort();
    };
  }, []);

  const level = failed ? "unknown" : health.checks.some((c) => c.level === "error") ? "error" : health.checks.some((c) => c.level === "warning") ? "warning" : health.checks.some((c) => c.level === "unknown") ? "unknown" : "ok";
  const market = health.checks.find((c) => c.id === "market");

  return (
    <>
    {!failed && (market?.level === "warning" || market?.level === "error") && <aside className="notice data-impact-notice" role="status">収集した市場データの更新が遅れています。表示中の集計は最新の市場状況を反映していない可能性があります。<a href="#collection-health">データの状態を見る</a></aside>}
    {children}
    <section id="collection-health" className="collection-health" aria-labelledby="collection-health-heading">
      <h2 id="collection-health-heading" className="sr-only">データ収集の状態</h2>
      <details className="health-disclosure">
        <summary><span className={`health-dot health-${level}`} aria-hidden="true" /><span>データの状態</span><strong className={`health-${level}`}>{failed ? "未確認" : labels[level]}</strong><span className="health-checked">確認 {fmtJst(health.checkedAt)}</span><Icon name="chevron" /></summary>
      <div className="collection-health-heading">
        <p className="muted">同期・保存済み市場データ・接続の詳細</p>
        <button className="health-refresh" type="button" onClick={() => void refresh()} disabled={loading} aria-label={loading ? "確認中" : "状態を再確認"}>
          <Icon name="refresh" className={loading ? "is-spinning" : undefined} />
          <span>{loading ? "確認中…" : "再確認"}</span>
        </button>
      </div>
      <div className="health-alerts" aria-live="polite">
        {failed && <p className="collection-health-warning">最新の状態を取得できませんでした。以下は前回確認時の情報です。</p>}
        {health.issues.length > 0 && <ul className="compact collection-health-warning">{health.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
      </div>
        <dl className="diagnostics-grid" aria-live="polite" aria-busy={loading}>
          {health.checks.map((check) => (
            <div key={check.id}>
              <dt>{check.label}</dt>
              <dd>
                <strong className={`health-${failed ? "unknown" : check.level}`}>{failed ? "未確認" : labels[check.level]}</strong>
                <span className="collection-health-detail">{check.detail}</span>
                <span className="collection-health-time">{check.timestamp ? fmtJst(check.timestamp) : "時刻不明"}</span>
              </dd>
            </div>
          ))}
        </dl>
        <p className="muted notice-meta">60秒ごとに自動確認。再確認すると最新の状態を取得します。</p>
      </details>
    </section>
    </>
  );
}
