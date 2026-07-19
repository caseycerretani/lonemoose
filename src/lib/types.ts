export type Availability = "available" | "limited" | "unavailable";

export interface GpuSpotQuote {
  providerId: string;
  providerName: string;
  region: string;
  instanceType: string;
  priceUsdPerHr: number;
  availability: Availability;
  timestamp: string;
}

export interface PriceProvider {
  id: string;
  name: string;
  fetchQuotes(): Promise<GpuSpotQuote[]>;
}
