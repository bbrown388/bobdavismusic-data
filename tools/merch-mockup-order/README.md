# Merch mockup order

Keeps every colour of a product on bobdavismusic.com/merch.html showing its photos in the
same sequence, so switching colour looks like the garment was recoloured rather than the
gallery being reshuffled.

## The problem

Printful stores each colour's mockups in the order it generated them, which follows the
order the mockup styles were ticked in the Printful UI. That order varied per colour, and
Shopify preserves it, so the BD Logo Trucker Cap opened on the flat front shot for twelve
colours and the angled shot for Navy.

Nothing in the Storefront API can fix this from metadata. Three of the four cap photos per
colour are all named `<colour>-front-<hash>` and are all 2000x2000, so filename and
dimensions cannot tell them apart. The only thing that separates them is what they show.

## How it works

1. Fetch the catalogue and group each product's images by colour, matching on filename the
   same way merch.html does (longest match wins, so `Black/ White` beats `Black`).
2. Fingerprint every image with a **colour-invariant edge signature**: 128px greyscale,
   edge filter, `log1p`, resampled to 24x24, z-normalised.
3. Match each colour's photos against a reference colour with an exact Hungarian assignment.
4. Order the styles by the position they already hold for most colours, so the canonical
   order is the majority's existing look and not an aesthetic judgement.
5. Write `merch/image-order.json` in the `bobdavismusic-data` repo as `filename -> position`.

### Why edges and not a plain thumbnail

A luminance thumbnail mostly measures the garment's colour, which is the one thing that
legitimately differs between photos we want to call identical. Edge energy comes from the
silhouette, the embroidery, the mesh panels and any model, none of which change with colour.

Measured on the 12-colour BD Logo Trucker Cap, the correct assignment beat the next-best one by:

| signature | mean margin | worst colour |
|---|---|---|
| luminance | 14% | 5% |
| **edges** | **53%** | **37%** |

## It only acts when it is both needed and sure

Two gates, so a run can only ever improve things:

- **Needed.** A style already sitting at the same position for every colour is left alone.
  The bottle and the twill cap are already consistent and are not touched.
- **Sure.** If the weakest match in a product falls below 15%, the product is skipped and
  reported instead. The bottle is four near-identical studio shots whose edges barely
  differ; guessing there could scramble a gallery that is currently fine.

## Failure behaviour

The map is keyed on image filename. If Printful regenerates mockups the filenames change,
those images drop out of the map, and merch.html falls back to its built-in angle/size/colour
sort. A missing, stale, half-stale or corrupt map all degrade to "Printful's order" rather
than to a broken page, and the fetch is capped at 2.5s so a slow data host cannot hold up
the shop. Re-running with `--write` restores exact ordering.

## It runs itself

`.github/workflows/merch-mockup-order.yml` regenerates the map daily and commits it only
when the order actually changed. Nothing to run by hand, and it does not depend on Bob's PC
being on.

The trigger is a **schedule**, not a push, because the thing that goes stale is Shopify's
image set, which changes when Printful republishes a product. That never coincides with a
commit here, so a push trigger would almost never fire at the right moment. It also runs on
pushes that touch the tool itself, so a bad edit surfaces immediately, and can be started by
hand from the Actions tab.

This repo needs no pull request, so a corrected map is live as soon as GitHub Pages
publishes, usually under a minute.

Optional: set an `NTFY_TOPIC` repository secret and the run pushes to Bob's phone whenever
it actually commits a change. Without the secret the notify step is simply skipped. It fires
only on a real change, so the known twill cap gap below never becomes a daily notification.

## Running it by hand

```bash
python mockup_order.py --report   # what it found, writes nothing
python mockup_order.py --check    # exit 1 if the committed map is stale or drifted
python mockup_order.py --write    # regenerate the map
```

Signatures are cached in `.sigcache/`, so re-runs only download images that are new. CI
restores that cache between runs, which saves roughly 300 image fetches.

## Things only a human can fix

`--report` and `--check` list these. As of the first run:

- **BD Logo Structured Twill Cap** has 6 mockups for Multicam Black, Dark Navy, Black,
  Royal Blue and Red but only 5 for the other six colours, which are missing the sixth
  style. Ordering is still consistent, the gallery is just shorter for those colours.
  Fix by generating the missing mockup in Printful.
