"""
Krei Pipeline — Crexi scraper, v0.2

What's new vs v0.1:
  - Pagination: pulls multiple pages of results, not just the first 100
  - Cap rate filter: drops listings below 6%
  - SQLite: stores every listing we've seen; each run only reports NEW ones

The pagination trick: after the first page loads via Playwright (which gets us
past Cloudflare), we use page.evaluate() to run JavaScript *inside* the browser
that calls fetch() for subsequent pages. Because it runs inside the real browser,
it inherits the session cookies and headers — Cloudflare can't tell the difference.
"""

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from telegram_sender import send_listings

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
CREXI_BASE_URL = "https://www.crexi.com/properties"
DB_PATH        = os.getenv("DB_PATH", "listings.db")

BUY_BOX = {
    "county":    "palm beach",   # case-insensitive match
    "price_min": 5_000_000,
    "price_max": 20_000_000,
    "cap_min":   6.0,            # percent — drop anything below this
}

# These go straight into Crexi's search API body.
# We pull 5 pages × 100 listings = 500 FL results, then filter locally.
# Why 500? Palm Beach County is ~2–3% of FL listings, so 500 gives us ~10–15 matches.
PAGE_SIZE  = 100
MAX_PAGES  = 5

BASE_SEARCH = {
    "count":          PAGE_SIZE,
    "offset":         0,
    "sortOrder":      "rank",
    "sortDirection":  "Descending",
    "types":          ["Retail", "Office"],
    "priceMin":       BUY_BOX["price_min"],
    "priceMax":       BUY_BOX["price_max"],
    "states":         ["FL"],
    "includeUnpriced": False,
}


# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------
def init_db():
    """
    Creates the SQLite database file and the listings table if they don't exist.
    SQLite is a file-based database — no server needed, just a .db file on disk.
    The PRIMARY KEY on crexi_id means inserting a duplicate ID silently does nothing.
    """
    # Create the directory if it doesn't exist (e.g. /data on Railway before
    # the Volume is mounted, or a local path that hasn't been created yet).
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            crexi_id   TEXT PRIMARY KEY,
            name       TEXT,
            price      TEXT,
            cap_rate   TEXT,
            types      TEXT,
            address    TEXT,
            city       TEXT,
            county     TEXT,
            state      TEXT,
            zip        TEXT,
            size_sqft  TEXT,
            url        TEXT,
            source     TEXT DEFAULT 'crexi',
            first_seen TEXT
        )
    """)
    conn.commit()
    conn.close()


def split_new_vs_seen(listings: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Checks each listing's crexi_id against the database.
    Returns (new_listings, already_seen_listings).
    """
    conn = sqlite3.connect(DB_PATH)
    new, seen = [], []
    for listing in listings:
        exists = conn.execute(
            "SELECT 1 FROM listings WHERE crexi_id = ?", (listing["crexi_id"],)
        ).fetchone()
        (seen if exists else new).append(listing)
    conn.close()
    return new, seen


