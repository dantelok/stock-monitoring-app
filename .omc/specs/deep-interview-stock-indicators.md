# Deep Interview Spec: Mean-Reversion Swing Trading Indicators & Screener

## Metadata
- Interview ID: stock-indicators-strategy
- Rounds: 10
- Final Ambiguity Score: 14%
- Type: brownfield
- Generated: 2026-03-20
- Threshold: 20%
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.95 | 0.35 | 0.33 |
| Constraint Clarity | 0.82 | 0.25 | 0.21 |
| Success Criteria | 0.80 | 0.25 | 0.20 |
| Context Clarity | 0.80 | 0.15 | 0.12 |
| **Total Clarity** | | | **0.86** |
| **Ambiguity** | | | **0.14** |

## Goal
Build a mean-reversion swing trading toolkit: add RSI, MACD, VWAP, and ATR indicators to the existing single-stock chart view, and build an automated sector-based stock screener that filters by fundamentals (EPS, P/E, Revenue Growth) and technicals (RSI, Bollinger Band position), ranks results with a composite mean-reversion score (70% technical / 30% fundamental weighting), and displays them in a sortable table with click-through to the full chart.

## New Technical Indicators

### 1. RSI (Relative Strength Index) — 14-period
- Oscillator (0-100). Key levels: oversold < 30, overbought > 70
- Primary signal for mean-reversion: buy when RSI < 30 in a stock with strong fundamentals
- Display: separate sub-chart below main price chart

### 2. MACD (Moving Average Convergence Divergence) — 12/26/9
- MACD line (12 EMA - 26 EMA), Signal line (9 EMA of MACD), Histogram (MACD - Signal)
- Confirms momentum direction and potential reversals
- Display: separate sub-chart with histogram bars + two lines

### 3. VWAP (Volume Weighted Average Price)
- Intraday anchor price. Price below VWAP = potential mean-reversion buy zone
- Most relevant for 1d and 5d periods (intraday data)
- Display: overlay on main price chart

### 4. ATR (Average True Range) — 14-period
- Measures volatility. Used for stop-loss sizing (e.g., stop = entry - 2×ATR)
- Display: separate sub-chart or as a numeric value in stock info panel

## Screener Feature

### Pipeline
1. **Select sector**: Tech or Energy (curated list of ~20-30 top stocks per sector)
2. **Fundamental filters** (user-adjustable via UI):
   - EPS > threshold (default: > 0)
   - P/E ratio < threshold (default: < 30)
   - Revenue Growth > threshold (default: > 5%)
3. **Technical filters** (user-adjustable via UI):
   - RSI < threshold (default: < 40 for mean-reversion candidates)
   - Price near lower Bollinger Band (configurable proximity %)
4. **Rank** by composite Mean-Reversion Score (0-100):
   - 70% technical weight: RSI proximity to oversold, Bollinger %B, MACD histogram direction
   - 30% fundamental weight: EPS strength, P/E attractiveness, revenue growth rate
5. **Display** as sortable ranked table: ticker, company name, price, RSI, EPS, P/E, revenue growth, mean-reversion score
6. **Click row** → navigate to full chart view for that stock

### UI Placement
- New "Screener" tab within the existing single-page app (alongside the current chart view)
- Filter controls: sliders/inputs for each threshold at the top of the screener tab
- Sector selector: dropdown or toggle buttons for Tech / Energy

### Stock Universe (Curated Lists)
- **Tech**: ~20-30 major tech stocks (AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, etc.)
- **Energy**: ~20-30 major energy stocks (XOM, CVX, COP, SLB, EOG, MPC, etc.)

## Constraints
- yfinance is the only data source (no paid APIs)
- yfinance rate limiting: batch requests carefully, consider caching
- Screener scans curated sector lists (~20-30 stocks), not entire exchanges
- All filter thresholds adjustable via UI (sliders/inputs with sensible defaults)
- Mean-reversion score weighted 70% technical / 30% fundamental
- Sectors limited to Tech and Energy for now (extensible later)

## Non-Goals
- No real-time streaming / WebSocket price feeds
- No user authentication or saved screener configurations (for now)
- No backtesting engine (future enhancement)
- No alerts/notifications system
- No portfolio tracking or P&L
- No dynamic sector fetching from external APIs — use hardcoded curated lists

