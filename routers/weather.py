"""Weather router — API endpoint + cosmic Suisei-themed frontend."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from services import weather
from services.geolocation import get_location

router = APIRouter()


@router.get("/weather")
async def get_weather():
    """Get current weather + 3-day forecast as JSON."""
    geo = await get_location()
    data = await weather.fetch_weather(geo["lat"], geo["lon"])
    if data is None:
        return {"error": "Weather service unavailable"}
    return {
        "location": f"{geo['city']}, {geo['country']}",
        **data,
    }


@router.get("/weather/ui", response_class=HTMLResponse)
async def weather_ui():
    """Serve the cosmic weather dashboard."""
    return _WEATHER_HTML


_WEATHER_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Suisei Weather</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: 'Inter', sans-serif;
    min-height: 100vh;
    background: #0a0e1a;
    color: #e0e6f0;
    overflow-x: hidden;
  }

  /* Starfield */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background:
      radial-gradient(1px 1px at 10% 20%, rgba(150, 180, 255, 0.8), transparent),
      radial-gradient(1px 1px at 30% 60%, rgba(180, 200, 255, 0.6), transparent),
      radial-gradient(1.5px 1.5px at 50% 10%, rgba(200, 220, 255, 0.9), transparent),
      radial-gradient(1px 1px at 70% 80%, rgba(160, 190, 255, 0.5), transparent),
      radial-gradient(1px 1px at 90% 40%, rgba(140, 170, 255, 0.7), transparent),
      radial-gradient(1.5px 1.5px at 20% 90%, rgba(170, 200, 255, 0.6), transparent),
      radial-gradient(1px 1px at 60% 30%, rgba(190, 210, 255, 0.4), transparent),
      radial-gradient(1px 1px at 80% 70%, rgba(130, 160, 255, 0.8), transparent),
      radial-gradient(1px 1px at 40% 50%, rgba(200, 220, 255, 0.5), transparent),
      radial-gradient(1.5px 1.5px at 15% 45%, rgba(160, 180, 255, 0.7), transparent),
      radial-gradient(1px 1px at 85% 15%, rgba(180, 200, 255, 0.6), transparent),
      radial-gradient(1px 1px at 55% 85%, rgba(140, 170, 255, 0.4), transparent);
    z-index: 0;
    pointer-events: none;
    animation: twinkle 8s ease-in-out infinite alternate;
  }

  @keyframes twinkle {
    0% { opacity: 0.6; }
    100% { opacity: 1; }
  }

  .container {
    position: relative;
    z-index: 1;
    max-width: 480px;
    margin: 0 auto;
    padding: 24px 16px;
    min-height: 100vh;
  }

  /* Header */
  .header {
    text-align: center;
    margin-bottom: 28px;
  }

  .header h1 {
    font-size: 1.4rem;
    font-weight: 700;
    background: linear-gradient(135deg, #60a5fa, #a78bfa, #60a5fa);
    background-size: 200% auto;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    animation: shimmer 3s linear infinite;
    letter-spacing: 0.5px;
  }

  @keyframes shimmer {
    to { background-position: 200% center; }
  }

  .header .location {
    font-size: 0.8rem;
    color: #7b8db5;
    margin-top: 4px;
    font-weight: 300;
  }

  .header .updated {
    font-size: 0.7rem;
    color: #4a5a7a;
    margin-top: 2px;
  }

  /* Current card */
  .current-card {
    background: linear-gradient(135deg, rgba(30, 40, 80, 0.7), rgba(20, 25, 50, 0.8));
    border: 1px solid rgba(96, 165, 250, 0.15);
    border-radius: 20px;
    padding: 28px 24px;
    text-align: center;
    backdrop-filter: blur(10px);
    position: relative;
    overflow: hidden;
    margin-bottom: 16px;
  }

  .current-card::before {
    content: '';
    position: absolute;
    top: -50%;
    left: -50%;
    width: 200%;
    height: 200%;
    background: radial-gradient(circle, rgba(96, 165, 250, 0.05) 0%, transparent 60%);
    pointer-events: none;
  }

  .weather-icon {
    font-size: 4rem;
    margin-bottom: 8px;
    filter: drop-shadow(0 0 20px rgba(96, 165, 250, 0.3));
  }

  .temp-main {
    font-size: 3.5rem;
    font-weight: 700;
    background: linear-gradient(180deg, #fff, #a0b8e0);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    line-height: 1.1;
  }

  .temp-unit { font-size: 1.5rem; font-weight: 300; }
  .feels-like {
    font-size: 0.8rem;
    color: #7b8db5;
    margin-top: 2px;
  }

  .description {
    font-size: 1rem;
    color: #a0b8e0;
    margin-top: 8px;
    font-weight: 400;
  }

  /* Stats row */
  .stats {
    display: flex;
    justify-content: center;
    gap: 24px;
    margin-top: 20px;
  }

  .stat {
    text-align: center;
  }

  .stat-label {
    font-size: 0.65rem;
    color: #5a6a8a;
    text-transform: uppercase;
    letter-spacing: 1px;
  }

  .stat-value {
    font-size: 1rem;
    font-weight: 600;
    color: #c0d0f0;
    margin-top: 2px;
  }

  /* Forecast */
  .section-title {
    font-size: 0.75rem;
    color: #5a6a8a;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin-bottom: 10px;
    padding-left: 4px;
  }

  .forecast {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .forecast-card {
    background: rgba(25, 32, 60, 0.6);
    border: 1px solid rgba(96, 165, 250, 0.08);
    border-radius: 14px;
    padding: 14px 18px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    backdrop-filter: blur(5px);
    transition: border-color 0.3s;
  }

  .forecast-card:hover {
    border-color: rgba(96, 165, 250, 0.25);
  }

  .fc-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .fc-icon { font-size: 1.5rem; }

  .fc-day {
    font-size: 0.85rem;
    font-weight: 600;
    color: #c0d0f0;
  }

  .fc-desc {
    font-size: 0.7rem;
    color: #6a7a9a;
    margin-top: 1px;
  }

  .fc-right { text-align: right; }

  .fc-temps {
    font-size: 0.85rem;
    font-weight: 600;
    color: #c0d0f0;
  }

  .fc-temps .lo {
    color: #5a6a8a;
    font-weight: 400;
  }

  .fc-rain {
    font-size: 0.65rem;
    color: #60a5fa;
    margin-top: 2px;
  }

  /* Suisei comment */
  .suisei-comment {
    background: linear-gradient(135deg, rgba(96, 165, 250, 0.08), rgba(167, 139, 250, 0.08));
    border: 1px solid rgba(167, 139, 250, 0.15);
    border-radius: 14px;
    padding: 14px 18px;
    margin-top: 16px;
    font-size: 0.8rem;
    color: #b0c0e0;
    font-style: italic;
    line-height: 1.5;
  }

  .suisei-comment::before {
    content: '~ ';
    color: #a78bfa;
  }

  /* Loading */
  .loading {
    text-align: center;
    padding: 80px 0;
    color: #5a6a8a;
  }

  .loading .spinner {
    width: 32px;
    height: 32px;
    border: 2px solid rgba(96, 165, 250, 0.2);
    border-top-color: #60a5fa;
    border-radius: 50%;
    animation: spin 1s linear infinite;
    margin: 0 auto 12px;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  .error-msg {
    text-align: center;
    color: #f87171;
    padding: 40px;
    font-size: 0.85rem;
  }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Suisei Weather</h1>
    <div class="location" id="location">Loading...</div>
    <div class="updated" id="updated"></div>
  </div>
  <div id="content">
    <div class="loading">
      <div class="spinner"></div>
      Fetching weather data...
    </div>
  </div>
</div>

<script>
const ICONS = {
  clear: '\\u2728', cloudy: '\\u2601\\ufe0f', rainy: '\\ud83c\\udf27\\ufe0f',
  drizzle: '\\ud83c\\udf26\\ufe0f', stormy: '\\u26c8\\ufe0f',
  snowy: '\\u2744\\ufe0f', foggy: '\\ud83c\\udf2b\\ufe0f',
};

const COMMENTS = {
  clear: [
    "Clear skies~ Perfect weather to go outside... or stay in and watch me stream.",
    "Not a cloud in sight! Sui-chan approves of this weather.",
  ],
  cloudy: [
    "Cloudy, huh... Looks like the sky is being indecisive today.",
    "Overcast vibes. Good gaming weather if you ask me~",
  ],
  rainy: [
    "Rain again... Don't forget your umbrella, okay?",
    "It's raining~ Stay inside and keep me company instead.",
    "Rainy day... Perfect excuse to be lazy, right?",
  ],
  drizzle: [
    "Light drizzle... Not enough to cancel plans, but enough to be annoying.",
    "A little drizzle never hurt anyone~ But bring an umbrella just in case.",
  ],
  stormy: [
    "Thunderstorm?! Stay safe inside! ...And maybe talk to me while you wait it out.",
    "Storm warning! This is NOT the time to go outside. Stay here with me.",
  ],
  foggy: [
    "Foggy... How mysterious. Can you even see anything out there?",
    "The fog makes everything look like a horror game scene...",
  ],
  snowy: [
    "Snow! ...Wait, it doesn't snow here. Something's weird.",
    "Snowy weather~ Wrap up warm!",
  ],
};

function getDayName(dateStr) {
  const d = new Date(dateStr + 'T00:00:00');
  const today = new Date();
  today.setHours(0,0,0,0);
  const diff = (d - today) / 86400000;
  if (diff <= 0) return 'Today';
  if (diff <= 1) return 'Tomorrow';
  return d.toLocaleDateString('en', { weekday: 'short' });
}

function pickComment(condition) {
  const list = COMMENTS[condition] || COMMENTS.clear;
  return list[Math.floor(Math.random() * list.length)];
}

async function load() {
  try {
    const resp = await fetch('/weather');
    if (!resp.ok) throw new Error('API error');
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    render(data);
  } catch (e) {
    document.getElementById('content').innerHTML =
      '<div class="error-msg">Could not load weather data.<br>Try refreshing the page.</div>';
  }
}

function render(data) {
  const c = data.current;
  const icon = ICONS[c.condition] || ICONS.clear;

  document.getElementById('location').textContent = data.location;
  document.getElementById('updated').textContent = 'Updated just now';

  let forecastHTML = '';
  for (const f of data.forecast) {
    const fi = ICONS[f.condition] || ICONS.clear;
    forecastHTML += `
      <div class="forecast-card">
        <div class="fc-left">
          <span class="fc-icon">${fi}</span>
          <div>
            <div class="fc-day">${getDayName(f.date)}</div>
            <div class="fc-desc">${f.description}</div>
          </div>
        </div>
        <div class="fc-right">
          <div class="fc-temps">${Math.round(f.temp_max)}° <span class="lo">${Math.round(f.temp_min)}°</span></div>
          <div class="fc-rain">${f.rain_chance}% rain</div>
        </div>
      </div>`;
  }

  document.getElementById('content').innerHTML = `
    <div class="current-card">
      <div class="weather-icon">${icon}</div>
      <div class="temp-main">${Math.round(c.temperature)}<span class="temp-unit">°C</span></div>
      <div class="feels-like">Feels like ${Math.round(c.feels_like)}°C</div>
      <div class="description">${c.description}</div>
      <div class="stats">
        <div class="stat">
          <div class="stat-label">Humidity</div>
          <div class="stat-value">${c.humidity}%</div>
        </div>
        <div class="stat">
          <div class="stat-label">Wind</div>
          <div class="stat-value">${c.wind_speed} km/h</div>
        </div>
      </div>
    </div>
    <div class="section-title">3-Day Forecast</div>
    <div class="forecast">${forecastHTML}</div>
    <div class="suisei-comment">${pickComment(c.condition)}</div>
  `;
}

load();
setInterval(load, 1800000); // refresh every 30 min
</script>
</body>
</html>
"""
