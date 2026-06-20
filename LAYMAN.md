# Scrapo, in plain English

Scrapo is a tool for developers that **reads websites for you and hands back clean, usable data**, either as tidy text or as structured fields (a name, a price, a date). It is built to be reliable, cheap to run at scale, and easy for AI assistants to use.

If you have ever copied information off a web page by hand, or wished a program could "just go read that site and tell me what is on it," that is the job Scrapo does: automatically, repeatedly, and in a way you can check up on later.

---

## What it actually does

- **Turns messy web pages into clean text.** A typical web page is full of menus, ads, popups, and code. Scrapo strips all that out and gives you just the content, as Markdown (simple, readable text). It also handles things that are not web pages: JSON feeds, RSS/Atom feeds, and PDFs all come back in a usable form rather than as garbage.
- **Has a cleaner "reading mode."** You can ask Scrapo to throw away all the website "furniture" — the navigation menus, sidebars, footers, and ads — and keep just the main article text, the way a browser's reader view does. The result reads like a clean document, which is especially good when you are going to feed it to an AI. This is optional: you turn it on when you want it, and it stays off otherwise.
- **Pulls out specific facts.** Tell it "I want the product name and price from these pages" and it returns exactly those fields, in the same shape, for every page.
- **Can "map" a website before reading it.** Point Scrapo at a site and it will quickly give you a list of all the pages (web addresses) it can find, *without* downloading the content of each one. Think of it as getting the table of contents first, so you can decide what is actually worth reading before you spend time (or money) reading it.
- **Can take a list and fetch it all at once.** If you already know the exact pages you want, hand Scrapo the whole list and it goes and gets them together, in parallel, instead of one slow page at a time. If one address fails, the rest still come back fine — one bad link does not sink the batch. Your results come back in the same order you asked for them.
- **Can follow simple click-through steps you write out.** Some content only appears after you click a button, type into a box, or scroll down. You can give Scrapo a short, plain recipe of steps — "go here, click this, type that, wait, scroll, take a picture" — and it follows them in order to reach the content. Because these are exact steps you spelled out, it does not need an AI to figure each one out, so it is fast, predictable, and free of AI cost.
- **Handles modern, complicated websites.** Some sites only show their content after running code in your browser; some actively try to block automated visitors. Scrapo starts with the fast, simple approach and automatically escalates (to a real, invisible browser, then a "stealth" browser, then an AI that can click around) only when it actually needs to.
- **Uses a site's official API when there is one.** A few popular sites (Wikipedia and its sister projects) fight scrapers hard but publish the very same content through a clean, free public API. Scrapo recognises those addresses and quietly fetches the API instead of the page — so a Wikipedia link just works, with no "prove you're human" wall and no wasted effort fighting one.
- **Often gets the data for free, with no AI at all.** Many sites already publish their key facts (a product's name and price, an article's author and date) in a hidden, machine-readable tag meant for search engines. Scrapo reads that first, so for those pages it returns the structured fields instantly, for free, with no AI and nothing that can break when the page's visible layout shifts around.
- **Learns once, then works for free.** When a page does not publish its data that way, the first extraction can use an AI model to figure out where the information lives. It remembers that "recipe," so future runs do not need the AI at all, which keeps costs near zero. If the site's layout changes, it notices, re-learns, and keeps going; a recipe that keeps failing is thrown away so the next run starts fresh.
- **Can keep loading "more" until everything is there.** Plenty of pages only reveal their full content as you scroll (an endless feed) or as you keep clicking a "Load more" button. Scrapo can do that for you: keep scrolling, or keep clicking, until nothing new appears, with a safety cap so an endless feed cannot run away.
- **Saves your results straight to a file.** After a batch or a crawl, Scrapo can write everything out as a spreadsheet (CSV) or a line-per-page data file (JSONL), so the results drop straight into another tool or pipeline.
- **Works from plain, ordinary Python too.** Scrapo's engine is built for advanced "asynchronous" code, but you do not have to know any of that: there are plain, simple versions of every main command you can call from a normal script or a notebook.
- **Remembers everything it fetched.** Every page Scrapo downloads is saved. You can re-run your extraction later without touching the internet, or compare two points in time to see exactly what changed ("the price went from $42 to $45").
- **Stays out of trouble by default.** It refuses to fetch internal or private addresses (the kind an attacker would aim it at), retries flaky requests instead of giving up, and can respect a site's `robots.txt` rules, flag personal information it runs into (emails, phone numbers, things that look like credit-card or social-security numbers), restrict itself to certain countries, and keep an append-only log of everything it did.
- **Got more reliable and safer under the hood.** A few quieter improvements in this release: it is better at noticing when a site is actually blocking it (the "are you a robot?" challenges and "verify you're human" pop-ups) so it can react instead of silently returning nothing; it now genuinely obeys the spending limits you set, so an AI-powered job cannot quietly run up a surprise bill; it is faster on big jobs because it reuses its connections instead of starting from scratch each page; and it is harder to trick into visiting a private or internal address — including the sneaky case where a public page tries to bounce ("redirect") it somewhere it should not go.
- **Plugs into the tools you already use.** It works from Python code, from a command line, and as an "MCP server," which means AI assistants like Claude can use Scrapo directly as a capability. There is also a small built-in web page (`scrapo serve`) for trying it out in a browser.
- **Runs entirely on your own machine or servers.** There is no Scrapo cloud service you sign up for or route your data through. It is a self-hosted library: *you* run it, and you bring your own pieces — your own proxies (the relay services that help reach stubborn sites) and your own AI keys. The upside is you own it end to end and nothing passes through someone else's hands. The trade-off is that it is not a hosted "just works" product; there is setup, and you supply the parts.

---

## What's possible

