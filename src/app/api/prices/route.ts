import { fetchAllQuotes } from "@/lib/providers";

export const dynamic = "force-dynamic";

export async function GET() {
  const quotes = await fetchAllQuotes();
  return Response.json({
    fetchedAt: new Date().toISOString(),
    quotes,
  });
}
