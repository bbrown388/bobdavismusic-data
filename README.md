# bobdavismusic-data

Machine-written data for [bobdavismusic.com](https://bobdavismusic.com).

| File | Written by | Read by |
|------|-----------|---------|
| `data/followers.json` | `follower-updater/update-followers.mjs` (daily) | the site's follower counter, fetched at runtime |
| `data/followers-history.csv` | same job, same commit | nothing yet; a chart-ready time series |

## Why this repo exists

The website repo requires a pull request for `main`. The daily follower job is
automation, so every run failed that rule. Rather than punch a hole in the rule
for a bot, the data it writes lives here instead, in a repo with no protection.
The website never receives an automated push at all.

Served over GitHub Pages, which sends `Access-Control-Allow-Origin: *`, so the
site can fetch it cross-origin:

```
https://bbrown388.github.io/bobdavismusic-data/data/followers.json
```

Nothing here is hand-edited. If a number looks wrong, fix the scraper.
