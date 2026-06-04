const express = require('express');
const cors = require('cors');

const app = express();
const PORT = 3001;

app.use(cors());
app.use(express.json());

const trailsRouter = require('./routes/trails');
const reportsRouter = require('./routes/reports');

app.use('/api/trails', trailsRouter);
app.use('/api/reports', reportsRouter);

app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

app.listen(PORT, () => {
  console.log(`Lonemoose API running on http://localhost:${PORT}`);
});
