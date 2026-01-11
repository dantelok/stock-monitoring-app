from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np

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
        
        # Use yf.download() with session for reliability
        hist = yf.download(
            symbol,
            period=config['period'],
            interval=config['interval'],
            progress=False,
            auto_adjust=True,
        )

        if hist.empty:
            return jsonify({'error': 'No data found for this symbol. Please try again.'}), 404
        
        # Get stock info with session
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        # Handle multi-level columns from yf.download (flatten if needed)
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        
        # Calculate Technical Indicators
        close_prices = hist['Close']
        
        # SMAs
        sma_20 = calculate_sma(close_prices, 20)
        sma_50 = calculate_sma(close_prices, 50)
        sma_200 = calculate_sma(close_prices, 200)
        
        # Bollinger Bands
        bb_middle, bb_upper, bb_lower = calculate_bollinger_bands(close_prices, 20, 2)
        
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
            
            # Align benchmark data with stock data
            benchmark_close = benchmark['Close'].reindex(hist.index, method='ffill')
            rs_data = calculate_relative_strength(close_prices, benchmark_close)
        except:
            rs_data = pd.Series(index=hist.index, data=100)  # Default to 100 if benchmark fails
        
        # Format data for chart
        chart_data = []
        for index, row in hist.iterrows():
            timestamp = int(index.timestamp() * 1000)  # Convert to milliseconds
            chart_data.append({
                'x': timestamp,
                'y': [
                    round(float(row['Open']), 2),
                    round(float(row['High']), 2),
                    round(float(row['Low']), 2),
                    round(float(row['Close']), 2)
                ]
            })
        
        # Line chart data (closing prices)
        line_data = []
        for index, row in hist.iterrows():
            timestamp = int(index.timestamp() * 1000)
            line_data.append({
                'x': timestamp,
                'y': round(float(row['Close']), 2)
            })
        
        # Volume data
        volume_data = []
        for index, row in hist.iterrows():
            timestamp = int(index.timestamp() * 1000)
            # Color based on price movement
            is_up = row['Close'] >= row['Open']
            volume_data.append({
                'x': timestamp,
                'y': int(row['Volume']) if not pd.isna(row['Volume']) else 0,
                'fillColor': '#00ff88' if is_up else '#ff4757'
            })
        
        # SMA data
        sma_20_data = [{'x': int(idx.timestamp() * 1000), 'y': round(float(val), 2) if not pd.isna(val) else None} 
                       for idx, val in sma_20.items()]
        sma_50_data = [{'x': int(idx.timestamp() * 1000), 'y': round(float(val), 2) if not pd.isna(val) else None} 
                       for idx, val in sma_50.items()]
        sma_200_data = [{'x': int(idx.timestamp() * 1000), 'y': round(float(val), 2) if not pd.isna(val) else None} 
                        for idx, val in sma_200.items()]
        
        # Bollinger Bands data
        bb_upper_data = [{'x': int(idx.timestamp() * 1000), 'y': round(float(val), 2) if not pd.isna(val) else None} 
                         for idx, val in bb_upper.items()]
        bb_middle_data = [{'x': int(idx.timestamp() * 1000), 'y': round(float(val), 2) if not pd.isna(val) else None} 
                          for idx, val in bb_middle.items()]
        bb_lower_data = [{'x': int(idx.timestamp() * 1000), 'y': round(float(val), 2) if not pd.isna(val) else None} 
                         for idx, val in bb_lower.items()]
        
        # Relative Strength data
        rs_chart_data = [{'x': int(idx.timestamp() * 1000), 'y': round(float(val), 2) if not pd.isna(val) else None} 
                         for idx, val in rs_data.items()]
        
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
        
        # Safely get info (may fail for some tickers)
        try:
            stock_name = info.get('longName') or info.get('shortName') or symbol
            currency = info.get('currency', 'USD')
            market_cap = info.get('marketCap', 'N/A')
            volume = info.get('volume', 'N/A')
            high_52w = info.get('fiftyTwoWeekHigh', 'N/A')
            low_52w = info.get('fiftyTwoWeekLow', 'N/A')
        except:
            stock_name = symbol
            currency = 'USD'
            market_cap = 'N/A'
            volume = 'N/A'
            high_52w = 'N/A'
            low_52w = 'N/A'
        
        return jsonify({
            'symbol': symbol,
            'name': stock_name,
            'currentPrice': round(current_price, 2),
            'priceChange': round(price_change, 2),
            'priceChangePct': round(price_change_pct, 2),
            'currency': currency,
            'marketCap': market_cap,
            'volume': volume,
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
            'period': period
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
    except:
        return jsonify({'results': []})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
