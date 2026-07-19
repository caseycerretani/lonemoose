import { PriceProvider } from "@/lib/types";
import { awsProvider } from "./aws";
import { gcpProvider } from "./gcp";
import { azureProvider } from "./azure";
import { lambdaLabsProvider } from "./lambdaLabs";
import { runpodProvider } from "./runpod";
import { vastaiProvider } from "./vastai";
import { coreweaveProvider } from "./coreweave";

export const providers: PriceProvider[] = [
  awsProvider,
  gcpProvider,
  azureProvider,
  lambdaLabsProvider,
  runpodProvider,
  vastaiProvider,
  coreweaveProvider,
];

export async function fetchAllQuotes() {
  const results = await Promise.allSettled(
    providers.map((provider) => provider.fetchQuotes()),
  );

  return results.flatMap((result, i) => {
    if (result.status === "fulfilled") return result.value;
    console.error(`Provider ${providers[i].id} failed:`, result.reason);
    return [];
  });
}
