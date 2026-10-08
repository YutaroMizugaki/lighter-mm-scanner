"use client";

import { useEffect, useRef, useState } from "react";
import type { CollectionHealth as Health } from "@/lib/dataHealth";
import { fmtJst } from "@/lib/format";

const labels = { ok: "正常", warning: "要確認", error: "問題あり", unknown: "不明" };

export default function CollectionHealth({ initial }: { initial: Health }) {
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

  return (
    <section className="panel collection-health" aria-labelledby="collection-health-heading">
      <div className="collection-health-heading">
        <h2 id="collection-health-heading">データ収集の状態</h2>
        <button type="button" onClick={() => void refresh()} disabled={loading}>
          {loading ? "確認中…" : "状態を再確認"}
        </button>
      </div>
      <div aria-live="polite" aria-busy={loading}>
        {failed && <p className="collection-health-warning">最新の状態を取得できませんでした。以下は前回確認時の情報です。</p>}
        <dl className="diagnostics-grid">
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
        {health.issues.length > 0 && <ul className="compact collection-health-warning">{health.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
      </div>
      <p className="muted notice-meta">確認: {fmtJst(health.checkedAt)} · 60秒ごとに自動確認。再確認は状態の取得のみを行います。</p>
    </section>
  );
}
