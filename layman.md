# Scrapo, in plain English

Scrapo is a tool for developers that **reads websites for you and hands back clean, usable data** — either as tidy text, or as structured fields (a name, a price, a date). It's built to be reliable, cheap to run at scale, and easy for AI assistants to use.

If you've ever copied information off a web page by hand, or wished a program could "just go read that site and tell me what's on it," that's the job Scrapo does — automatically, repeatedly, and in a way you can check up on later.

---

## What it actually does

- **Turns messy web pages into clean text.** A typical web page is full of menus, ads, popups, and code. Scrapo strips all that out and gives you just the content, as Markdown (simple, readable text).
- **Pulls out specific facts.** Tell it "I want the product name and price from these pages" and it returns exactly those fields, in the same shape, for every page.
- **Handles modern, complicated websites.** Some sites only show their content after running code in your browser; some actively try to block automated visitors. Scrapo starts with the fast, simple approach and automatically escalates — to a real (invisible) browser, then a "stealth" browser, then an AI that can click around — only when it actually needs to.
- **Learns once, then works for free.** The first time it extracts data from a site, it can use an AI model to figure out where the information lives. It remembers that "recipe," so future runs don't need the AI at all — which keeps costs near zero. If the site's layout changes, it notices, re-learns, and keeps going.
- **Remembers everything it fetched.** Every page Scrapo downloads is saved. You can re-run your extraction later without touching the internet, or compare two points in time to see exactly what changed ("the price went from $42 to $45").
- **Has built-in guardrails.** It can respect a site's `robots.txt` rules, flag personal information it runs into (emails, phone numbers, things that look like credit-card or social-security numbers), restrict itself to certain countries, and keep an append-only log of everything it did.
- **Plugs into the tools you already use.** It works from Python code, from a command line, and as an "MCP server" — meaning AI assistants like Claude can use Scrapo directly as a capability. There's also a small built-in web page (`scrapo serve`) for trying it out in a browser.
- **Runs entirely on your own machine or servers.** There's no Scrapo cloud service you have to sign up for or route your data through. You own it end to end.

---

## What's possible

- Get clean, readable text out of most public web pages.
- Get structured data (the specific fields you define) out of pages, reliably and repeatably.
- Scrape JavaScript-heavy pages that don't work with a plain download.
- Get past light anti-bot defenses on your own; get past tougher ones by plugging in a commercial proxy service (Bright Data, Oxylabs, Scrapfly, and Zyte are supported out of the box).
- Crawl a whole site — follow links automatically, with limits on depth and page count, skipping duplicates.
- Re-run an old extraction against the saved copy of a page, with zero new network requests.
- Compare two saved runs and see, field by field, what changed.
- Use it from Python, from the terminal, or wired into an AI agent via MCP.
- Choose your AI provider (Anthropic, OpenAI, Google Gemini) — or run with no AI at all once the selectors are cached.
- Keep an audit trail and basic compliance controls around the scraping you do.

---

## What's *not* possible (or not yet)

- **It's not a no-code, point-and-click product.** You need to write a little Python or use the command line. The built-in web page is intentionally minimal — it's for trying things, not a polished app.
- **It can't magically beat every site's defenses.** Aggressive bot protection and CAPTCHAs are genuinely hard. A proxy provider helps a lot, but nothing is guaranteed. The most advanced mode — an AI that drives a browser through logins and CAPTCHAs — exists but is lightweight and experimental today.
- **It won't log into sites for you by default.** You can supply credentials or a saved login session, but automated login flows are still experimental.
- **There's no hosted dashboard or scheduler.** Scrapo doesn't run your jobs in the cloud, send alerts, or give you a web console to manage everything. You run and schedule it yourself.
- **It's alpha software.** The core works and is stable, but expect rough edges. Some pieces (cloud snapshot storage, advanced action caching, a hosted control plane) are planned, not built.
- **AI extraction costs money.** The first run on a new site (or after a layout change) calls a paid AI model. Scrapo is designed to minimize this — most runs use the free cached recipe — but it isn't literally free.
- **It's not legal advice or a compliance guarantee.** The robots-rules, personal-data flagging, geo limits, and audit log are *tools* to help you scrape responsibly. You're still responsible for following each site's terms and the law. (Note: robots-rule enforcement is off by default — you have to turn it on.)
- **It's Python-only.** No JavaScript, Java, Go, etc. versions. Requires Python 3.11 or newer.

---

## The one thing that makes it different

Lots of tools can scrape a page. Very few keep a **permanent, replayable record** of every fetch — so that months later you can show what a page said, re-extract from it, and pinpoint exactly which field changed and whether it changed because the website changed or because *your AI model* changed. That auditability is Scrapo's headline feature.

---

## Where to go next

- The [README](README.md) has the technical quickstart, the full feature list, and configuration.
- `pip install scrapo`, then `scrapo serve` to poke at it in a browser, or `scrapo scrape https://example.com/` from the terminal.
