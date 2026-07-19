import { GpuSpotQuote, PriceProvider } from "@/lib/types";
import { makeQuote } from "./mockUtils";

const REGIONS: Array<[string, number]> = [
  ["us-east-1", 1.49],
  ["us-west-1", 1.55],
];

export const lambdaLabsProvider: PriceProvider = {
  id: "lambda",
  name: "Lambda Labs",
  async fetchQuotes(): Promise<GpuSpotQuote[]> {
    return REGIONS.map(([region, base]) =>
      makeQuote("lambda", "Lambda Labs", region, base, 0.08),
    );
  },
};
