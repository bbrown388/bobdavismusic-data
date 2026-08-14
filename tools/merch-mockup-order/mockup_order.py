#!/usr/bin/env python3
"""
Canonical mockup ordering for bobdavismusic.com/merch.html

THE PROBLEM
Printful generates one set of mockups per garment colour, and Shopify stores them in
generation order. Generation order follows the order the mockup styles were ticked in
the Printful UI, and that varied per colour. So "BD Logo Trucker Cap" in Navy shows
flat / angled / man / woman while Black-White-Black shows flat / woman / man / angled.
Same photos, different sequence, which reads as a glitch when you switch colours.

Filenames and image dimensions cannot fix this: two different shots of the same cap are
the same size and their filenames differ only by a content hash.

THE FIX
Identify each photo by what it actually IS, using a colour-invariant fingerprint, match
the styles across colours, and emit a canonical position per image. merch.html reads the
resulting map as the primary sort key.

WHY AN EDGE FINGERPRINT
A plain luminance thumbnail mostly measures the garment colour, which is the one thing
that legitimately differs between the images we want to call identical. Edge energy comes
from the silhouette, the embroidery, the mesh panels, the background seam and any model,
none of which change with colour. Measured on the 12-colour BD Logo Trucker Cap, the
correct assignment beat the next-best one by 53% on average (worst colour 37%) with edges,
versus 14% average and 5% worst with luminance. See --report for the live numbers.

SELF-HEALING
The map is keyed on image filename. If Printful regenerates mockups the filenames change,
those images fall out of the map, and merch.html silently reverts to its built-in
angle/size/colour sort. Nothing breaks; the order just goes back to being Printful's.
Re-running this tool regenerates the map and restores exact ordering.

USAGE
  python mockup_order.py --report          inspect what it found, write nothing
  python mockup_order.py --check           exit 1 if the committed map is stale or drifted
  python mockup_order.py --write           regenerate the map in the data repo
"""

import argparse
import hashlib
import io
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from collections import defaultdict

import numpy as np
from PIL import Image, ImageFilter

SHOP_DOMAIN = "6anqxb-rt.myshopify.com"
STOREFRONT_TOKEN = "2a7e0382b6e5915658809f9549bb28a7"
GQL = f"https://{SHOP_DOMAIN}/api/2024-01/graphql.json"

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".sigcache")
OUT_PATH = os.path.normpath(os.path.join(HERE, "..", "..", "merch", "image-order.json"))

# A local margin below this means the fingerprint could not confidently tell two styles
# apart, so the pairing is reported for human eyes instead of being trusted silently.
MARGIN_FLOOR = 0.15

CATALOG_QUERY = """{
  products(first: 40) {
    edges { node {
      title
      options { name values }
      images(first: 60) { edges { node { url width height } } }
    } }
  }
}"""


# --------------------------------------------------------------------------- catalog

def read_url(req, tries=4):
    """One run makes ~300 requests unattended on a schedule, so a single reset connection
    or blip must not fail the job. Exponential backoff, then give up honestly."""
    for n in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception:
            if n == tries - 1:
                raise
            time.sleep(2 ** n)


def fetch_catalog():
    req = urllib.request.Request(
        GQL,
        data=json.dumps({"query": CATALOG_QUERY}).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Storefront-Access-Token": STOREFRONT_TOKEN,
        },
    )
    payload = json.loads(read_url(req))
    if "errors" in payload:
        raise SystemExit(f"Storefront API error: {payload['errors']}")
    return [e["node"] for e in payload["data"]["products"]["edges"]]


def slug(s):
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", s.lower()))


def filename(url):
    return url.split("/")[-1].split("?")[0]


def colour_of(url, values):
    """Printful names each mockup after its variant, which is the only photo-to-colour
    link the Storefront API exposes. Longest match wins so 'Black/ White' beats 'Black'."""
    f = filename(url).lower()
    hits = [v for v in values if slug(v) and re.search(r"(^|-)%s(-|\.|$)" % re.escape(slug(v)), f)]
    return max(hits, key=lambda v: len(slug(v))) if hits else None


