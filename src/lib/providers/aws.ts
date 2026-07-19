import { GpuSpotQuote, PriceProvider } from "@/lib/types";
import { makeQuote } from "./mockUtils";

const REGIONS: Array<[string, number]> = [
  ["us-east-1", 2.14],
  ["us-west-2", 2.22],
  ["eu-west-1", 2.35],
];

export const awsProvider: PriceProvider = {
  id: "aws",
  name: "AWS EC2",
  async fetchQuotes(): Promise<GpuSpotQuote[]> {
    return REGIONS.map(([region, base]) =>
      makeQuote("aws", "AWS EC2", region, base, 0.12),
    );
  },
};