- Get clean, readable text out of most public web pages — and, if you turn on reading mode, an even cleaner "main article only" version with the menus and ads removed.
- Get structured data (the specific fields you define) out of pages, reliably and repeatably, including lists (every product on a listing page, every row of a table) when you describe them as a list of records.
- Get a quick list of all the pages on a site (a "map") before downloading any of them, so you can pick what is worth fetching.
- Hand it a fixed list of web addresses and have them all fetched together, with any failures isolated so they do not take down the rest.
- Reach content hidden behind a button, a scroll, or a simple form by writing out the click-through steps yourself — no AI needed for that part.
- Scrape JavaScript-heavy pages that do not work with a plain download.
- Get past light anti-bot defenses on your own; get past tougher ones by plugging in a commercial proxy service (Bright Data, Oxylabs, Scrapfly, and Zyte are supported out of the box), or hand it your own list of proxies and let it rotate through them, automatically benching any one that starts getting blocked and bringing it back later.
- Crawl a whole site: follow links automatically, with limits on depth and page count, skipping duplicates (and get each page handed back to you as it finishes, rather than waiting for the whole crawl).
- Re-run an old extraction against the saved copy of a page, with zero new network requests.
- Compare two saved runs and see, field by field, what changed.
- Watch a page: re-check it later and get told whether it changed and exactly which fields moved. If the page hasn't changed, the re-check is almost free (no re-download, no AI call): it asks the server "has this changed since I last looked?" first. You can also run Scrapo's own scheduler, which keeps a saved list of watches, checks each on its own timetable, and pings a web address you choose whenever one changes.
- Pull the structured fields a page already publishes for search engines, instantly and for free, before any AI is involved.
- Save a whole batch or crawl straight to a CSV spreadsheet or a JSONL data file.
- Use it from Python, from the terminal, or wired into an AI agent via MCP.
- Choose any AI provider — Anthropic, Google Gemini, OpenAI, DeepSeek, OpenRouter, a local model through Ollama, or any other OpenAI-compatible service — or run with no AI at all once the selectors are cached. If you don't pick one, Scrapo just uses whichever provider's key you have set.
- Put a real ceiling on cost: cap how many AI calls or how many dollars a job is allowed to spend, and Scrapo now actually stops once the limit is reached (rather than letting it slip past) — the cap holds even across a big crawl or a whole batch.
- Keep an audit trail and basic compliance controls around the scraping you do.

---

## What's *not* possible (or not yet)

- **It is not a no-code, point-and-click product.** You need to write a little Python or use the command line. The built-in web page is intentionally minimal; it is for trying things, not a polished app.
- **It cannot magically beat every site's defenses.** Aggressive bot protection and CAPTCHAs are genuinely hard. Scrapo is now noticeably better at *recognising* when it has been blocked, but recognising a wall is not the same as climbing it. For the toughest sites you still have to supply good proxies of your own — Scrapo will use them well, but it cannot conjure them, and nothing is guaranteed. The most advanced mode (an AI that drives a browser through logins and CAPTCHAs) exists but is lightweight and experimental today, and ships without a default driver.
- **The guided click-through steps are not the same as "figure it out for me."** The new step-by-step recipes (click, type, scroll, wait) are reliable precisely because *you* spell out each step. If you instead want Scrapo to work out the steps on its own — to reason its way through a goal — that is the separate, AI-driven "agent" mode below, and it is far less mature.
- **Logging into sites is experimental.** There is a built-in "agent" mode where an AI clicks through a page toward a goal (logins, simple forms), but it is new and unproven; for anything important, either write out the steps yourself (above), or supply credentials or a saved login session. (Once the agent has done a task on a site, it remembers the steps and replays them next time without calling the AI, and quietly falls back to the AI if the page has changed.)
- **There is no hosted cloud dashboard.** Scrapo now ships a self-hosted scheduler you can run yourself: it keeps a saved list of pages to watch, re-checks each one on its own schedule, and sends an alert (an automatic web request to an address you choose) the moment something changes. What is still missing is the turnkey cloud version: a website you log into, with accounts and a visual dashboard, that runs all of this for you. That hosted product would be built on top of this engine, not part of the library.
- **It is a self-hosted library, not a hosted service.** You run it yourself and bring your own proxies and AI keys. There is no "sign up and it just works" version — and that is on purpose, not an oversight. If you want a turnkey cloud product that handles proxies, scheduling, and a dashboard for you, Scrapo is not that.
- **It is beta software.** The core works and is stable, but expect rough edges. The one piece still left out is the fully hosted "sign up and we run and monitor everything for you in the cloud" service, with a web dashboard and accounts; the self-hosted engine for scheduling and alerts now ships.
- **AI extraction costs money.** The first run on a new site (or after a layout change) calls a paid AI model. Scrapo is designed to minimize this (most runs use the free cached recipe) but it is not literally free.
- **It is not legal advice or a compliance guarantee.** The robots rules, personal-data flagging, geo limits, and audit log are *tools* to help you scrape responsibly. You are still responsible for following each site's terms and the law. (Note: robots-rule enforcement is off by default; you have to turn it on.)
- **It is Python-only.** No JavaScript, Java, Go, etc. versions. Requires Python 3.11 or newer.

---

## The one thing that makes it different

Lots of tools can scrape a page. Very few keep a **permanent, replayable record** of every fetch, so that months later you can show what a page said, re-extract from it, and pinpoint exactly which field changed and whether it changed because the website changed or because *your AI model* changed. That auditability is Scrapo's headline feature.

---

## Where to go next

- The [README](README.md) has the technical quickstart, the full feature list, and configuration.
- `pip install scrapo`, then `scrapo serve` to poke at it in a browser, or `scrapo scrape https://example.com/` from the terminal.
