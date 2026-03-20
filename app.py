from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)


def calculate_sma(data, period):
    """Calculate Simple Moving Average"""
    return data.rolling(window=period, min_periods=1).mean()


def calculate_bollinger_bands(data, period=20, num_std=2):
    """Calculate Bollinger Bands"""
    sma = data.rolling(window=period, min_periods=1).mean()
    std = data.rolling(window=period, min_periods=1).std()
    upper_band = sma + (std * num_std)
    lower_band = sma - (std * num_std)
    return sma, upper_band, lower_band


def calculate_relative_strength(stock_data, benchmark_data):
    """
    Calculate Relative Strength (RS) - compares stock performance to benchmark (S&P 500)
    RS = (Stock Price / Starting Stock Price) / (Benchmark Price / Starting Benchmark Price)
    This shows if the stock is outperforming or underperforming the market
    """
    if len(stock_data) == 0 or len(benchmark_data) == 0:
        return pd.Series()

    # Normalize both to start at 1 (or 100)
    stock_normalized = stock_data / stock_data.iloc[0]
    benchmark_normalized = benchmark_data / benchmark_data.iloc[0]

    # RS = Stock Performance / Benchmark Performance
    # Multiply by 100 for easier reading
    rs = (stock_normalized / benchmark_normalized) * 100
    return rs


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
    h, l, c, v = high.values, low.values, close.values, volume.values
    typical_price = (h + l + c) / 3
    tp_vol = typical_price * v
    dates = index.date
    df = pd.DataFrame({'tp_vol': tp_vol, 'volume': v}, index=index)
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
    band_width = upper - lower
    pctb = (close - lower) / band_width.replace(0, np.nan)
    return pctb


def calculate_mean_reversion_score(rsi_val, pctb_val, macd_hist_vals, eps, pe, rev_growth):
    """
    Composite mean-reversion score (0-100).
    70% technical (RSI, Bollinger %B, MACD) / 30% fundamental (EPS, P/E, Revenue Growth)
    """
    # Technical components (each 0-100)
    rsi_score = min(100, max(0, (40 - rsi_val) / 40 * 100)) if rsi_val is not None else 0
    bb_score = min(100, max(0, (0.5 - pctb_val) / 0.5 * 100)) if pctb_val is not None else 0

    # MACD histogram: fresh crossover = 100, positive = 50, improving = 25, else 0
    macd_score = 0
    if macd_hist_vals and len(macd_hist_vals) >= 2:
        curr, prev = macd_hist_vals[-1], macd_hist_vals[-2]
        if curr > 0 and prev <= 0:
            macd_score = 100
        elif curr > 0:
            macd_score = 50
        elif curr > prev:
            macd_score = 25

    technical_score = (rsi_score + bb_score + macd_score) / 3

    # Fundamental components (each 0-100)
    fund_scores = []
    if eps is not None and eps > 0:
        fund_scores.append(min(100, max(0, eps / 10 * 100)))
    if pe is not None and pe > 0:
        fund_scores.append(min(100, max(0, (30 - pe) / 30 * 100)))
    if rev_growth is not None:
        growth_pct = rev_growth * 100  # Convert 0-1 scale to percentage
        fund_scores.append(min(100, max(0, growth_pct / 20 * 100)))

    if fund_scores:
        fundamental_score = sum(fund_scores) / len(fund_scores)
    else:
        # No fundamental data available — use 100% technical
        return round(technical_score, 2)

    composite = 0.7 * technical_score + 0.3 * fundamental_score
    return round(composite, 2)


# Time period configurations
PERIOD_CONFIG = {
    '1d': {'period': '1d', 'interval': '5m'},
    '5d': {'period': '5d', 'interval': '15m'},
    '1mo': {'period': '1mo', 'interval': '1h'},
    '3mo': {'period': '3mo', 'interval': '1d'},
    '1y': {'period': '1y', 'interval': '1d'},
    '5y': {'period': '5y', 'interval': '1wk'},
    'max': {'period': 'max', 'interval': '1mo'},
}

