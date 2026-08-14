# SG Car Scout — arcade HUD edition

A single-page app for hunting Singapore used cars: builds SGCarmart searches, reads listing figures you paste in (or capture with one tap), and works out what a car *actually* costs you rather than what the ad claims.

## Telegram bot for the group

The same search-building and true-depreciation logic is also available as a Telegram bot, so a group of friends can share models, saved hunts, calibration, and a shortlist without anyone installing the app. It builds links the same way the app does — **it never scrapes or auto-fetches SGCarmart**; every result is a link a human taps, same as the site's own terms expect.

**Two versions exist, use `telegram-bot/` unless you specifically want the other:**
- **[`telegram-bot/`](telegram-bot/README_BOT.md)** — standalone Python bot, `python bot.py` and you're done. No website, no Netlify, no webhook, no public URL. Runs on your own PC/server or any free host that can keep a Python process alive.
- **[`netlify/functions/`](BOT_SETUP.md)** — the same bot as a Netlify Functions webhook, for if you'd rather it ride along on the same deploy as the PWA below and don't want to keep a process running yourself.

## Put it on your iPhone home screen

**GitHub Pages (free, 5 minutes)**

1. Create a new public repo, e.g. `car-scout`.
2. Upload all nine files to the repo root: `index.html`, `manifest.webmanifest`, `sw.js`, `fx.css`, `fx.js`, `icon-maker.html`, `icon-180.png`, `icon-192.png`, `icon-512.png`.
3. Repo → **Settings → Pages** → Source: *Deploy from a branch* → Branch: `main`, folder `/ (root)` → Save.
4. Wait a minute, then open `https://<your-username>.github.io/car-scout/` in **Safari** on your iPhone.
5. Share button → **Add to Home Screen**.

It opens fullscreen with no browser chrome, has its own icon, and works offline after the first load.

**Netlify alternative:** drag the folder onto app.netlify.com/drop. Instant HTTPS URL, no repo.

Must be **HTTPS** — the service worker and clipboard paste won't run over plain HTTP or from a local file.

## Web-Slinger theme + Full FX (sound, comic bursts)

A 9th theme, **Web-Slinger** — comic-book red/blue, halftone-dot masthead, same AA-checked contrast discipline as the other eight (a colour swap, no licensed art). Pick it from **Theme** at the top of any tab.

Below the theme swatches is a **Full FX** switch. It's off by default — sound shouldn't surprise you the first time you open the app. Turn it on and:

- Tapping a chip, tab, or a primary action (Build searches, Save to shortlist, Save current hunt, Calibrate & save, Download backup) plays a short synthesized sound — a "thwip" for navigation, a punchier "pow" plus a comic burst word (POW! / ZOOM! / BAM!) for the big actions.
- Listing cards and search rows get a bouncier comic "panel drop" entrance instead of the plain fade.
- All of it respects iOS's **Reduce Motion** setting (animations turn off; sound is unaffected, since it isn't motion) and your device's silent switch (standard iOS Safari audio behaviour).

**No audio or image files are shipped.** Every sound in `fx.js` is synthesized live with the Web Audio API — short oscillator sweeps and filtered noise bursts — so there's nothing to host, nothing that can go missing on a redeploy, and nothing under anyone else's copyright. It's inspired by the tone of comic-HUD trackers like Sony's Spidey Tracker promo site, not a clone of it — that site plots live map sightings, which isn't a shape that fits a car-search tool, so the borrow is the *feel* (chunky panels, comic bursts, sound on tap), not its literal map layout.

Both `fx.css` and `fx.js` are additive: if either fails to load for any reason, the rest of the app (search building, shortlist, calculator) works exactly as before.

## Known issue, fixed: PARF filter was letting renewed-COE cars through

If you hunted PARF-only cars and still saw old cars on renewed COE (e.g. a 2008-registered car with "COE till 2029"), that was a real bug, now fixed. The app used to fake the PARF/renewed split by capping "years of COE left" at 10 — but that's not a valid proxy: a renewed-COE car deep into its *second* 10-year term can also show only 2–3 years left. Years-left tells you nothing about whether the COE was ever renewed.

