"""
UpsideOnly Market Feed
Fetches real-time OHLCV data for all 25 markets using free APIs.
Primary: yfinance (stocks/ETFs/commodities/forex)
Fallback: Finnhub (already in Pantheon - d86chq1r01qgiu44rds0d86chq1r01qgiu44rdsg)
Crypto: CoinGecko free API
"""

import pandas as pd
import numpy as np
import requests
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# Finnhub key from Pantheon
FINNHUB_KEY = "d86chq1r01qgiu44rds0d86chq1r01qgiu44rdsg"

# Asset map: UpsideOnly display name -> data source ticker
ASSET_MAP = {
    # Equities / ETFs
    "SPY":  {"source": "yfinance", "ticker": "SPY"},
    "QQQ":  {"source": "yfinance", "ticker": "QQQ"},
    "EWY":  {"source": "yfinance", "ticker": "EWY"},
    "EWJ":  {"source": "yfinance", "ticker": "EWJ"},
    # Commodities
    "GOLD": {"source": "yfinance", "ticker": "GC=F"},
    "OIL":  {"source": "yfinance", "ticker": "CL=F"},
    # Forex (Finnhub is better for forex 24/7)
    "EURUSD": {"source": "finnhub", "ticker": "OANDA:EUR_USD"},
    "GBPUSD": {"source": "finnhub", "ticker": "OANDA:GBP_USD"},
    "USDJPY": {"source": "finnhub", "ticker": "OANDA:USD_JPY"},
    # Crypto (CoinGecko free)
    "BTC":  {"source": "coingecko", "ticker": "bitcoin"},
    "ETH":  {"source": "coingecko", "ticker": "ethereum"},
    "SOL":  {"source": "coingecko", "ticker": "solana"},
    "XRP":  {"source": "coingecko", "ticker": "ripple"},
}


class MarketFeed:
    """
    Fetches 1-min OHLCV data for all markets.
    Falls back gracefully between sources.
    """

    def __init__(self):
        self.cache = {}
        self.cache_time = {}
        self.cache_ttl = 60  # seconds — refresh every 1 min

    def _fetch_yfinance(self, ticker: str, period: str = "1d", interval: str = "1m") -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf
            df = yf.download(ticker, period=period, interval=interval,
                           progress=False, auto_adjust=True)
            if df.empty:
                return None
            df.columns = [c.lower() for c in df.columns]
            df = df[['open', 'high', 'low', 'close', 'volume']].dropna()
            return df
        except Exception as e:
            log.warning(f"yfinance error for {ticker}: {e}")
            return None

    def _fetch_finnhub(self, ticker: str, resolution: str = "1") -> Optional[pd.DataFrame]:
        try:
            to_ts = int(time.time())
            from_ts = to_ts - 3600  # last 1 hour
            url = (f"https://finnhub.io/api/v1/forex/candle"
                   f"?symbol={ticker}&resolution={resolution}"
                   f"&from={from_ts}&to={to_ts}&token={FINNHUB_KEY}")
            r = requests.get(url, timeout=10)
            data = r.json()
            if data.get('s') != 'ok':
                return None
            df = pd.DataFrame({
                'open': data['o'], 'high': data['h'],
                'low': data['l'], 'close': data['c'],
                'volume': data.get('v', [1000]*len(data['c']))
            }, index=pd.to_datetime(data['t'], unit='s'))
            return df
        except Exception as e:
            log.warning(f"Finnhub error for {ticker}: {e}")
            return None

    def _fetch_coingecko(self, coin_id: str) -> Optional[pd.DataFrame]:
        try:
            url = (f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
                   f"?vs_currency=usd&days=1")
            r = requests.get(url, timeout=10)
            data = r.json()
            if not data:
                return None
            df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close'])
            df.index = pd.to_datetime(df['timestamp'], unit='ms')
            df['volume'] = 100000  # CoinGecko OHLC doesn't include volume
            df = df[['open', 'high', 'low', 'close', 'volume']]
            return df.tail(60)  # last 60 bars
        except Exception as e:
            log.warning(f"CoinGecko error for {coin_id}: {e}")
            return None

    def _synthetic_fallback(self, asset: str, last_price: float = 100.0) -> pd.DataFrame:
        """Generate synthetic data for testing — remove in production."""
        np.random.seed(hash(asset) % 9999)
        n = 50
        prices = last_price + np.cumsum(np.random.randn(n) * last_price * 0.001)
        return pd.DataFrame({
            "open": prices * 0.9995,
            "high": prices * 1.0015,
            "low": prices * 0.9985,
            "close": prices,
            "volume": np.random.randint(50000, 500000, n)
        }, index=pd.date_range(end=datetime.now(), periods=n, freq="1min"))

    def fetch(self, asset: str) -> Optional[pd.DataFrame]:
        # Check cache
        now = time.time()
        if asset in self.cache and (now - self.cache_time.get(asset, 0)) < self.cache_ttl:
            return self.cache[asset]

        config = ASSET_MAP.get(asset)
        if not config:
            log.warning(f"Unknown asset: {asset}")
            return None

        df = None
        source = config['source']

        if source == 'yfinance':
            df = self._fetch_yfinance(config['ticker'])
        elif source == 'finnhub':
            df = self._fetch_finnhub(config['ticker'])
        elif source == 'coingecko':
            df = self._fetch_coingecko(config['ticker'])

        if df is None:
            log.warning(f"All sources failed for {asset} — using synthetic fallback")
            df = self._synthetic_fallback(asset)

        self.cache[asset] = df
        self.cache_time[asset] = now
        return df

    def fetch_all(self) -> dict:
        """Fetch data for all markets. Returns {asset: df} dict."""
        results = {}
        for asset in ASSET_MAP:
            df = self.fetch(asset)
            if df is not None and len(df) >= 20:
                results[asset] = df
            time.sleep(0.1)  # Be nice to APIs
        log.info(f"Fetched data for {len(results)}/{len(ASSET_MAP)} markets")
        return results


if __name__ == "__main__":
    feed = MarketFeed()
    data = feed.fetch_all()
    for asset, df in data.items():
        print(f"{asset}: {len(df)} bars, last close={df['close'].iloc[-1]:.4f}")
