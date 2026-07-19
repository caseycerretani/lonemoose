"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { GpuSpotQuote } from "@/lib/types";
import { formatTime, formatUsd } from "@/lib/format";

const POLL_INTERVAL_MS = 12_000;
const MAX_HISTORY_POINTS = 30;

const PROVIDER_COLORS: Record<string, string> = {
  aws: "#f59e0b",
  gcp: "#4285f4",
  azure: "#0078d4",
  lambda: "#8b5cf6",
  runpod: "#a3e635",
  vastai: "#ef4444",
  coreweave: "#14b8a6",
};

type SortKey = "priceUsdPerHr" | "providerName" | "region" | "availability";

interface HistoryPoint {
  time: string;
  label: string;
  [providerId: string]: number | string;
}

export default function PriceDashboard() {
  const [quotes, setQuotes] = useState<GpuSpotQuote[]>([]);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("priceUsdPerHr");
  const [sortAsc, setSortAsc] = useState(true);
  const [availableOnly, setAvailableOnly] = useState(false);
  const [history, setHistory] = useState<HistoryPoint[]>([]);

  async function loadPrices() {
    try {
      setError(null);
      const res = await fetch("/api/prices", { cache: "no-store" });
      if (!res.ok) throw new Error(`Request failed: ${res.status}`);
      const data: { fetchedAt: string; quotes: GpuSpotQuote[] } =
        await res.json();

      setQuotes(data.quotes);
      setFetchedAt(data.fetchedAt);

      const byProvider = new Map<string, number>();
      for (const q of data.quotes) {
        if (q.availability === "unavailable") continue;
        const current = byProvider.get(q.providerId);
        if (current === undefined || q.priceUsdPerHr < current) {
          byProvider.set(q.providerId, q.priceUsdPerHr);
        }
      }

      const point: HistoryPoint = {
        time: data.fetchedAt,
        label: formatTime(data.fetchedAt),
      };
      for (const [providerId, price] of byProvider) point[providerId] = price;

      setHistory((prev) => [...prev, point].slice(-MAX_HISTORY_POINTS));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load prices");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // Fetching + polling on mount is the documented pattern for effect-driven
    // data sync; the state updates happen after the fetch resolves, not
    // synchronously within this effect body.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadPrices();
    const id = setInterval(loadPrices, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  const filtered = useMemo(() => {
    let rows = quotes;
    if (availableOnly) {
      rows = rows.filter((q) => q.availability !== "unavailable");
    }
    return [...rows].sort((a, b) => {
      const dir = sortAsc ? 1 : -1;
      if (sortKey === "priceUsdPerHr") {
        return (a.priceUsdPerHr - b.priceUsdPerHr) * dir;
      }
      return String(a[sortKey]).localeCompare(String(b[sortKey])) * dir;
    });
  }, [quotes, availableOnly, sortKey, sortAsc]);

  const cheapest = useMemo(() => {
    const available = quotes.filter((q) => q.availability !== "unavailable");
    if (available.length === 0) return null;
    return available.reduce((min, q) =>
      q.priceUsdPerHr < min.priceUsdPerHr ? q : min,
    );
  }, [quotes]);

  const avgPrice = useMemo(() => {
    if (quotes.length === 0) return null;
    const sum = quotes.reduce((acc, q) => acc + q.priceUsdPerHr, 0);
    return sum / quotes.length;
  }, [quotes]);

  const providerIds = useMemo(
    () => Array.from(new Set(quotes.map((q) => q.providerId))),
    [quotes],
  );

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortAsc((prev) => !prev);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <SummaryCard
          label="Cheapest right now"
          value={cheapest ? formatUsd(cheapest.priceUsdPerHr) : "—"}
          sub={cheapest ? `${cheapest.providerName} · ${cheapest.region}` : ""}
          accent="text-emerald-400"
        />
        <SummaryCard
          label="Average across providers"
          value={avgPrice !== null ? formatUsd(avgPrice) : "—"}
          sub={`${quotes.length} quotes`}
        />
        <SummaryCard
          label="Last updated"
          value={fetchedAt ? formatTime(fetchedAt) : "—"}
          sub={loading ? "Refreshing…" : "Auto-refreshes every 12s"}
        />
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-white/70">
            Cheapest-available price trend
          </h2>
        </div>
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={history}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff1a" />
              <XAxis
                dataKey="label"
                stroke="#ffffff66"
                fontSize={12}
                tickLine={false}
              />
              <YAxis
                stroke="#ffffff66"
                fontSize={12}
                tickLine={false}
                width={56}
                tickFormatter={(v: number) => `$${v.toFixed(2)}`}
              />
              <Tooltip
                contentStyle={{
                  background: "#111827",
                  border: "1px solid #ffffff22",
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(value) => formatUsd(Number(value))}
              />
              {providerIds.map((id) => (
                <Line
                  key={id}
                  type="monotone"
                  dataKey={id}
                  name={
                    quotes.find((q) => q.providerId === id)?.providerName ??
                    id
                  }
                  stroke={PROVIDER_COLORS[id] ?? "#94a3b8"}
                  dot={false}
                  strokeWidth={2}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-medium text-white/70">
            All provider quotes
          </h2>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-white/60">
              <input
                type="checkbox"
                checked={availableOnly}
                onChange={(e) => setAvailableOnly(e.target.checked)}
                className="accent-emerald-500"
              />
              Available only
            </label>
            <button
              onClick={loadPrices}
              className="rounded-md border border-white/15 px-3 py-1.5 text-xs font-medium text-white/80 hover:bg-white/10"
            >
              Refresh now
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-white/10 text-xs uppercase tracking-wide text-white/40">
                <Th label="Provider" onClick={() => toggleSort("providerName")} />
                <Th label="Region" onClick={() => toggleSort("region")} />
                <Th label="Price" onClick={() => toggleSort("priceUsdPerHr")} />
                <Th
                  label="Availability"
                  onClick={() => toggleSort("availability")}
                />
              </tr>
            </thead>
            <tbody>
              {filtered.map((q) => {
                const isCheapest =
                  cheapest &&
                  q.providerId === cheapest.providerId &&
                  q.region === cheapest.region;
                return (
                  <tr
                    key={`${q.providerId}-${q.region}`}
                    className={`border-b border-white/5 ${
                      q.availability === "unavailable" ? "opacity-40" : ""
                    } ${isCheapest ? "bg-emerald-500/10" : ""}`}
                  >
                    <td className="py-2.5 pr-4">
                      <span className="flex items-center gap-2">
                        <span
                          className="h-2 w-2 rounded-full"
                          style={{
                            background:
                              PROVIDER_COLORS[q.providerId] ?? "#94a3b8",
                          }}
                        />
                        {q.providerName}
                      </span>
                    </td>
                    <td className="py-2.5 pr-4 text-white/70">{q.region}</td>
                    <td className="py-2.5 pr-4 font-mono">
                      {formatUsd(q.priceUsdPerHr)}
                      {isCheapest && (
                        <span className="ml-2 rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                          BEST
                        </span>
                      )}
                    </td>
                    <td className="py-2.5">
                      <AvailabilityBadge availability={q.availability} />
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && !loading && (
                <tr>
                  <td colSpan={4} className="py-8 text-center text-white/40">
                    No quotes match the current filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <div className="text-xs uppercase tracking-wide text-white/40">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-semibold ${accent ?? ""}`}>
        {value}
      </div>
      {sub && <div className="mt-1 text-xs text-white/40">{sub}</div>}
    </div>
  );
}

function Th({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <th className="cursor-pointer select-none py-2 pr-4" onClick={onClick}>
      {label}
    </th>
  );
}

function AvailabilityBadge({
  availability,
}: {
  availability: GpuSpotQuote["availability"];
}) {
  const styles: Record<GpuSpotQuote["availability"], string> = {
    available: "bg-emerald-500/15 text-emerald-300",
    limited: "bg-amber-500/15 text-amber-300",
    unavailable: "bg-white/10 text-white/40",
  };
  return (
    <span
      className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${styles[availability]}`}
    >
      {availability}
    </span>
  );
}
