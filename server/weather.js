const db = require('./db');

// GPS coordinates for each region
const REGIONS = [
  { name: 'Taos',        lat: 36.4072, lon: -105.5731 },
  { name: 'Angel Fire',  lat: 36.3856, lon: -105.2686 },
];

let lastUpdate = null;
let lastResults = null;

// NM trails dry out fast — monsoon rains are intense but short-lived.
// Thresholds tuned for high-desert conditions.
function inferCondition(rain24h, rain48h, snow48h) {
  if (snow48h >= 10) return 'Snowy';
  if (snow48h > 0)   return 'Snowy';
  if (rain24h > 15)  return 'Muddy';
  if (rain24h > 4)   return 'Tacky';
  if (rain48h > 10)  return 'Tacky';   // drying out from yesterday's rain
  return 'Dry';
}

function buildComment(rain24h, rain48h, snow48h, condition) {
  const parts = [];
  if (snow48h > 0)  parts.push(`${snow48h.toFixed(1)} mm snow (48h)`);
  if (rain24h > 0)  parts.push(`${rain24h.toFixed(1)} mm rain (24h)`);
  else              parts.push('no rain in 24h');
  if (rain48h > rain24h) parts.push(`${rain48h.toFixed(1)} mm rain (48h)`);
  return `Auto-updated from Open-Meteo weather data — ${parts.join(', ')}. Inferred condition: ${condition}.`;
}

async function fetchRegionWeather(region) {
  const url =
    `https://api.open-meteo.com/v1/forecast` +
    `?latitude=${region.lat}&longitude=${region.lon}` +
    `&daily=rain_sum,snowfall_sum` +
    `&past_days=7&forecast_days=0` +
    `&timezone=America%2FDenver`;

  const res = await fetch(url, {
    headers: { 'User-Agent': 'Lonemoose Trail Tracker/1.0' },
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) throw new Error(`Open-Meteo HTTP ${res.status}`);
  return res.json();
}

const delWeather = db.prepare("DELETE FROM reports WHERE trail_id = ? AND source = 'weather'");
const insWeather = db.prepare(`
  INSERT INTO reports (trail_id, condition, rating, comment, source, created_at)
  VALUES (?, ?, NULL, ?, 'weather', strftime('%Y-%m-%dT%H:%M:%fZ','now'))
`);

async function runWeatherUpdate() {
  console.log('[weather] Fetching Open-Meteo data…');
  const results = [];

  for (const region of REGIONS) {
    try {
      const data = await fetchRegionWeather(region);
      const { rain_sum: rain, snowfall_sum: snow } = data.daily;
      const n = rain.length;

      const rain24h = rain[n - 1] ?? 0;
      const rain48h = rain24h + (rain[n - 2] ?? 0);
      const snow48h = (snow[n - 1] ?? 0) + (snow[n - 2] ?? 0);
      const condition = inferCondition(rain24h, rain48h, snow48h);
      const comment   = buildComment(rain24h, rain48h, snow48h, condition);

      const trails = db.prepare('SELECT id FROM trails WHERE region = ?').all(region.name);
      const upsert = db.transaction(() => {
        for (const t of trails) {
          delWeather.run(t.id);
          insWeather.run(t.id, condition, comment);
        }
      });
      upsert();

      const entry = { region: region.name, condition, rain24h, rain48h, snow48h };
      results.push(entry);
      console.log(`[weather] ${region.name} → ${condition} (rain24h=${rain24h}mm, snow48h=${snow48h}mm)`);
    } catch (err) {
      console.error(`[weather] ${region.name} failed:`, err.message);
      results.push({ region: region.name, error: err.message });
    }
  }

  lastUpdate = new Date().toISOString();
  lastResults = results;
  return { lastUpdate, results };
}

function getStatus() {
  return { lastUpdate, results: lastResults };
}

module.exports = { runWeatherUpdate, getStatus };
