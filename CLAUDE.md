# Krei Deal Pipeline

Internal deal-sourcing tool for Krei Group LLC. Scrapes CRE listing sites daily and emails a digest to the principal.

## Project Goal (v1)
Daily email to dad with new CRE listings matching the buy box below. Nothing more until v1 ships.

## Buy Box
- **Geography:** Palm Beach County (Tier 1), South FL (Tier 2), rest of FL (Tier 3)
- **Asset types:** NNN retail, multi-tenant retail, medical office
- **Price:** $5M–$20M
- **Cap rate:** 6% floor, 7%+ priority

## Sources to Scrape
Crexi, LoopNet, CityFeet, Showcase

## Stack
- Python 3.14
- Playwright (headless browser, stealth mode)
- Anthropic SDK (Claude extracts listing data from raw HTML)
- SQLite (deduplicate listings across runs)
- Resend (send daily email digest)
- Railway (deploy and schedule)

## Rules
- Ship ugly every week — no polish until v1 is live
- No scope creep until v1 ships
- No UAE deals
- No branding work

## User Context
- Builder is 18, learning to code as he builds this
- Explain all code changes inline so he understands what's happening
- Keep explanations tied to what's actually being written, not generic tutorials