The actual test is **registration age**: an original COE runs exactly 10 years, so a genuine PARF car must be registered within the last 10 years, and a car on a renewed COE must, by definition, be registered *more* than 10 years ago (renewal can't happen until the original term expires). The app now enforces PARF-only / renewed-only hunts with a registration-year bound instead of a COE-years-left cap — see `regYearBound()` and the `regFrom`/`regTo` calibration slots in **Hunt → Calibrate SGCarmart**. Like every other filter, those two param names are guessed until you calibrate them against a real sgcarmart.com URL — until then the Hunt tab shows a red **Unverified filter** warning right above your search queue whenever a single COE-status hunt is active, so you know to double-check reg years on the results before trusting them. The plain "min/max COE years left" fields are still there too, but now they're just that — a literal years-left filter, no longer secretly doing double duty as the PARF switch.

## What changed in this edition

1. **Calibrate SGCarmart, instead of guessing.** SGCarmart has moved its listing URLs off the old `used_cars/listing.php` scheme, and nobody outside Singapore can fetch the site to check what replaced it — it's Cloudflare-gated and geofenced. So the app stopped guessing harder and started asking *you*: **Hunt → Calibrate SGCarmart**. Set one filter on sgcarmart.com from your phone, paste the plain URL and the one-filter URL, and the app diffs the two querystrings to find the real parameter — for price, depreciation, mileage, COE range, the model box, and every vehicle type separately. Each is marked **✓ verified** once you've done it, or **guessed** until you have. This works no matter how SGCarmart's URLs change in the future, because it never hardcodes a guess — it reads the site's actual behaviour off a URL you supply.
2. **Saved Hunts.** Beyond one-off saved links, **Hunt → Saved hunts** snapshots your whole setup — every selected model, every vehicle type, COE status, and all your numbers — under a name (and an optional tag like "Weekend watch"). Tap **Load** next quarter and it's all back, ready to rebuild against whatever's calibrated at the time.
3. **Quick capture bookmarklet.** **Tools → Quick capture** gives you a bookmarklet: tap it on any SGCarmart listing page and it copies the URL plus the page text to your clipboard in one go, so **Add → Read a listing** has everything it needs without manual copy-paste of individual fields.
4. **Arcade HUD look.** Full pixel-console redesign — chunky bordered panels, pixel-font headings, a scanning-radar masthead, comic drop-shadow badges, target-lock corner marks on listing cards. Numbers stay in the clean monospace font throughout, so depreciation figures are still fast to read at a glance. All eight themes (Log Card, Paper, Orchard, Night Drive, OLED Black, Marina, Kopi, Dusk) carry the new chrome.
5. **Custom app icon.** Open `icon-maker.html` in any browser (locally, or once deployed) to generate a cartoony radar/car-badge icon in your theme colour, at all three required sizes, then replace the `icon-*.png` files and redeploy.

## What it does

- **Hunt** — multi-select any number of models and vehicle types, set your numbers, tap **Build searches**. You get one ready-made SGCarmart link per model × type combination, each with every filter applied. Save the whole setup as a named Hunt to rerun later.
- **Add** — paste the details block from any listing (or use the Quick capture bookmarklet); price, depreciation, reg date, mileage, ARF, owners, road tax and dereg value get pulled out automatically, along with the listing URL itself.
- **Shortlist** — every car with a true-depreciation figure, a paper-value bar, and automatic red flags.
- **Compare** — weight what you care about, then read the side-by-side table.
- **Tools** — standalone rebate calculator, quick-capture bookmarklet, plus backup and restore.

## Calibrating SGCarmart — the one thing to do first

Before your first real hunt, spend ~10 minutes calibrating. For each of: model search, max price, max dep/yr, max mileage, min COE left, max COE left, and every vehicle type you care about:

1. On sgcarmart.com (in Singapore, or via VPN), set **that one filter only**, nothing else, and copy the resulting URL.
2. In the app, open **Hunt → Calibrate SGCarmart**, pick that field from the dropdown, and paste:
   - the **plain URL** (sgcarmart.com with no filters set) — only needed once, it's reused for every field.
   - the **one-filter URL** you just copied.
   - for price/dep/mileage, also type in **the value you actually entered** on the site, so the app can work out whether it wants thousands or raw dollars.
3. Tap **Detect & save**. The field turns ✓ verified and every search link from then on uses the real parameter.

Fields you haven't calibrated still work — they fall back to best-effort defaults, same as before — but they're labelled "guessed" everywhere in the app so you know which links to double-check before trusting them.

## The one number that matters

Dealers quote depreciation over a period that flatters the car. This app recomputes:

```
true depreciation = (asking price − paper value at COE expiry) ÷ years of COE you actually get
```

Worked from real figures: a Cerato asking $52,800 with a listed $9,980/yr but only 2.6 years of COE left is really costing **$16,727 a year**. That gap is the whole reason this app exists.

## Rebate rules built in

- ARF rebate: 75% under 5 years, stepping down 5 points a year to 50% at 9–10 years, nothing past 10 — for cars first registered **before 13 Feb 2026**. Every used car on the market now is on this schedule.
- Cars registered **from 13 Feb 2026** get the revised 30%-down-to-5% schedule.
- Rebate caps: none before 15 Feb 2023, $60,000 from then, $30,000 from Feb 2026.
- COE rebate: `QP × months remaining ÷ 120`. Five-year renewals earn no rebate at all.

These are estimates from LTA's published schedule and were checked against LTA's own worked example ($67,000 ARF at 9 years → $33,500). **Before you pay anything, confirm the specific car on OneMotoring → Enquire PARF/COE Rebate.** That figure is the one that binds.

## Why searches are listed instead of merged

Selecting truck + sedan + SUV produces three separate searches per model rather than one combined URL. SGCarmart's parameter for combining several vehicle types in a single query hasn't been observed by anyone who's calibrated this app yet, so it doesn't guess — a guessed parameter fails silently and shows you the wrong cars, which is worse than an extra tap. If you calibrate it yourself and confirm a combining parameter exists, this is a small code change — ask for it.

## Two things worth knowing

**Listings can't be pulled in automatically.** SGCarmart sits behind Cloudflare and is geofenced to Singapore, and it sends no header permitting other sites to read its pages. No amount of client-side code gets around that — a scraper would need its own server plus a Singapore IP, and would be against their terms. So the app builds the search and hands you off, and — since this edition — lets you calibrate those searches against the real, current site instead of a frozen guess.

**Storage is per-browser.** Everything lives in Safari's local storage for that one URL, including your calibration and saved hunts. Clearing website data wipes it all. Use **Tools → Download backup** now and then; restore merges rather than overwrites, and brings your calibration and hunts back too.

## Units: price and depreciation

SGCarmart's *old* price and depreciation filters took whole thousands — `PR2=90` meant $90,000. The app still defaults to that divisor, but calibrating price/dep against a real URL overrides it automatically if the new scheme takes raw dollars instead. Expand **What gets sent to SGCarmart** under the numbers to see the live conversion for every field. Because a thousands-based filter only accepts whole thousands, a figure like $13,500 is rounded **up** to $14,000 — widening rather than narrowing, so you never lose a car you asked for.
