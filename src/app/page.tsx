import PriceDashboard from "@/components/PriceDashboard";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-6 py-10">
      <header>
        <h1 className="text-2xl font-semibold">G100 Spot Price Tracker</h1>
        <p className="mt-1 text-sm text-white/50">
          Live G100 GPU spot instance pricing across cloud and GPU-rental
          providers.
        </p>
      </header>
      <PriceDashboard />
      <footer className="mt-4 text-xs text-white/30">
        Prices shown are simulated sample data for demo purposes, not live
        market rates.
      </footer>
    </main>
  );
}
