# lonemoose — G100 Spot Price Tracker

A dashboard for comparing G100 GPU spot instance prices across cloud and
GPU-rental providers in one place.

## What's here

- **Provider abstraction** (`src/lib/providers/`) — each provider (AWS, Google
  Cloud, Azure, Lambda Labs, RunPod, Vast.ai, CoreWeave) implements a common
  `PriceProvider` interface (`fetchQuotes()` → `GpuSpotQuote[]`) defined in
  `src/lib/types.ts`. Providers currently return simulated data so the app
  runs with zero configuration; swap a provider's implementation for a real
  API call (and it'll show up in the dashboard automatically) whenever you
  have API access.
- **`/api/prices`** (`src/app/api/prices/route.ts`) — aggregates all
  providers server-side and returns the combined quote list.
- **Dashboard** (`src/app/page.tsx`, `src/components/PriceDashboard.tsx`) —
  polls `/api/prices` every 12s, and shows:
  - Cheapest current price, average price, and last-updated summary cards
  - A price-over-time trend chart (cheapest available price per provider)
  - A sortable/filterable table of every provider/region quote, with the
    current best price highlighted

## Adding a real provider

1. Create `src/lib/providers/<yourProvider>.ts` implementing `PriceProvider`
   (see the existing files for the shape) and call the real pricing API
   inside `fetchQuotes()`.
2. Register it in `src/lib/providers/index.ts`'s `providers` array.

No other changes are needed — the API route and dashboard work off the
`PriceProvider` interface.

## Development

```bash
npm install
npm run dev
```

Open http://localhost:3000.

```bash
npm run lint       # eslint
npx tsc --noEmit   # typecheck
npm run build      # production build
```