# ----------------------------------------------------------------------- fingerprint

def signature(url):
    """Colour-invariant fingerprint: edge energy on a 24x24 grid, z-normalised.

    log1p compresses the huge dynamic range of edge magnitude so a single hard silhouette
    edge cannot dominate the vector, and z-normalising removes overall contrast, which is
    what still varies a little between a white cap and a black one."""
    key = hashlib.sha1(url.encode()).hexdigest()
    cached = os.path.join(CACHE_DIR, key + ".npy")
    if os.path.exists(cached):
        return np.load(cached)

    sized = url + ("&" if "?" in url else "?") + "width=128"
    req = urllib.request.Request(sized, headers={"User-Agent": "mockup-order/1.0"})
    im = Image.open(io.BytesIO(read_url(req))).convert("L").filter(ImageFilter.FIND_EDGES)
    a = np.asarray(im.resize((24, 24), Image.LANCZOS), dtype=float)
    a = np.log1p(a)
    v = ((a - a.mean()) / (a.std() + 1e-6)).ravel()

    os.makedirs(CACHE_DIR, exist_ok=True)
    np.save(cached, v)
    return v


# ------------------------------------------------------------------------ assignment

def hungarian(cost):
    """Exact minimum-cost assignment for a rows x cols matrix, rows <= cols.
    Standard O(n^2 m) shortest-augmenting-path formulation."""
    n, m = len(cost), len(cost[0])
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, -1
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    out = [-1] * n
    for j in range(1, m + 1):
        if p[j] > 0:
            out[p[j] - 1] = j - 1
    return out


# --------------------------------------------------------------------------- analysis

def analyse_product(node, verbose=False):
    """Returns (order_map, issues) where order_map is {filename: canonical_index}."""
    title = node["title"]
    issues = []

    colour_opt = next(
        (o for o in node["options"] if re.fullmatch(r"colou?r", o["name"], re.I)), None
    )
    images = [e["node"]["url"] for e in node["images"]["edges"]]
    if not colour_opt or not images:
        return {}, issues

    by_colour = defaultdict(list)
    unmatched = []
    for url in images:
        c = colour_of(url, colour_opt["values"])
        (by_colour[c] if c else unmatched).append(url)
    if unmatched:
        issues.append(
            f"{title}: {len(unmatched)} image(s) match no colour name, left unordered "
            f"(e.g. {filename(unmatched[0])})"
        )
    if len(by_colour) < 2:
        return {}, issues

    counts = {c: len(v) for c, v in by_colour.items()}
    # The reference is the colour with the most styles, so every other colour can be
    # matched INTO it rather than losing styles the reference happens to lack.
    ref = max(by_colour, key=lambda c: (counts[c], c))
    ref_urls = by_colour[ref]
    n_styles = len(ref_urls)

    if len(set(counts.values())) > 1:
        short = {c: n for c, n in sorted(counts.items()) if n < n_styles}
        issues.append(
            f"{title}: uneven mockup counts, {ref} has {n_styles} but "
            + ", ".join(f"{c} has {n}" for c, n in short.items())
            + ". MANUAL ACTION: regenerate the missing mockups in Printful."
        )

    ref_sigs = [signature(u) for u in ref_urls]
    # position_of[style][colour] = index that style occupies in that colour's sequence
    position_of = defaultdict(dict)
    style_of = {}
    worst_margin = float("inf")

    for colour, urls in by_colour.items():
        sigs = [signature(u) for u in urls]
        # rows = this colour's photos, cols = reference styles; rows <= cols required
        cost = [[float(np.linalg.norm(s - r)) for r in ref_sigs] for s in sigs]
        if len(sigs) > n_styles:
            continue
        assign = hungarian(cost)
        seen = set()
        for i, style in enumerate(assign):
            if style in seen:
                issues.append(f"{title}/{colour}: duplicate style assignment, skipped")
                continue
            seen.add(style)
            best = cost[i][style]
            others = [cost[i][j] for j in range(n_styles) if j != style]
            margin = ((min(others) - best) / best) if others and best > 0 else 1.0
            style_of[filename(urls[i])] = style
            position_of[style][colour] = i
            worst_margin = min(worst_margin, margin)
            if verbose:
                print(f"    {colour:22s} photo {i + 1} -> style {style + 1}  margin {margin * 100:5.0f}%")

    # Two gates before this product is allowed to override the site's own sort. Both exist
    # so the tool can only ever make things better: it must be NEEDED, and it must be SURE.

    # Gate 1: is anything actually wrong? A style whose position is the same for every
    # colour is already consistent, and rewriting it would be churn with risk and no gain.
    drifting = [s for s in position_of if len(set(position_of[s].values())) > 1]
    if not drifting:
        if verbose:
            print("    already consistent across colours, nothing to rewrite")
        return {}, issues

    # Gate 2: are the pairings trustworthy? Some products (the bottle) are four near
    # identical studio shots whose edges barely differ. Guessing there could scramble a
    # gallery that is currently fine, so decline and hand it to a human instead.
    if worst_margin < MARGIN_FLOOR:
        issues.append(
            f"{title}: order varies by colour but the photos are too visually similar to "
            f"pair automatically (weakest match {worst_margin * 100:.0f}%). Left as-is. "
            f"MANUAL ACTION: reorder this product's images in Shopify by hand."
        )
        return {}, issues

    # Canonical order is the position each style ALREADY holds for most colours, so the
    # result is the majority's existing look rather than an aesthetic judgement of mine.
    ranked = sorted(
        position_of,
        key=lambda s: (
            statistics.median(position_of[s].values()),
            statistics.mean(position_of[s].values()),
            s,
        ),
    )
    rank_of_style = {s: i for i, s in enumerate(ranked)}
    order_map = {fn: rank_of_style[st] for fn, st in style_of.items()}

    if verbose:
        print(
            f"    rewriting {len(order_map)} images, fixes {len(drifting)} style(s) that "
            f"varied by colour, weakest match {worst_margin * 100:.0f}%"
        )

    return order_map, issues


