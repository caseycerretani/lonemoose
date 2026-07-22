const express = require('express');
const cors = require('cors');
const path = require('path');
const { runWeatherUpdate, getStatus } = require('./weather');

const app = express();
const PORT = process.env.PORT || 3001;
const WEATHER_INTERVAL = 6 * 60 * 60 * 1000; // 6 hours

app.use(cors());
app.use(express.json());

// Serve built React frontend in production
const publicDir = path.join(__dirname, 'public');
app.use(express.static(publicDir));

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

// All non-API routes return the React app (client-side routing)
app.get('*', (req, res) => {
  res.sendFile(path.join(publicDir, 'index.html'));
});

app.listen(PORT, () => {
  console.log(`Lonemoose API running on http://localhost:${PORT}`);

  // Initial weather fetch shortly after startup, then every 6 hours
  setTimeout(runWeatherUpdate, 5000);
  setInterval(runWeatherUpdate, WEATHER_INTERVAL);
});