# Curated stock lists per sector
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


def _serialize_series(series):
    """Convert a pandas Series to ApexCharts-compatible [{x, y}] format"""
    return [{'x': int(idx.timestamp() * 1000),
             'y': round(float(val), 2) if not pd.isna(val) else None}
            for idx, val in series.items()]


def _fetch_stock_for_screener(symbol):
    """Fetch fundamentals + 3mo technicals for one stock (used by screener)"""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        hist = yf.download(symbol, period='3mo', interval='1d', progress=False, auto_adjust=True)
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        if hist.empty:
            return None

        close = hist['Close']
        rsi_series = calculate_rsi(close)
        pctb_series = calculate_bollinger_pctb(close)
        _, _, macd_h = calculate_macd(close)

        rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None
        pctb_val = float(pctb_series.iloc[-1]) if not pd.isna(pctb_series.iloc[-1]) else None
        macd_hist_vals = [round(float(v), 4) if not pd.isna(v) else 0 for v in macd_h.iloc[-2:].tolist()]

        return {
            'symbol': symbol,
            'name': info.get('longName') or info.get('shortName') or symbol,
            'price': round(float(close.iloc[-1]), 2),
            'rsi': round(rsi_val, 2) if rsi_val is not None else None,
            'pctb': round(pctb_val, 4) if pctb_val is not None else None,
            'macd_hist': macd_hist_vals,
            'eps': info.get('trailingEps'),
            'pe': info.get('trailingPE'),
            'rev_growth': info.get('revenueGrowth') or info.get('revenueQuarterlyGrowth'),
        }
    except Exception:
        return None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/stock/<symbol>')
