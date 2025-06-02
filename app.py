from flask import Flask, render_template, request, redirect, url_for, session, flash
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from datetime import datetime
import pandas as pd
from requests_html import HTMLSession
from pygooglenews import GoogleNews
import sqlite3
import time
import requests
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Database initialization
def startdb():
    conn = sqlite3.connect('trade_history.db')
    conn.cursor().execute('''
        CREATE TABLE IF NOT EXISTS trades (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              symbol TEXT,
              qty REAL,
              side TEXT,
              time TEXT,
              price REAL,
              market_cap REAL
              )
        ''')
    conn.commit()
    conn.close()

def log_trade_to_db(symbol, qty, side, price, market_cap):
    conn = sqlite3.connect('trade_history.db')
    now = datetime.now().isoformat()
    conn.cursor().execute('''
        INSERT INTO trades (symbol, qty, side, time, price, market_cap)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (symbol, qty, side, now, price, market_cap))
    conn.commit()
    conn.close()

def get_trade_history():
    conn = sqlite3.connect('trade_history.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades")
    trades = cursor.fetchall()
    conn.close()
    return trades

def convert_cap(market_cap):
    try:
        if isinstance(market_cap, (int, float)):
            return float(market_cap)
        cap_str = str(market_cap).replace(',', '').upper()
        if 'B' in cap_str:
            return float(cap_str.replace('B', '')) * 1000000000
        elif 'M' in cap_str:
            return float(cap_str.replace('M', '')) * 1000000
        else:
            return float(cap_str)
    except:
        return 0.0

def get_top_gainers():
    site = "https://finance.yahoo.com/gainers?offset=0&count=100"
    try:
        session = HTMLSession()
        response = session.get(site)
        tables = pd.read_html(response.html.raw_html)
        session.close()
        if len(tables) > 0:
            df = tables[0]
            if "Symbol" in df.columns and "Market Cap" in df.columns and "Price" in df.columns:
                return list(zip(df["Symbol"].tolist(), df["Market Cap"].tolist(), df["Price"].tolist()))
        return []
    except Exception as e:
        print(f"Error fetching gainers: {e}")
        return []

def newscheck(symbol):
    try:
        s = GoogleNews().search(symbol)
        keywords = ["merger", "acquisition", "acquires", "merge", "buyout", "IPO", "initial public offering"]
        for entry in s["entries"]:
            title = entry["title"].lower()
            if any(keyword in title for keyword in keywords):
                return False
        return True
    except:
        return False

def tradable(symbol, trading_client):
    if newscheck(symbol):
        try:
            asset = trading_client.get_asset(symbol)
            return asset.tradable
        except Exception:
            return False
    return False

def get_current_price(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        data = requests.get(url, headers=headers).json()
        return data['chart']['result'][0]['meta']['regularMarketPrice']
    except:
        return None

def submit_order(symbol, qty, trading_client, market_cap):
    if tradable(symbol, trading_client):
        try:
            market_order_data = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY
            )
            trading_client.submit_order(order_data=market_order_data)
            price_ex = get_current_price(symbol)
            log_trade_to_db(symbol, qty, "SELL", price_ex, market_cap)
            return True, f"Successfully submitted short order for {symbol}"
        except Exception as e:
            return False, f"Order failed: {str(e)[:100]}"
    return False, f"{symbol} is not tradable"

# Initialize database
startdb()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start():
    session['api_key'] = request.form['api_key']
    session['api_secret'] = request.form['api_secret']
    session['min_cap'] = float(request.form['min_cap'])
    session['max_attempts'] = int(request.form['max_attempts'])
    return redirect(url_for('process_gainers'))

@app.route('/continue_trading')
def continue_trading():
    # Reset only the trading state, keep credentials
    session.pop('gainers', None)
    session.pop('current_index', None)
    session.pop('attempts', None)
    session.pop('trade_error', None)
    return redirect(url_for('process_gainers'))

@app.route('/process_gainers')
def process_gainers():
    # Check if credentials are present
    if 'api_key' not in session or 'api_secret' not in session:
        flash('API credentials missing', 'danger')
        return redirect(url_for('index'))

    # Clear any previous trade error
    session.pop('trade_error', None)

    # Fetch new gainers if not in session
    if 'gainers' not in session or not session['gainers']:
        try:
            gainers = get_top_gainers()
            if not gainers:
                flash('Failed to fetch top gainers', 'danger')
                return redirect(url_for('index'))

            min_cap = session['min_cap']
            filtered_gainers = []
            for symbol, market_cap, price in gainers:
                cap_value = convert_cap(market_cap)
                if cap_value >= min_cap:
                    filtered_gainers.append((symbol, market_cap, price))

            session['gainers'] = filtered_gainers
            session['current_index'] = 0
            session['attempts'] = 0
        except Exception as e:
            flash(f"Error processing gainers: {str(e)}", 'danger')
            return redirect(url_for('index'))

    # Check if we have reached max attempts
    if session['attempts'] >= session['max_attempts']:
        flash('Maximum attempts reached', 'info')
        return redirect(url_for('index'))

    # Check if we have processed all gainers
    if session['current_index'] >= len(session['gainers']):
        flash('No more stocks to process', 'info')
        return redirect(url_for('index'))

    # Process the next stock
    symbol, market_cap, price = session['gainers'][session['current_index']]
    trading_client = TradingClient(session['api_key'], session['api_secret'], paper=True)

    if tradable(symbol, trading_client):
        # Store current stock in session for the trade page
        session['current_symbol'] = symbol
        session['current_market_cap'] = market_cap
        session['current_price'] = price
        return redirect(url_for('trade'))
    else:
        # This stock is not tradable, move to next
        session['current_index'] += 1
        session['attempts'] += 1
        flash(f"{symbol} is not tradable", 'warning')
        return redirect(url_for('process_gainers'))

@app.route('/trade')
def trade():
    if 'current_symbol' not in session:
        flash('No stock selected for trading', 'danger')
        return redirect(url_for('index'))

    # Check if there's a trade error to display
    trade_error = session.pop('trade_error', None)

    return render_template(
        'trade.html',
        symbol=session['current_symbol'],
        market_cap=session['current_market_cap'],
        price=session['current_price'],
        attempts=session['attempts'] + 1,
        max_attempts=session['max_attempts'],
        trade_error=trade_error
    )

@app.route('/execute_trade', methods=['POST'])
def execute_trade():
    qty = float(request.form['qty'])
    trading_client = TradingClient(session['api_key'], session['api_secret'], paper=True)
    success, message = submit_order(
        session['current_symbol'],
        qty,
        trading_client,
        session['current_market_cap']
    )

    if success:
        # Clear trading state to exit loop
        session.pop('gainers', None)
        session.pop('current_index', None)
        session.pop('attempts', None)
        session.pop('current_symbol', None)
        session.pop('current_market_cap', None)
        session.pop('current_price', None)
        session.pop('trade_error', None)
        flash(message, 'success')
        return redirect(url_for('index'))
    else:
        # Store error in session to display on trade page
        session['trade_error'] = message
        return redirect(url_for('trade'))

@app.route('/skip_trade')
def skip_trade():
    # Move to next stock without trading
    symbol = session['current_symbol']
    session['current_index'] += 1
    session['attempts'] += 1
    session.pop('trade_error', None)

    # Check if we should continue or stop
    if session['attempts'] >= session['max_attempts']:
        flash('Maximum attempts reached', 'info')
        return redirect(url_for('index'))
    elif session['current_index'] >= len(session['gainers']):
        flash('No more stocks to process', 'info')
        return redirect(url_for('index'))
    else:
        flash(f"Skipped {symbol}", 'info')
        return redirect(url_for('process_gainers'))

@app.route('/history')
def history():
    trades = get_trade_history()
    return render_template('history.html', trades=trades)

if __name__ == '__main__':
    app.run(debug=True)

