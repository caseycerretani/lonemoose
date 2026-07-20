const express = require('express');
const cors = require('cors');
const { runWeatherUpdate, getStatus } = require('./weather');

const app = express();
const PORT = 3001;
const WEATHER_INTERVAL = 6 * 60 * 60 * 1000; // 6 hours

app.use(cors());
app.use(express.json());

const trailsRouter  = require('./routes/trails');
const reportsRouter = require('./routes/reports');

app.use('/api/trails',  trailsRouter);
app.use('/api/reports', reportsRouter);

app.get('/api/health', (_req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

app.get('/api/weather/status', (_req, res) => {
  res.json(getStatus());
});

app.post('/api/weather/refresh', async (_req, res) => {
  try {
    const result = await runWeatherUpdate();
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`Lonemoose API running on http://localhost:${PORT}`);

  // Initial weather fetch shortly after startup, then every 6 hours
  setTimeout(runWeatherUpdate, 5000);
  setInterval(runWeatherUpdate, WEATHER_INTERVAL);
});
