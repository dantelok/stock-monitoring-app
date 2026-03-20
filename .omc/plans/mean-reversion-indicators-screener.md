# Plan: Mean-Reversion Swing Trading Indicators & Screener (v2 — Revised)

## Source
Deep Interview Spec: `.omc/specs/deep-interview-stock-indicators.md` (14% ambiguity)

## RALPLAN-DR Summary

### Principles
1. **Incremental extension** — add to existing patterns, don't rewrite what works
2. **Backend-first** — indicators are pure math functions; get calculations right before touching UI
3. **Minimal API surface** — two new endpoints (`/api/screener/scan`, `/api/screener/results`), extend existing `/api/stock/<symbol>`
4. **Graceful degradation** — yfinance failures for individual stocks shouldn't crash the screener
5. **Separation of concerns within the monolith** — indicator calculations, scoring logic, and stock lists are distinct function groups with clear data flow, even though they live in one file

### Decision Drivers
1. **yfinance rate limiting** — scanning 20-30 stocks triggers many API calls; must use ThreadPoolExecutor + delays
2. **Single-file simplicity** — current app is one `app.py` + one `index.html`; adding too many files breaks the simplicity
3. **ApexCharts sub-chart pattern** — existing volume/RS charts establish the pattern for RSI/MACD/ATR sub-charts
4. **Algorithm correctness** — RSI and ATR must use Wilder's smoothing to match industry-standard charting platforms

### Viable Options

#### Option A: Pure Monolith Extension
Add all new functions to `app.py`, synchronous screener endpoint.
- **Pros:** Consistent with existing patterns, simplest to implement
- **Cons:** Screener blocks Flask for 25-30s, no caching, every filter change re-scans
- **Invalidation rationale:** The 25-30s blocking window makes the app appear broken during scans. Unacceptable UX even for personal use.

#### Option B: Modular Split
Extract indicators into `indicators.py`, screener into `screener.py`, stock lists into `config.py`.
- **Pros:** Better separation, easier to test individually
- **Cons:** Breaks existing simplicity, overkill for this project scale
- **Invalidation rationale:** Splitting adds friction without proportional benefit at this scale.

#### Option C: Monolith + Scan Cache + Threaded Fetch (Chosen)
Keep single-file architecture but add ThreadPoolExecutor for parallel yfinance fetches and an in-memory scan cache so filter changes don't re-scan.
- **Pros:** Preserves single-file simplicity, scan time ~5-7s (vs 25-30s), instant filter adjustments from cache
- **Cons:** Module-level cache introduces state; cache needs TTL or manual refresh
- **Risk:** Cache staleness — mitigated by 5-minute TTL and manual "Rescan" button

## Requirements Summary

### Part 1: New Technical Indicators (extend `/api/stock/<symbol>`)
- RSI (14-period) — Wilder's smoothing, oscillator 0-100, sub-chart
- MACD (12/26/9) — MACD line, signal line, histogram, sub-chart
- VWAP — overlay on main price chart (intraday periods only: 1d, 5d), daily-resetting for 5d
- ATR (14-period) — Wilder's smoothing, volatility measure, sub-chart
- Bollinger %B — derived from existing Bollinger Bands, used in screener scoring

### Part 2: Stock Screener (new endpoints + new UI tab)
- Sector selection: Tech (~25 stocks), Energy (~25 stocks)
- Fundamental filters: EPS > X, P/E < X, Revenue Growth > X%
- Technical filters: RSI < X, Bollinger %B < X (where %B represents position within bands, 0=lower, 1=upper)
- Composite mean-reversion score (70% technical / 30% fundamental)
- Sortable ranked table with click-through to chart view
- All thresholds adjustable via UI controls — filter changes read from cache (instant)

