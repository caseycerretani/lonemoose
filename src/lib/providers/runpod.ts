import { GpuSpotQuote, PriceProvider } from "@/lib/types";
import { makeQuote } from "./mockUtils";

const REGIONS: Array<[string, number]> = [
  ["us-east", 1.29],
  ["eu-central", 1.38],
];

export const runpodProvider: PriceProvider = {
  id: "runpod",
  name: "RunPod",
  async fetchQuotes(): Promise<GpuSpotQuote[]> {
    return REGIONS.map(([region, base]) =>
      makeQuote("runpod", "RunPod", region, base, 0.2),
    );
  },
};
