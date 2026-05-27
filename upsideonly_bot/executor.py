"""
UpsideOnly Trade Executor
Uses Playwright to interact with the UpsideOnly web platform.
Reads signals from SignalEngine, executes trades, manages positions.

Requirements:
    pip install playwright pandas numpy requests yfinance
    playwright install chromium
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, Browser

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [EXEC] %(message)s')

# ── Config ──────────────────────────────────────────────────────────────────
BASE_URL        = "https://upsideonly.com"
EMAIL           = "kevinleestites2@gmail.com"  # Google SSO
POSITION_SIZE   = 0.01         # 1% of $100k = $1,000 per trade
MAX_OPEN_TRADES = 5            # max concurrent positions
SCAN_INTERVAL   = 60           # seconds between market scans
LOG_PATH        = Path("trade_log.json")
TELEGRAM_TOKEN  = "8679655550:AAGUB1m5fmqHc8OHqqM24Vixz8FfwX-gqD4"
TELEGRAM_CHAT   = "7135054241"
# ────────────────────────────────────────────────────────────────────────────


def send_telegram(msg: str):
    import requests
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT, "text": msg}, timeout=5)
    except Exception as e:
        log.warning(f"Telegram failed: {e}")


def log_trade(trade: dict):
    trades = []
    if LOG_PATH.exists():
        trades = json.loads(LOG_PATH.read_text())
    trades.append({**trade, "timestamp": datetime.now().isoformat()})
    LOG_PATH.write_text(json.dumps(trades, indent=2))


class UpsideOnlyExecutor:
    """
    Playwright-based executor for UpsideOnly.
    Handles login, trade placement, position monitoring.
    """

    def __init__(self):
        self.page: Optional[Page] = None
        self.browser: Optional[Browser] = None
        self.open_trades: list[dict] = []
        self.balance = 100_000.0
        self.session_pnl = 0.0

    async def launch(self, headless: bool = True):
        pw = await async_playwright().start()
        self.browser = await pw.chromium.launch(headless=headless)
        context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"
        )
        self.page = await context.new_page()
        log.info("Browser launched")

    async def login_google(self) -> bool:
        """Navigate to UpsideOnly and trigger Google SSO."""
        await self.page.goto(f"{BASE_URL}/ZeT4IkAe2B-sVxrf?mode=login")
        await self.page.wait_for_load_state("networkidle")

        # Dismiss cookie banner
        try:
            await self.page.click("button:has-text('Accept All')", timeout=3000)
        except Exception:
            pass

        # Click Continue with Google
        try:
            await self.page.click("button:has-text('Continue with Google')", timeout=5000)
            log.info("Clicked Google SSO button")
            # Google OAuth popup will appear — needs manual auth once,
            # then session is saved in context
            await self.page.wait_for_url("**/trade**", timeout=30000)
            log.info("Login successful — on trading page")
            return True
        except Exception as e:
            log.error(f"Login failed: {e}")
            return False

    async def get_current_balance(self) -> float:
        """Read current virtual balance from the dashboard."""
        try:
            balance_el = await self.page.wait_for_selector(
                "[data-testid='balance'], .balance, text=/$[0-9,]+/",
                timeout=5000
            )
            text = await balance_el.inner_text()
            return float(text.replace("$", "").replace(",", ""))
        except Exception:
            return self.balance

    async def get_open_positions(self) -> list[dict]:
        """Scrape open positions from the portfolio/positions panel."""
        positions = []
        try:
            # Navigate to positions tab
            await self.page.click("text=Positions", timeout=3000)
            await self.page.wait_for_timeout(1000)

            rows = await self.page.query_selector_all(".position-row, [data-testid='position']")
            for row in rows:
                text = await row.inner_text()
                positions.append({"raw": text})
        except Exception as e:
            log.warning(f"Could not fetch positions: {e}")
        return positions

    async def place_trade(self, signal) -> bool:
        """
        Execute a trade from a Signal object.
        Opens the asset, sets direction, size, TP, SL and submits.
        """
        if len(self.open_trades) >= MAX_OPEN_TRADES:
            log.info(f"Max open trades ({MAX_OPEN_TRADES}) reached — skipping {signal.asset}")
            return False

        log.info(f"Placing {signal.direction.upper()} trade: {signal.asset} "
                 f"entry={signal.entry_price:.4f} TP={signal.take_profit:.4f} "
                 f"SL={signal.stop_loss:.4f} conf={signal.confidence:.0%}")

        try:
            # ── Step 1: Navigate to asset ──────────────────────────────────
            # Click on the asset in the markets list
            # Try direct search first
            search = self.page.locator("input[placeholder*='search'], input[placeholder*='Search']")
            if await search.count() > 0:
                await search.first.fill(signal.asset)
                await self.page.wait_for_timeout(500)

            # Click the asset
            asset_link = self.page.locator(
                f"text={signal.asset}",
            ).first
            await asset_link.click(timeout=5000)
            await self.page.wait_for_timeout(1000)

            # ── Step 2: Select direction ───────────────────────────────────
            if signal.direction == "short":
                sell_btn = self.page.locator("button:has-text('Sell'), button:has-text('Short')")
                await sell_btn.first.click(timeout=3000)
            else:
                buy_btn = self.page.locator("button:has-text('Buy'), button:has-text('Long')")
                await buy_btn.first.click(timeout=3000)

            await self.page.wait_for_timeout(500)

            # ── Step 3: Set position size ──────────────────────────────────
            size_usd = self.balance * POSITION_SIZE
            size_input = self.page.locator(
                "input[name*='size'], input[name*='amount'], input[placeholder*='amount']"
            ).first
            await size_input.fill(str(int(size_usd)))
            await self.page.wait_for_timeout(300)

            # ── Step 4: Set Take Profit ────────────────────────────────────
            try:
                tp_input = self.page.locator(
                    "input[name*='take_profit'], input[placeholder*='Take profit']"
                ).first
                await tp_input.fill(f"{signal.take_profit:.4f}")
            except Exception:
                log.warning("Could not set TP — skipping")

            # ── Step 5: Set Stop Loss ──────────────────────────────────────
            try:
                sl_input = self.page.locator(
                    "input[name*='stop_loss'], input[placeholder*='Stop loss']"
                ).first
                await sl_input.fill(f"{signal.stop_loss:.4f}")
            except Exception:
                log.warning("Could not set SL — skipping")

            await self.page.wait_for_timeout(300)

            # ── Step 6: Submit ─────────────────────────────────────────────
            submit_btn = self.page.locator(
                "button[type='submit'], button:has-text('Place Order'), button:has-text('Trade')"
            ).first
            await submit_btn.click(timeout=5000)
            await self.page.wait_for_timeout(1500)

            # ── Log trade ─────────────────────────────────────────────────
            trade = {
                "asset": signal.asset,
                "direction": signal.direction,
                "entry": signal.entry_price,
                "tp": signal.take_profit,
                "sl": signal.stop_loss,
                "confidence": signal.confidence,
                "strategy": signal.strategy,
                "size_usd": size_usd,
                "status": "open"
            }
            self.open_trades.append(trade)
            log_trade(trade)

            msg = (f"✅ TRADE PLACED\n"
                   f"{signal.direction.upper()} {signal.asset}\n"
                   f"Entry: {signal.entry_price:.4f}\n"
                   f"TP: {signal.take_profit:.4f} | SL: {signal.stop_loss:.4f}\n"
                   f"Confidence: {signal.confidence:.0%} | R:R {signal.rr_ratio:.1f}\n"
                   f"Strategy: {signal.strategy}")
            send_telegram(msg)
            log.info(f"Trade placed successfully: {signal.asset}")
            return True

        except Exception as e:
            log.error(f"Trade placement failed for {signal.asset}: {e}")
            return False

    async def run_cycle(self, signals: list):
        """Process a batch of signals from the signal engine."""
        placed = 0
        for sig in signals:
            if len(self.open_trades) >= MAX_OPEN_TRADES:
                break
            # Skip duplicate assets already in open trades
            if any(t['asset'] == sig.asset for t in self.open_trades):
                continue
            success = await self.place_trade(sig)
            if success:
                placed += 1
                await asyncio.sleep(2)  # Pace the orders

        log.info(f"Cycle complete: {placed} new trades placed, {len(self.open_trades)} total open")
        return placed

    async def close(self):
        if self.browser:
            await self.browser.close()


# ── Main Loop ────────────────────────────────────────────────────────────────
async def main():
    """
    Full trading loop:
    1. Login
    2. Every 60s: fetch market data, generate signals, place trades
    3. Monitor positions
    4. Report to Telegram
    """
    from market_feed import MarketFeed
    from signal_engine import SignalEngine

    feed = MarketFeed()
    engine = SignalEngine(min_confidence=0.60, min_rr=1.5)
    executor = UpsideOnlyExecutor()

    send_telegram("🚀 AgoraPrime Bot ONLINE — UpsideOnly executor starting")

    await executor.launch(headless=True)
    logged_in = await executor.login_google()

    if not logged_in:
        send_telegram("❌ Login failed — manual intervention needed")
        log.error("Could not log in. Run with headless=False to debug.")
        await executor.close()
        return

    send_telegram("✅ Login successful — scanning markets")
    cycle = 0

    while True:
        try:
            cycle += 1
            log.info(f"=== Cycle {cycle} ===")

            # Fetch latest market data
            market_data = feed.fetch_all()

            # Generate signals
            signals = engine.scan_all(market_data)
            log.info(f"Generated {len(signals)} signals")

            # Execute top signals
            if signals:
                await executor.run_cycle(signals)

            # Status report every 10 cycles
            if cycle % 10 == 0:
                send_telegram(
                    f"📊 AgoraPrime Status\n"
                    f"Cycle: {cycle}\n"
                    f"Open trades: {len(executor.open_trades)}\n"
                    f"Signals this scan: {len(signals)}"
                )

            await asyncio.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            log.info("Stopped by user")
            break
        except Exception as e:
            log.error(f"Cycle error: {e}")
            send_telegram(f"⚠️ Bot error cycle {cycle}: {e}")
            await asyncio.sleep(30)

    await executor.close()
    send_telegram("🛑 AgoraPrime Bot OFFLINE")


if __name__ == "__main__":
    asyncio.run(main())
