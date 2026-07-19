import { GpuSpotQuote, PriceProvider } from "@/lib/types";
import { makeQuote } from "./mockUtils";

const REGIONS: Array<[string, number]> = [
  ["eastus", 2.29],
  ["westeurope", 2.41],
];

export const azureProvider: PriceProvider = {
  id: "azure",
  name: "Microsoft Azure",
  async fetchQuotes(): Promise<GpuSpotQuote[]> {
    return REGIONS.map(([region, base]) =>
      makeQuote("azure", "Microsoft Azure", region, base, 0.13),
    );
  },
};
