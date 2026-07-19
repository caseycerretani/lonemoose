import { GpuSpotQuote, PriceProvider } from "@/lib/types";
import { makeQuote } from "./mockUtils";

const REGIONS: Array<[string, number]> = [
  ["us-central1", 1.98],
  ["europe-west4", 2.11],
  ["asia-southeast1", 2.05],
];

export const gcpProvider: PriceProvider = {
  id: "gcp",
  name: "Google Cloud",
  async fetchQuotes(): Promise<GpuSpotQuote[]> {
    return REGIONS.map(([region, base]) =>
      makeQuote("gcp", "Google Cloud", region, base, 0.1),
    );
  },
};
