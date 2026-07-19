import { GpuSpotQuote, PriceProvider } from "@/lib/types";
import { makeQuote } from "./mockUtils";

const REGIONS: Array<[string, number]> = [
  ["us", 0.95],
  ["eu", 1.05],
  ["asia", 1.02],
];

export const vastaiProvider: PriceProvider = {
  id: "vastai",
  name: "Vast.ai",
  async fetchQuotes(): Promise<GpuSpotQuote[]> {
    return REGIONS.map(([region, base]) =>
      makeQuote("vastai", "Vast.ai", region, base, 0.3),
    );
  },
};