def get_stock(symbol):
    period = request.args.get('period', '1y')

    if period not in PERIOD_CONFIG:
        return jsonify({'error': 'Invalid period'}), 400

    try:
        symbol = symbol.upper()
        config = PERIOD_CONFIG[period]

        hist = yf.download(
            symbol,
            period=config['period'],
            interval=config['interval'],
            progress=False,
            auto_adjust=True,
        )

        if hist.empty:
            return jsonify({'error': 'No data found for this symbol. Please try again.'}), 404

        ticker = yf.Ticker(symbol)
        info = ticker.info

        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)

        close_prices = hist['Close']

        # SMAs
        sma_20 = calculate_sma(close_prices, 20)
        sma_50 = calculate_sma(close_prices, 50)
        sma_200 = calculate_sma(close_prices, 200)

        # Bollinger Bands
        bb_middle, bb_upper, bb_lower = calculate_bollinger_bands(close_prices, 20, 2)

        # RSI
        rsi = calculate_rsi(close_prices)

        # MACD
        macd_line, macd_signal, macd_histogram = calculate_macd(close_prices)

        # ATR
        atr = calculate_atr(hist['High'], hist['Low'], close_prices)

        # VWAP (intraday periods only)
        vwap = None
        if period in ('1d', '5d'):
            vwap = calculate_vwap(hist['High'], hist['Low'], close_prices, hist['Volume'], hist.index)

        # Fetch benchmark (S&P 500) for Relative Strength
        try:
            benchmark = yf.download(
                'SPY',
                period=config['period'],
                interval=config['interval'],
                progress=False,
                auto_adjust=True
            )
            if isinstance(benchmark.columns, pd.MultiIndex):
                benchmark.columns = benchmark.columns.get_level_values(0)

            benchmark_close = benchmark['Close'].reindex(hist.index, method='ffill')
            rs_data = calculate_relative_strength(close_prices, benchmark_close)
        except Exception:
            rs_data = pd.Series(index=hist.index, data=100)

        # Format data for chart
        chart_data = []
        for idx, row in hist.iterrows():
            timestamp = int(idx.timestamp() * 1000)
            chart_data.append({
                'x': timestamp,
                'y': [
                    round(float(row['Open']), 2),
                    round(float(row['High']), 2),
                    round(float(row['Low']), 2),
                    round(float(row['Close']), 2)
                ]
            })

        line_data = [{'x': int(idx.timestamp() * 1000), 'y': round(float(row['Close']), 2)}
                     for idx, row in hist.iterrows()]

        volume_data = []
        for idx, row in hist.iterrows():
            timestamp = int(idx.timestamp() * 1000)
            is_up = row['Close'] >= row['Open']
            volume_data.append({
                'x': timestamp,
                'y': int(row['Volume']) if not pd.isna(row['Volume']) else 0,
                'fillColor': '#00ff88' if is_up else '#ff4757'
            })

        # Serialize indicator data
        sma_20_data = _serialize_series(sma_20)
        sma_50_data = _serialize_series(sma_50)
        sma_200_data = _serialize_series(sma_200)
        bb_upper_data = _serialize_series(bb_upper)
        bb_middle_data = _serialize_series(bb_middle)
        bb_lower_data = _serialize_series(bb_lower)
        rs_chart_data = _serialize_series(rs_data)
        rsi_data = _serialize_series(rsi)
        atr_data = _serialize_series(atr)

        # MACD: line, signal, histogram (with color)
        macd_line_data = _serialize_series(macd_line)
        macd_signal_data = _serialize_series(macd_signal)
        macd_hist_data = [{'x': int(idx.timestamp() * 1000),
                           'y': round(float(val), 4) if not pd.isna(val) else None,
                           'fillColor': '#00ff88' if (not pd.isna(val) and val >= 0) else '#ff4757'}
                          for idx, val in macd_histogram.items()]

        vwap_data = _serialize_series(vwap) if vwap is not None else None

        # Calculate price change
        if len(hist) >= 2:
            current_price = float(hist['Close'].iloc[-1])
            previous_price = float(hist['Close'].iloc[0])
            price_change = current_price - previous_price
            price_change_pct = (price_change / previous_price) * 100
        else:
            current_price = float(hist['Close'].iloc[-1]) if len(hist) > 0 else 0
            price_change = 0
            price_change_pct = 0

        try:
            stock_name = info.get('longName') or info.get('shortName') or symbol
            currency = info.get('currency', 'USD')
            market_cap = info.get('marketCap', 'N/A')
            vol = info.get('volume', 'N/A')
            high_52w = info.get('fiftyTwoWeekHigh', 'N/A')
            low_52w = info.get('fiftyTwoWeekLow', 'N/A')
        except Exception:
            stock_name = symbol
            currency = 'USD'
            market_cap = 'N/A'
            vol = 'N/A'
            high_52w = 'N/A'
            low_52w = 'N/A'

        response = {
            'symbol': symbol,
            'name': stock_name,
            'currentPrice': round(current_price, 2),
            'priceChange': round(price_change, 2),
            'priceChangePct': round(price_change_pct, 2),
            'currency': currency,
            'marketCap': market_cap,
            'volume': vol,
            'high52Week': high_52w,
            'low52Week': low_52w,
            'candlestickData': chart_data,
            'lineData': line_data,
            'volumeData': volume_data,
            'sma20': sma_20_data,
            'sma50': sma_50_data,
            'sma200': sma_200_data,
            'bbUpper': bb_upper_data,
            'bbMiddle': bb_middle_data,
            'bbLower': bb_lower_data,
            'relativeStrength': rs_chart_data,
            'rsi': rsi_data,
            'macdLine': macd_line_data,
            'macdSignal': macd_signal_data,
            'macdHistogram': macd_hist_data,
            'atr': atr_data,
            'period': period
        }

        if vwap_data is not None:
            response['vwap'] = vwap_data

        return jsonify(response)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/screener/scan')