## Acceptance Criteria
- [ ] RSI (14) calculated and displayed as a sub-chart below the main price chart
- [ ] MACD (12/26/9) with histogram displayed as a sub-chart
- [ ] VWAP calculated and overlaid on the main price chart (for intraday periods)
- [ ] ATR (14) displayed as a sub-chart or info panel value
- [ ] Screener tab accessible from the main app navigation
- [ ] Sector selector (Tech / Energy) in the screener UI
- [ ] Adjustable fundamental filter inputs: EPS, P/E, Revenue Growth with defaults
- [ ] Adjustable technical filter inputs: RSI threshold, Bollinger proximity with defaults
- [ ] Screener results displayed as a sortable table with columns: ticker, name, price, RSI, EPS, P/E, revenue growth, mean-reversion score
- [ ] Clicking a screener result row opens the full chart view for that stock
- [ ] Mean-reversion composite score calculated with 70/30 technical/fundamental weighting
- [ ] Screener handles yfinance rate limits gracefully (no crashes on batch requests)

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| User needs all indicator types | Asked what trading style | Swing trading, mean-reversion specifically |
| Full sector scanning needed | Contrarian: composite score per stock enough? | User confirmed automated screening is essential |
| Dynamic sector data needed | Simplifier: curated lists sufficient? | User wants curated lists with fundamental pre-filters |
| Complex configurable UI | Could hardcode defaults | User wants adjustable thresholds in UI |
| Equal weighting for score | Asked about weight preference | 70% technical / 30% fundamental |

## Technical Context
### Existing Codebase
- **Backend**: Flask app (`app.py`, 249 lines) with yfinance data fetching
- **Frontend**: Single `templates/index.html` with ApexCharts, dark theme
- **Current indicators**: SMA (20/50/200), Bollinger Bands (20, 2σ), Relative Strength vs SPY, Volume
- **API pattern**: `/api/stock/<symbol>?period=1y` returns JSON with all chart data
- **Data library**: pandas + numpy for calculations

### New Backend Work Needed
- New functions: `calculate_rsi()`, `calculate_macd()`, `calculate_vwap()`, `calculate_atr()`
- New route: `/api/screener` accepting sector + filter params, returning ranked results
- Curated stock lists as Python constants or config
- Fundamental data fetching via `yf.Ticker(symbol).info` (EPS, P/E, revenue growth)

### New Frontend Work Needed
- Tab navigation: "Chart" | "Screener"
- Screener UI: sector selector, filter sliders/inputs, results table
- New ApexCharts sub-charts for RSI, MACD, ATR
- VWAP overlay on existing price chart
- Click handler on table rows to switch to chart view

## Ontology (Key Entities)
| Entity | Fields | Relationships |
|--------|--------|---------------|
| Stock | symbol, name, price, sector | has many Indicators, has one FundamentalData |
| Indicator | type, value, period, parameters | belongs to Stock |
| FundamentalData | eps, pe_ratio, revenue_growth, market_cap | belongs to Stock |
| ScreenerFilter | dimension, operator, threshold | applied to ScreenerRun |
| ScreenerResult | stock, score, rank, indicator_values | produced by ScreenerRun |
| MeanReversionScore | technical_score, fundamental_score, composite | belongs to ScreenerResult |

## Interview Transcript
<details>
<summary>Full Q&A (10 rounds)</summary>

### Round 1
**Q:** What's your primary investing/trading style?
**A:** Swing trading (days-weeks)
**Ambiguity:** 79%

### Round 2
**Q:** What's the hardest part — finding entries, screening stocks, or managing exits?
**A:** All of the above
**Ambiguity:** 72%

### Round 3
**Q:** Which approach resonates: momentum, trend-following, mean-reversion, or multi-factor?
**A:** Mean-reversion
**Ambiguity:** 62%

### Round 4 (Contrarian)
**Q:** Would a Mean Reversion Score per stock be enough, or is automated screening essential?
**A:** Screening is essential
**Ambiguity:** 53%

### Round 5
**Q:** What stock universe should the screener scan?
**A:** Predefined sectors
**Ambiguity:** 49%

### Round 6
**Q:** (User interjected) Can I add criteria to lessen the list? E.g., only Tech/Energy, EPS thresholds, RSI thresholds
**A:** Layered filtering: sector → fundamentals (EPS, P/E, revenue growth) → technicals (RSI, Bollinger)
**Ambiguity:** 42%

### Round 7
**Q:** Which fundamental filters: EPS only, +P/E, +Revenue Growth?
**A:** EPS + P/E + Revenue Growth
**Ambiguity:** 36%

### Round 8
**Q:** Hardcoded defaults or adjustable UI?
**A:** Adjustable in the UI
**Ambiguity:** 28%

### Round 9
**Q:** How should screener results appear?
**A:** Ranked table with click-through to chart
**Ambiguity:** 21%

### Round 10
**Q:** Separate page or tab? Technical vs fundamental score weighting?
**A:** Tab in existing app. Technicals heavier (70/30).
**Ambiguity:** 14%

</details>
