import { GpuSpotQuote, PriceProvider } from "@/lib/types";
import { makeQuote } from "./mockUtils";

const REGIONS: Array<[string, number]> = [
  ["us-east", 1.72],
  ["us-west", 1.68],
];

export const coreweaveProvider: PriceProvider = {
  id: "coreweave",
  name: "CoreWeave",
  async fetchQuotes(): Promise<GpuSpotQuote[]> {
    return REGIONS.map(([region, base]) =>
      makeQuote("coreweave", "CoreWeave", region, base, 0.1),
    );
  },
};