def screener_scan():
    """Scan a sector: fetch fundamentals + technicals for all stocks, cache results"""
    sector = request.args.get('sector', '').lower()
    force = request.args.get('force', '0') == '1'

    if sector not in SECTOR_STOCKS:
        return jsonify({'error': f'Invalid sector. Choose from: {", ".join(SECTOR_STOCKS.keys())}'}), 400

    # Check cache
    if not force and sector in _screener_cache:
        cache_entry = _screener_cache[sector]
        if time.time() - cache_entry['timestamp'] < CACHE_TTL:
            return jsonify({
                'data': cache_entry['data'],
                'scanned': cache_entry['scanned'],
                'failed': cache_entry['failed'],
                'cached': True
            })

    stocks = SECTOR_STOCKS[sector]
    results = []
    failed = 0

    # Fetch in batches of 5 with ThreadPoolExecutor
    batch_size = 5
    for i in range(0, len(stocks), batch_size):
        batch = stocks[i:i + batch_size]
        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = {executor.submit(_fetch_stock_for_screener, sym): sym for sym in batch}
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    results.append(result)
                else:
                    failed += 1
        # Rate limit between batches
        if i + batch_size < len(stocks):
            time.sleep(0.5)

    # Cache results
    _screener_cache[sector] = {
        'data': results,
        'scanned': len(stocks),
        'failed': failed,
        'timestamp': time.time()
    }

    return jsonify({
        'data': results,
        'scanned': len(stocks),
        'failed': failed,
        'cached': False
    })


@app.route('/api/screener/results')
def screener_results():
    """Filter cached scan results and return ranked results with scores"""
    sector = request.args.get('sector', '').lower()

    if sector not in SECTOR_STOCKS:
        return jsonify({'error': f'Invalid sector. Choose from: {", ".join(SECTOR_STOCKS.keys())}'}), 400

    if sector not in _screener_cache:
        return jsonify({'error': 'No scan data. Run a scan first.'}), 400

    # Parse filter params with defaults
    eps_min = float(request.args.get('eps_min', 0))
    pe_max = float(request.args.get('pe_max', 30))
    rev_growth_min = float(request.args.get('rev_growth_min', 5))  # percentage
    rsi_max = float(request.args.get('rsi_max', 40))
    bb_pctb_max = float(request.args.get('bb_pctb_max', 0.2))

    cached_data = _screener_cache[sector]['data']
    filtered = []

    for stock in cached_data:
        # Fundamental filters (skip filter if data is None)
        if stock.get('eps') is not None and stock['eps'] < eps_min:
            continue
        if stock.get('pe') is not None and stock['pe'] > pe_max:
            continue
        if stock.get('rev_growth') is not None and (stock['rev_growth'] * 100) < rev_growth_min:
            continue

        # Technical filters (skip filter if data is None)
        if stock.get('rsi') is not None and stock['rsi'] > rsi_max:
            continue
        if stock.get('pctb') is not None and stock['pctb'] > bb_pctb_max:
            continue

        # Calculate score
        score = calculate_mean_reversion_score(
            stock.get('rsi'),
            stock.get('pctb'),
            stock.get('macd_hist'),
            stock.get('eps'),
            stock.get('pe'),
            stock.get('rev_growth')
        )

        filtered.append({
            'symbol': stock['symbol'],
            'name': stock['name'],
            'price': stock['price'],
            'rsi': stock.get('rsi'),
            'pctb': stock.get('pctb'),
            'eps': stock.get('eps'),
            'pe': round(stock['pe'], 2) if stock.get('pe') is not None else None,
            'revGrowth': round(stock['rev_growth'] * 100, 2) if stock.get('rev_growth') is not None else None,
            'score': score
        })

    # Sort by score descending
    filtered.sort(key=lambda x: x['score'], reverse=True)

    return jsonify({
        'results': filtered,
        'total_scanned': _screener_cache[sector]['scanned'],
        'total_passed': len(filtered)
    })


@app.route('/api/search/<query>')
def search_stocks(query):
    """Search for stock symbols"""
    try:
        ticker = yf.Ticker(query.upper())
        info = ticker.info

        if 'symbol' in info:
            return jsonify({
                'results': [{
                    'symbol': info.get('symbol', query.upper()),
                    'name': info.get('longName', info.get('shortName', query.upper())),
                    'exchange': info.get('exchange', 'N/A')
                }]
            })
        return jsonify({'results': []})
    except Exception:
        return jsonify({'results': []})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