def save_to_db(listings: list[dict]):
    """Inserts new listings into the database, skipping any duplicates."""
    if not listings:
        return
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """
        INSERT OR IGNORE INTO listings
          (crexi_id, name, price, cap_rate, types, address, city, county,
           state, zip, size_sqft, url, source, first_seen)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'crexi',?)
        """,
        [
            (
                l["crexi_id"], l["name"], l["price"], l["cap_rate"],
                json.dumps(l["property_types"]),
                l["address"], l["city"], l["county"], l["state"],
                l["zip"], l["size_sqft"], l["url"], now,
            )
            for l in listings
        ],
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# SCRAPER
# ---------------------------------------------------------------------------
async def fetch_all_pages() -> list[dict]:
    """
    Opens Chrome, intercepts the first search API call to inject our filters,
    then uses page.evaluate() to fetch additional pages from inside the browser.

    page.evaluate() runs JavaScript directly in the browser tab. That JS uses
    the browser's built-in fetch() to make API calls — with all the real browser
    cookies and headers already attached. Cloudflare can't distinguish this from
    a real user clicking "next page."
    """
    print("[browser] Launching Chrome...")
    all_raw: list[dict] = []
    session_ready = asyncio.Event()  # signals when the browser session is live

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        # --- Page 1: intercept the browser's own API call ---
        page1_data: list[dict] = []

        async def intercept_first_call(route):
            if route.request.method != "POST":
                await route.continue_()
                return

            try:
                original = json.loads(route.request.post_data or "{}")
            except Exception:
                original = {}

            body = {**original, **BASE_SEARCH, "offset": 0}
            print(f"[page 1] Intercepting first API call, injecting filters...")

            await route.continue_(
                method="POST",
                post_data=json.dumps(body),
                headers={**route.request.headers, "content-type": "application/json"},
            )

        async def on_response(response):
            if "api.crexi.com/assets/search" in response.url:
                try:
                    data = await response.json()
                    items = data.get("data", [])
                    total = data.get("totalCount", "?")
                    print(f"[page 1] Got {len(items)} listings (total in FL: {total:,})")
                    page1_data.extend(items)
                    session_ready.set()  # browser session is now live, safe to fetch more
                except Exception as e:
                    print(f"[page 1] Parse error: {e}")
                    session_ready.set()

        await page.route("**/api.crexi.com/assets/search", intercept_first_call)
        page.on("response", on_response)

        try:
            await page.goto(CREXI_BASE_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception:
            pass

        # Wait for page 1 to come back (up to 20s)
        try:
            await asyncio.wait_for(session_ready.wait(), timeout=20)
        except asyncio.TimeoutError:
            print("[browser] Timed out waiting for page 1")

        # Remove both the route interceptor AND the response listener before
        # fetching more pages — otherwise they'd fire again for each evaluate() call.
        await page.unroute("**/api.crexi.com/assets/search")
        page.remove_listener("response", on_response)

        all_raw.extend(page1_data)

        # --- Pages 2–N: run fetch() inside the browser ---
        # Now that the browser has a valid Cloudflare-verified session,
        # we inject JavaScript to call the Crexi API for the remaining pages.
        for page_num in range(2, MAX_PAGES + 1):
            offset = (page_num - 1) * PAGE_SIZE
            body = {**BASE_SEARCH, "offset": offset}
            print(f"[page {page_num}] Fetching offset={offset}...")

            try:
                # page.evaluate() runs this JS inside the real Chrome tab.
                # The `body` Python dict is passed as the argument to the JS function.
                result = await page.evaluate(
                    """async (body) => {
                        const res = await fetch('https://api.crexi.com/assets/search', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(body),
                        });
                        return await res.json();
                    }""",
                    body,
                )
                items = result.get("data", []) if isinstance(result, dict) else []
                print(f"[page {page_num}] Got {len(items)} listings")
                all_raw.extend(items)
                await asyncio.sleep(0.5)  # be polite, don't hammer the API
            except Exception as e:
                print(f"[page {page_num}] Error: {e}")
                break

        await browser.close()

    print(f"\n[scraper] Total raw FL listings fetched: {len(all_raw)}")
    return all_raw


# ---------------------------------------------------------------------------
# NORMALIZE + FILTER
# ---------------------------------------------------------------------------
def normalize_and_filter(raw: list[dict]) -> list[dict]:
    """
    Converts Crexi's field names into our schema, then applies:
      - Palm Beach County filter
      - Price $5M–$20M (double-checks, API isn't always strict)
      - Cap rate >= 6%
    """
    results = []
    for item in raw:
        loc      = item.get("locations", [{}])[0]
        county   = loc.get("county", "") or ""
        price    = item.get("askingPrice")
        cap      = item.get("capRate")        # float like 6.57, or None
        slug     = item.get("urlSlug", "")
        asset_id = str(item.get("id", ""))

        # --- Buy box filters ---
        if BUY_BOX["county"] not in county.lower():
            continue
        if not price or not (BUY_BOX["price_min"] <= price <= BUY_BOX["price_max"]):
            continue
        # Drop listings where we KNOW the cap rate is below the floor.
        # If cap rate isn't published, keep the listing — broker may not have listed it.
        if cap is not None and cap < BUY_BOX["cap_min"]:
            continue

        results.append({
            "crexi_id":       asset_id,
            "name":           item.get("name"),
            "price":          f"${price:,.0f}",
            "cap_rate":       f"{cap:.2f}%" if cap else "not listed",
            "property_types": item.get("types", []),
            "address":        loc.get("fullAddress"),
            "city":           loc.get("city"),
            "county":         county,
            "state":          loc.get("state", {}).get("code"),
            "zip":            loc.get("zip"),
            "size_sqft":      f"{item['squareFootage']:,}" if item.get("squareFootage") else None,
            "url": (
                f"https://www.crexi.com/properties/{asset_id}/{slug}"
                if slug else None
            ),
            "source": "crexi",
        })

    # Debug: how many Palm Beach listings existed before cap rate cut?
    palm_any_cap = [
        item for item in raw
        if BUY_BOX["county"] in (((item.get("locations") or [{}])[0]).get("county") or "").lower()
        and item.get("askingPrice")
        and BUY_BOX["price_min"] <= item["askingPrice"] <= BUY_BOX["price_max"]
    ]
    print(f"[debug]  Palm Beach $5M–$20M (any cap rate): {len(palm_any_cap)}")
    for p in palm_any_cap:
        print(f"         {p.get('name')} | cap={p.get('capRate')} | ${p.get('askingPrice'):,.0f}")

    print(f"[filter] After buy box (Palm Beach | $5M–$20M | ≥6% cap): {len(results)} listings")
    return results


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
async def main():
    # Set up the database on first run (no-op if it already exists)
    init_db()

    # Scrape
    raw = await fetch_all_pages()
    if not raw:
        print("[error] No data returned from Crexi.")
        return

    # Apply buy box filters
    listings = normalize_and_filter(raw)

    # Split into new vs already seen
    new_listings, seen_listings = split_new_vs_seen(listings)

    # Save the new ones to the database
    save_to_db(new_listings)

    # Report to terminal
    print(f"\n{'='*60}")
    print(f"NEW listings this run: {len(new_listings)}")
    print(f"Already seen:          {len(seen_listings)}")
    print(f"{'='*60}\n")

    if new_listings:
        print(json.dumps(new_listings, indent=2))
        # Send to Telegram — dedup already handled above (only new_listings here)
        send_listings(new_listings)
    else:
        print("No new listings since last run.")


if __name__ == "__main__":
    asyncio.run(main())
