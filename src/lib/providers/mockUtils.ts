import { Availability, GpuSpotQuote } from "@/lib/types";

/** Random walk within +/- pct of a base value, so prices drift plausibly between polls. */
export function jitter(base: number, pct: number): number {
  const delta = base * pct * (Math.random() * 2 - 1);
  return Math.max(0.01, base + delta);
}

export function rollAvailability(weights: {
  available?: number;
  limited?: number;
  unavailable?: number;
}): Availability {
  const available = weights.available ?? 0.8;
  const limited = weights.limited ?? 0.15;
  const roll = Math.random();
  if (roll < available) return "available";
  if (roll < available + limited) return "limited";
  return "unavailable";
}

export function makeQuote(
  providerId: string,
  providerName: string,
  region: string,
  basePrice: number,
  jitterPct: number,
): GpuSpotQuote {
  return {
    providerId,
    providerName,
    region,
    instanceType: "G100.spot",
    priceUsdPerHr: Number(jitter(basePrice, jitterPct).toFixed(3)),
    availability: rollAvailability({}),
    timestamp: new Date().toISOString(),
  };
}