def build(verbose=False):
    products = fetch_catalog()
    order = {}
    issues = []
    for node in products:
        if verbose:
            print(f"\n  {node['title']}")
        m, iss = analyse_product(node, verbose)
        order.update(m)
        issues.extend(iss)
    return order, issues


# ------------------------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--report", action="store_true", help="show findings, write nothing")
    g.add_argument("--check", action="store_true", help="exit 1 if the committed map is stale")
    g.add_argument("--write", action="store_true", help="regenerate the committed map")
    args = ap.parse_args()

    order, issues = build(verbose=args.report)

    print(f"\n{len(order)} images ordered across the catalogue.")
    if issues:
        print(f"\n{len(issues)} issue(s) needing a human:")
        for i in issues:
            print(f"  - {i}")
    else:
        print("No issues found.")

    # Running under GitHub Actions: put the same report on the job page, so a scheduled
    # run is readable without digging through logs.
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Merch mockup order\n\n`{len(order)}` images ordered.\n\n")
            if issues:
                f.write(f"**{len(issues)} item(s) need a human:**\n\n")
                for i in issues:
                    f.write(f"- {i}\n")
            else:
                f.write("No issues found.\n")
            f.write("\n")

    if args.report:
        return 0

    existing = {}
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding="utf-8") as f:
            existing = json.load(f).get("order", {})

    if args.check:
        if existing == order:
            print("\nCommitted map is current.")
            return 0
        added = set(order) - set(existing)
        removed = set(existing) - set(order)
        moved = {k for k in set(order) & set(existing) if order[k] != existing[k]}
        print(f"\nDRIFT: {len(added)} new, {len(removed)} gone, {len(moved)} reordered.")
        print("Run with --write to refresh.")
        return 1

    doc = {
        "_comment": (
            "Canonical mockup display order for merch.html, keyed by Shopify image "
            "filename. Generated by tools/merch-mockup-order in the Claude working "
            "folder. Unknown filenames are ignored by the site, which falls back to its "
            "built-in sort, so a Printful regeneration degrades instead of breaking."
        ),
        "order": dict(sorted(order.items())),
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1, sort_keys=False)
        f.write("\n")
    print(f"\nWrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