## Acceptance Criteria
- [ ] `calculate_rsi(close_prices, period=14)` uses Wilder's EMA (`ewm(alpha=1/period)`) and returns Series of RSI values (0-100) matching TradingView within ±1 point
- [ ] `calculate_macd(close_prices, fast=12, slow=26, signal=9)` returns (macd_line, signal_line, histogram)
- [ ] `calculate_vwap(high, low, close, volume, index)` returns Series of VWAP values, resetting daily for multi-day periods
- [ ] `calculate_atr(high, low, close, period=14)` uses Wilder's EMA and returns Series of ATR values
- [ ] `calculate_bollinger_pctb(close, period=20, num_std=2)` returns Series of %B values (0-1 scale)
- [ ] `/api/stock/<symbol>` response includes `rsi`, `macd`, `macdSignal`, `macdHistogram`, `vwap`, `atr` fields
- [ ] VWAP only calculated/returned for intraday periods (1d, 5d)
- [ ] RSI sub-chart rendered below main chart with 30/70 horizontal reference lines
- [ ] MACD sub-chart rendered with histogram bars (green/red) + MACD/signal lines
- [ ] ATR sub-chart rendered below MACD
- [ ] VWAP line overlaid on main price chart (intraday only)
- [ ] Indicator toggle buttons work for new indicators — sub-charts use CSS show/hide (not full re-render)
- [ ] `/api/screener/scan?sector=tech` fetches all stocks in parallel via ThreadPoolExecutor, caches results, returns full dataset
- [ ] `/api/screener/results?sector=tech&eps_min=0&pe_max=30&rev_growth_min=5&rsi_max=40&bb_pctb_max=0.2` filters cached data and returns ranked JSON
- [ ] Screener scan completes in <10 seconds (parallelized with 5 workers)
- [ ] Screener handles individual stock fetch failures gracefully (skip, log, don't crash)
- [ ] When all stocks fail, return `{"results": [], "errors": ["Failed to fetch data for all stocks"], "scanned": 0}`
- [ ] Screener tab visible in main app navigation
- [ ] Sector toggle (Tech / Energy) in screener UI
- [ ] Adjustable filter inputs with defaults: EPS>0, P/E<30, RevGrowth>5%, RSI<40, BB %B<0.20
- [ ] Results table columns: Rank, Ticker, Name, Price, RSI, EPS, P/E, Rev Growth, Score
- [ ] Table sortable by clicking column headers (client-side sort)
- [ ] Clicking a row calls `loadStock(symbol)` + `switchTab('chart')`
- [ ] Mean-reversion score calculated with concrete formulas (see Step 4)

## Implementation Steps

### Step 1: Backend — Indicator Functions (`app.py` after `calculate_relative_strength`)
Add five new calculation functions:

```python
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

def calculate_rsi(data, period=14):
    """RSI using Wilder's smoothing (EMA with alpha=1/period)"""
    delta = data.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_macd(data, fast=12, slow=26, signal=9):
    """MACD with standard EMA periods"""
    ema_fast = data.ewm(span=fast, adjust=False).mean()
    ema_slow = data.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calculate_vwap(high, low, close, volume, index):
    """VWAP with daily reset for multi-day periods"""
    typical_price = (high + low + close) / 3
    tp_vol = typical_price * volume
    # Group by trading day to reset VWAP daily
    dates = index.date
    df = pd.DataFrame({'tp_vol': tp_vol.values, 'volume': volume.values}, index=index)
    df['date'] = dates
    df['cum_tp_vol'] = df.groupby('date')['tp_vol'].cumsum()
    df['cum_vol'] = df.groupby('date')['volume'].cumsum()
    vwap = df['cum_tp_vol'] / df['cum_vol']
    return pd.Series(vwap.values, index=index)

def calculate_atr(high, low, close, period=14):
    """ATR using Wilder's smoothing"""
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

def calculate_bollinger_pctb(close, period=20, num_std=2):
    """Bollinger %B: (price - lower) / (upper - lower). 0=at lower band, 1=at upper band"""
    sma = close.rolling(window=period, min_periods=1).mean()
    std = close.rolling(window=period, min_periods=1).std()
    upper = sma + (std * num_std)
    lower = sma - (std * num_std)
    pctb = (close - lower) / (upper - lower)
    return pctb
```

### Step 2: Backend — Extend `/api/stock/<symbol>` Response (`app.py` lines 89-221)
After existing indicator calculations (line 98), add:
- Call `calculate_rsi(close_prices)` → serialize to `rsi` array
- Call `calculate_macd(close_prices)` → serialize to `macd`, `macdSignal`, `macdHistogram` arrays
- Call `calculate_vwap(hist['High'], hist['Low'], close_prices, hist['Volume'], hist.index)` → serialize to `vwap` array (only for `1d`, `5d` periods)
- Call `calculate_atr(hist['High'], hist['Low'], close_prices)` → serialize to `atr` array
- Serialize each to `[{'x': timestamp_ms, 'y': rounded_value_or_null}, ...]` following existing pattern at lines 154-171
- Add all new fields to the JSON response dict (lines 200-222)
- For MACD histogram, include sign-based color: `{'x': ts, 'y': val, 'fillColor': '#00ff88' if val >= 0 else '#ff4757'}`

### Step 3: Backend — Curated Stock Lists + Cache (`app.py` after PERIOD_CONFIG)
```python
SECTOR_STOCKS = {
    'tech': ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO', 'ORCL', 'CRM',
             'AMD', 'ADBE', 'INTC', 'CSCO', 'QCOM', 'TXN', 'NOW', 'IBM', 'AMAT', 'MU',
             'LRCX', 'PANW', 'SNPS', 'CDNS', 'KLAC'],
    'energy': ['XOM', 'CVX', 'COP', 'SLB', 'EOG', 'MPC', 'PSX', 'VLO', 'OXY', 'WMB',
               'KMI', 'HES', 'DVN', 'HAL', 'BKR', 'FANG', 'TRGP', 'CTRA', 'EQT', 'OKE',
               'MRO', 'APA', 'MTDR', 'PR', 'MGY']
}

# In-memory scan cache: {sector: {'data': [...], 'timestamp': float}}
_screener_cache = {}
CACHE_TTL = 300  # 5 minutes
```

### Step 4: Backend — Scoring Functions (`app.py`)
Add `calculate_mean_reversion_score(rsi, pctb, macd_hist, eps, pe, rev_growth)`:

**Technical score (0-100, weight 70%) — average of three components:**
- **RSI component:** `min(100, max(0, (40 - rsi) / 40 * 100))`
  - RSI=0→100, RSI=20→50, RSI=30→25, RSI≥40→0
- **Bollinger %B component:** `min(100, max(0, (0.5 - pctb) / 0.5 * 100))`
  - %B=0 (at lower band)→100, %B=0.25→50, %B≥0.5→0
- **MACD histogram component:**
  - If `macd_hist[-1] > 0 and macd_hist[-2] <= 0`: 100 (fresh bullish crossover)
  - If `macd_hist[-1] > 0`: 50 (positive but not fresh crossover)
  - If `macd_hist[-1] > macd_hist[-2]`: 25 (negative but improving)
  - Else: 0 (negative and worsening)
- **Technical score** = `(rsi_score + bb_score + macd_score) / 3`

**Fundamental score (0-100, weight 30%) — average of three components:**
- **EPS component:** `min(100, max(0, eps / 10 * 100))`
  - EPS=$0→0, EPS=$5→50, EPS≥$10→100
  - Negative EPS→0
- **P/E component:** `min(100, max(0, (30 - pe) / 30 * 100))`
  - P/E=0→100, P/E=15→50, P/E≥30→0
  - Negative P/E→0 (not meaningful)
- **Revenue growth component:** `min(100, max(0, rev_growth / 20 * 100))`
  - Growth=0%→0, Growth=10%→50, Growth≥20%→100
  - Negative growth→0
  - Field lookup: try `info.get('revenueGrowth')` (annualized, 0-1 scale → multiply by 100), fallback to `info.get('revenueQuarterlyGrowth')`, fallback to `None` (skip component, average remaining)
- **Fundamental score** = `(eps_score + pe_score + rev_score) / count_of_available_components`

**Composite:** `0.7 * technical_score + 0.3 * fundamental_score`

If fundamental data is completely unavailable for a stock, use `composite = technical_score` (100% technical).

### Step 5: Backend — Screener Endpoints (`app.py`)

**Two endpoints (scan-then-filter architecture):**

#### `/api/screener/scan?sector=tech`
- Validate `sector` is in `SECTOR_STOCKS`
- Check cache: if `_screener_cache[sector]` exists and `time.time() - timestamp < CACHE_TTL`, return cached data
- Otherwise, fetch all stocks in parallel using `ThreadPoolExecutor(max_workers=5)`:

```python
def fetch_stock_data_for_screener(symbol):
    """Fetch fundamentals + 3mo technicals for one stock"""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        hist = yf.download(symbol, period='3mo', interval='1d', progress=False, auto_adjust=True)
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        if hist.empty:
            return None
        close = hist['Close']
        rsi_val = float(calculate_rsi(close).iloc[-1])
        pctb_val = float(calculate_bollinger_pctb(close).iloc[-1])
        macd_l, macd_s, macd_h = calculate_macd(close)
        macd_hist_vals = macd_h.iloc[-2:].tolist()  # last 2 values for crossover detection
        return {
            'symbol': symbol,
            'name': info.get('longName') or info.get('shortName') or symbol,
            'price': round(float(close.iloc[-1]), 2),
            'rsi': round(rsi_val, 2) if not pd.isna(rsi_val) else None,
            'pctb': round(pctb_val, 4) if not pd.isna(pctb_val) else None,
            'macd_hist': [round(v, 4) if not pd.isna(v) else 0 for v in macd_hist_vals],
            'eps': info.get('trailingEps'),
            'pe': info.get('trailingPE'),
            'rev_growth': info.get('revenueGrowth') or info.get('revenueQuarterlyGrowth'),
        }
    except Exception:
        return None
```

- Submit all stocks to executor, collect results with `as_completed()`
- Add 0.5s delay between submitting batches of 5 to respect rate limits
- Store results + timestamp in `_screener_cache[sector]`
- Return: `{"data": [...], "scanned": N, "failed": M, "cached": false}`

#### `/api/screener/results?sector=tech&eps_min=0&pe_max=30&rev_growth_min=5&rsi_max=40&bb_pctb_max=0.2`
- Read from `_screener_cache[sector]` (return error if no scan data)
- Parse all filter params with defaults: `eps_min=0, pe_max=30, rev_growth_min=5, rsi_max=40, bb_pctb_max=0.2`
- `bb_pctb_max` meaning: Bollinger %B must be below this value (0.2 = price in bottom 20% of band range)
- Apply fundamental filters: `eps >= eps_min`, `pe <= pe_max` (skip if None), `rev_growth * 100 >= rev_growth_min` (skip if None)
- Apply technical filters: `rsi <= rsi_max` (skip if None), `pctb <= bb_pctb_max` (skip if None)
- Calculate mean-reversion score for each passing stock
- Sort by score descending
- Return: `{"results": [{symbol, name, price, rsi, pctb, eps, pe, revGrowth, score}, ...], "total_scanned": N, "total_passed": M}`
- If zero results: `{"results": [], "total_scanned": N, "total_passed": 0}`

### Step 6: Frontend — Tab Navigation (`index.html`)
Add tab system in the header area (after search section, before time display):
- Two tabs: "Chart" (default active) | "Screener"
- CSS: styled like existing `.period-selector` pattern (`.tab-selector` with `.tab-btn` and `.tab-btn.active`)
- JS: `switchTab(tab)` toggles visibility:
  - `chart`: show `.main-content`, `.stock-info-card`, `.period-selector`, `.chart-container`, `.sub-charts`; hide `#screener-content`
  - `screener`: hide chart elements; show `#screener-content`
- New `<div id="screener-content" style="display:none">` section after `.main-content`

### Step 7: Frontend — New Sub-Charts (`index.html`)
Extend the `.sub-charts` section (after existing volume/RS containers):
- Add `#rsi-container` with RSI sub-chart:
  - ApexCharts line chart, same dark theme
  - Horizontal annotations at y=30 (green dashed) and y=70 (red dashed)
  - Y-axis range fixed 0-100
- Add `#macd-container` with MACD sub-chart:
  - Mixed chart: histogram bars (green/red by sign) + two line series (MACD line cyan, signal line orange)
- Add `#atr-container` with ATR sub-chart:
  - Simple line chart, purple color
- Add VWAP to main chart as additional series:
  - Conditionally added when period is '1d' or '5d'
  - Dashed orange line overlay
- New functions: `renderRSIChart(data)`, `renderMACDChart(data)`, `renderATRChart(data)`
- Follow existing `renderVolumeChart()`/`renderRSChart()` destroy-then-create pattern
- **Sub-chart toggle optimization**: For RSI, MACD, ATR, VWAP toggles in `toggleIndicator()`:
  - Use CSS show/hide on the container (`.hidden` class already exists at line 816)
  - Do NOT call `renderChart(stockData)` for sub-chart toggles
  - Only call `renderChart(stockData)` for main-chart overlays (SMA, Bollinger, VWAP)
- Add toggle buttons for RSI, MACD, VWAP, ATR to the indicator toggle area
- Update `indicators` object with: `rsi: true, macd: true, vwap: true, atr: true`
- Update `indicatorColors` with: `rsi: '#f59e0b', macd: '#00d4ff', vwap: '#ff9f43', atr: '#a855f7'`

### Step 8: Frontend — Screener UI (`index.html`)
Build `<div id="screener-content">`:
- **Sector selector:** two styled toggle buttons (Tech / Energy) using `.period-btn` pattern
  - Default: Tech selected
  - JS: `currentSector = 'tech'`
- **Filter controls:** horizontal row of labeled number inputs, styled with dark theme
  - EPS min (default 0, step 0.5)
  - P/E max (default 30, step 1)
  - Rev Growth min % (default 5, step 1)
  - RSI max (default 40, step 5)
  - BB %B max (default 0.20, step 0.05)
- **Action buttons:**
  - "Scan Sector" → calls `runScreenerScan()` which POSTs to `/api/screener/scan?sector=X`
  - "Apply Filters" → calls `applyScreenerFilters()` which GETs `/api/screener/results?...` (instant, reads cache)
  - "Rescan" → forces new scan (ignores cache by adding `&force=1`)
- **Loading state:** overlay spinner with text "Scanning {sector} stocks... (X/25)" during scan
- **Results table:** `<table id="screener-table">` with sortable headers
  - Columns: #, Ticker, Name, Price, RSI, BB %B, EPS, P/E, Rev Growth %, Score
  - Score column has color gradient: green (high) → yellow (medium) → red (low)
  - Clickable rows: `onclick="loadStock('${symbol}'); switchTab('chart');"`
- JS functions:
  - `runScreenerScan()` — fetch `/api/screener/scan?sector=X`, on success call `applyScreenerFilters()`
  - `applyScreenerFilters()` — fetch `/api/screener/results?...` with current filter values, render table
  - `sortScreenerResults(column)` — client-side sort: toggle asc/desc, re-render table rows
  - `renderScreenerTable(results)` — build table HTML from results array

### Step 9: Integration & Polish
- Ensure tab switching preserves chart state (CSS display toggle, don't destroy charts)
- Add screener error states: "No scan data — click Scan first", "No stocks match filters"
- Handle edge case: user clicks screener row before any chart has been rendered
- Test with real yfinance data for both sectors
- Verify all indicator toggles work (sub-charts via CSS, overlays via re-render)
- Verify screener click-through loads correct stock and switches to chart tab
- Add `import time` and `from concurrent.futures import ThreadPoolExecutor, as_completed` to top of `app.py`

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| yfinance rate limiting during screener scan | Scan fails or returns partial results | ThreadPoolExecutor (5 workers) + 0.5s inter-batch delay + per-stock try/except + partial results returned |
| yfinance `info` dict missing keys | Missing fundamental data | `.get()` with `None` defaults; scoring skips missing components and averages available ones |
| yfinance `revenueGrowth` field inconsistency | Missing revenue growth for some stocks | Fallback chain: `revenueGrowth` → `revenueQuarterlyGrowth` → `None` (skip in scoring) |
| VWAP meaningless for non-intraday data | Confusing indicator values | Only calculate/display VWAP for 1d and 5d periods |
| Large HTML file becomes unwieldy | Hard to maintain | Accept for now; refactor to JS modules later if needed |
| Screener scan takes 5-10s with threading | Moderate wait | Loading overlay with progress text; results cached for instant filter adjustments |
| Cache staleness (data up to 5 min old) | Slightly outdated scan results | 5-min TTL auto-expires; "Rescan" button for manual refresh |
| ApexCharts performance with 5+ sub-charts | Slow rendering | Sub-charts rendered once, toggled via CSS show/hide; only main chart overlays trigger re-render |
| All stocks in a sector fail to fetch | Empty screener results | Return structured error response; UI shows "No data available — try rescanning" |

## Verification Steps
1. Start Flask app: `python app.py`
2. Search for "AAPL" — verify RSI, MACD, ATR sub-charts appear below main chart
3. Verify RSI values roughly match TradingView for AAPL (within ±1-2 points at recent close)
4. Switch to 1d period — verify VWAP overlay appears on main chart
5. Switch to 5d period — verify VWAP resets each trading day (not one flat cumulative line)
6. Switch to 1y period — verify VWAP is NOT shown
7. Toggle RSI off — verify RSI container hides without main chart re-rendering
8. Toggle SMA off — verify main chart re-renders (overlay removal)
9. Switch to Screener tab — verify filter controls and sector selector appear
10. Click "Scan Sector" (Tech) — verify loading state appears, results populate in <10s
11. Adjust RSI max slider to 20 — click "Apply Filters" — verify table updates instantly (no re-scan)
12. Click a result row — verify chart tab activates with that stock loaded
13. Sort table by Score column — verify descending sort
14. Select "Energy" sector, scan again — verify different results
15. Wait 5+ minutes, scan again — verify fresh data is fetched (cache expired)

## ADR (Architecture Decision Record)

### Decision
Extend the monolith (`app.py` + `index.html`) with new indicator functions, a two-endpoint screener API (scan + filter), threaded fetch, and in-memory scan caching.

### Drivers
- User wants a complete mean-reversion toolkit in their existing app
- yfinance rate limiting constrains screener design — threading + caching are essential
- Existing single-file architecture should be preserved
- RSI/ATR must use Wilder's smoothing for industry-standard accuracy

### Alternatives Considered
- **Pure synchronous monolith** (Option A) — rejected: 25-30s blocking, unusable UX
- **Modular split** (Option B) — rejected: premature complexity for this project size
- **Database caching** — rejected: adds external dependency; in-memory dict with TTL is sufficient for curated sector lists of 25 stocks

### Why Chosen
Option C (monolith + cache + threads) is the minimal upgrade that solves the blocking problem. ThreadPoolExecutor adds ~10 lines. In-memory cache adds ~15 lines. The scan/filter split eliminates re-scanning on filter changes. Total added complexity is modest and justified by the UX improvement.

### Consequences
- `app.py` grows to ~550-650 lines (manageable)
- `index.html` grows significantly (one large file)
- Module-level `_screener_cache` dict introduces server state (resets on restart — acceptable for personal tool)
- Scan takes 5-10s instead of 25-30s
- Filter adjustments are instant (read from cache)

### Follow-ups
- Add persistent caching (SQLite or JSON file) if server restarts are frequent
- Consider SSE for real-time scan progress updates
- Extract to modules if file size exceeds ~800 lines

## Changelog (v2 Revision)
- **Fixed RSI**: Changed from SMA rolling mean to Wilder's EMA (`ewm(alpha=1/period, min_periods=period)`)
- **Fixed ATR**: Changed from SMA to Wilder's EMA (same fix as RSI)
- **Fixed VWAP**: Added daily reset via `groupby(index.date)` for multi-day periods
- **Added Bollinger %B**: New `calculate_bollinger_pctb()` function (was referenced but never defined)
- **Added ThreadPoolExecutor**: Screener uses 5 parallel workers, reducing scan time from ~27s to ~5-7s
- **Split screener into scan + filter**: `/api/screener/scan` (fetches + caches) and `/api/screener/results` (filters cached data)
- **Concrete scoring formulas**: All 6 sub-components now have explicit formulas with ranges
- **Defined `bb_pctb_max`**: Bollinger %B < 0.2 means price in bottom 20% of band range
- **Revenue growth field fallback**: `revenueGrowth` → `revenueQuarterlyGrowth` → None
- **Graceful empty results**: Specified response format when all stocks fail
- **Sub-chart toggle optimization**: CSS show/hide instead of full re-render for sub-chart toggles
- **Fixed Principle 5**: Changed from "separate modules" to "separation of concerns within the monolith"
- **Added cache TTL + Rescan button**: 5-minute TTL with manual refresh option
