"""HTML version of market_regime_dashboard.py, combined with the relative-strength ratio tabs and a
momentum-factor tab (SPMO / MTUM vs SPY / QQQ).

Uses the data loaders, indicators and z-score method in market_regime_core.py.
Usage: python market_regime_html.py OUT.html [TEMPLATE.html]
  TEMPLATE may contain a __DATA__ placeholder, or be an existing dashboard whose data block is
  replaced in place (default: OUT itself).
"""
import os, re, sys, json, warnings, subprocess, tempfile, urllib.parse
import pandas as pd
import yfinance as yf
import sector_performance as sector_tools
import options_history as option_tools
import opend_data as od   # OpenD prices for US stocks / ETFs, Yahoo for the rest
warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# data loaders, indicators and z-score (rebuilt from the original market_regime_dashboard.py)
import market_regime_core as mrd

CLOSE_DONE = (16, 20)   # NY time after which today's bar is treated as the final close
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SNAP_DIR = os.path.join(HERE, "Sector_Charts")   # one sub-folder per data date
SNAP_PERIOD = "1Y"                                 # chart window for the sector images

# ratio tabs: market-leadership ratios, then the extra ratios the regime score uses
PAIRS = [
    {"key": "qqq", "tab": "Growth vs market (QQQ / SPY)", "a": "QQQ", "b": "SPY",
     "title": "QQQ / SPY: Nasdaq-100 vs S&P 500",
     "up": "Nasdaq-100 / mega-cap growth leading the S&P 500.", "down": "growth lagging the broad market.",
     "note": "QQQ history starts Mar 1999."},
    {"key": "rsp", "tab": "Breadth (RSP / SPY)", "a": "RSP", "b": "SPY",
     "title": "Market breadth: equal-weight (RSP) vs cap-weight (SPY) S&P 500",
     "up": "the average S&P 500 stock beating the index: broadening breadth.", "down": "a narrow market led by the largest stocks.",
     "note": "RSP history starts May 2003."},
    {"key": "soxx", "tab": "Semis vs Nasdaq (SOXX / QQQ)", "a": "SOXX", "b": "QQQ",
     "title": "SOXX / QQQ: semiconductors vs Nasdaq-100",
     "up": "semis outperforming (a long SOXX / short QQQ pair making money).", "down": "semis lagging the Nasdaq-100.",
     "note": "SOXX history starts Jul 2001."},
    {"key": "soxxspy", "tab": "Semis vs market (SOXX / SPY)", "a": "SOXX", "b": "SPY",
     "title": "SOXX / SPY: semiconductors vs S&P 500",
     "up": "semis outperforming the broad market: AI / cyclical tech leadership.", "down": "semis lagging the S&P 500.",
     "note": "SOXX history starts Jul 2001."},
    {"key": "beta", "tab": "Risk appetite (SPHB / SPLV)", "a": "SPHB", "b": "SPLV",
     "title": "High beta vs low volatility: SPHB / SPLV",
     "up": "risk-on: the 100 highest-beta S&P 500 stocks beating the 100 lowest-volatility ones.", "down": "risk-off / defensive leadership.",
     "note": "SPHB and SPLV start May 2011 and rebalance quarterly, so sector mix shifts over time (SPHB is currently heavy in tech/semis, so it overlaps the SOXX trade)."},
]
EXTRA_PAIRS = [
    # baskets: "ta" / "tb" may list several tickers; the leg is then an equal-weight index (rebalanced daily, base 100)
    {"key": "cyc", "tab": "Cyclicals vs defensives", "a": "Cyclicals", "b": "Defensives",
     "ta": ["XLY", "XLI", "XLB"], "tb": ["XLP", "XLU", "XLV"],
     "title": "Cyclicals vs defensives: equal-weight (XLY, XLI, XLB) / equal-weight (XLP, XLU, XLV)",
     "basket": "Cyclicals = equal-weight XLY (consumer discretionary), XLI (industrials) and XLB (materials); "
               "Defensives = equal-weight XLP (consumer staples), XLU (utilities) and XLV (health care). "
               "Each basket is rebalanced to equal weights daily and indexed to 100.",
     "up": "cyclical sectors leading defensives: the economy seen as strong.", "down": "defensive sectors leading: growth worries.",
     "note": "All six Select Sector SPDRs start Dec 1998; dividend-adjusted closes."},
    {"key": "credit", "tab": "Credit (HYG / IEF)", "a": "HYG", "b": "IEF",
     "title": "High-yield credit vs Treasuries: HYG / IEF",
     "up": "junk bonds beating 7–10y Treasuries: credit risk appetite.", "down": "flight to quality in fixed income.",
     "note": "HYG starts Apr 2007."},
    {"key": "cugold", "tab": "Copper / Gold", "a": "Copper", "b": "Gold", "ta": "HG=F", "tb": "GC=F",
     "title": "Copper vs gold: growth vs safety",
     "up": "growth-sensitive copper beating safe-haven gold.", "down": "safety bid / growth worries.",
     "note": "Front-month futures (HG=F, GC=F), price only and subject to roll effects; history from 2000."},
    # "corr" = window (days) for the rolling correlation of daily returns, charted on this pair's tab
    {"key": "spytlt", "tab": "Stocks vs bonds (SPY / TLT)", "a": "SPY", "b": "TLT", "corr": 60,
     "title": "SPY / TLT: S&P 500 vs long-dated Treasuries",
     "up": "equities beating 20y+ Treasuries: risk-on, growth over duration.", "down": "long bonds outperforming: flight to safety or growth scare.",
     "note": "TLT history starts Jul 2002. The rolling correlation uses daily changes in adjusted closes (total return)."},
]
# Select Sector SPDRs vs SPY, shown as their own group on the ratios page
SECTORS = [("XLK", "Technology"), ("XLC", "Communication services"), ("XLY", "Consumer discretionary"),
           ("XLF", "Financials"), ("XLI", "Industrials"), ("XLB", "Materials"), ("XLE", "Energy"),
           ("XLV", "Health care"), ("XLP", "Consumer staples"), ("XLU", "Utilities"), ("XLRE", "Real estate")]
GICS_2018 = " The Sep 2018 GICS change moved Alphabet, Meta and Netflix etc. into the new XLC, so history before then is not like-for-like."
SECTOR_PAIRS = [
    {"key": "s_" + t.lower(), "grp": "sector", "tab": f"{name} ({t})", "a": t, "b": "SPY",
     "title": f"{t} / SPY: {name} vs S&P 500",
     "up": f"{name.lower()} outperforming the S&P 500.", "down": f"{name.lower()} lagging the S&P 500.",
     "note": (GICS_2018 if t in ("XLK", "XLY", "XLC") else "").strip()}
    for t, name in SECTORS
]

# display metadata per indicator (names must match market_regime_dashboard.build_indicators)
IND_META = {
    "Trend: SPY vs 200d":     ("SPY's distance from its 200-day average", lambda df, i: f"SPY {i:+.1%} vs 200d"),
    "Breadth: RSP/SPY":       ("3-month change in equal-weight vs cap-weight", lambda df, i: f"{i:+.1%} over 3M"),
    "Growth: QQQ/SPY":        ("3-month change in Nasdaq-100 vs S&P 500", lambda df, i: f"{i:+.1%} over 3M"),
    "Beta: SPHB/SPLV":        ("3-month change in high beta vs low vol", lambda df, i: f"{i:+.1%} over 3M"),
    "Cyclical: XLY/XLP":      ("3-month change in discretionary vs staples", lambda df, i: f"{i:+.1%} over 3M"),
    "Credit: HYG/IEF":        ("3-month change in high yield vs Treasuries", lambda df, i: f"{i:+.1%} over 3M"),
    "Macro: Copper/Gold":     ("3-month change in copper vs gold", lambda df, i: f"{i:+.1%} over 3M"),
    "FX: AUDJPY":             ("3-month change in AUD/JPY (carry / risk FX)", lambda df, i: f"{i:+.1%} over 3M"),
    "Vol: VIX3M/VIX":         ("VIX term structure; above 1 = calm (contango)", lambda df, i: f"{i:.2f} (VIX {df['VIX'].iloc[-1]:.1f})"),
    "Spread: -HY OAS":        ("High-yield option-adjusted spread (inverted)", lambda df, i: f"HY OAS {-i:.2f}%"),
    "Rates: -Real 10y chg":   ("3-month change in 10y real yield (inverted)", lambda df, i: f"real 10y {-i * 100:+.0f}bp over 3M ({df['REAL_10Y'].iloc[-1]:.2f}%)"),
    "Dollar: -USD chg":       ("3-month change in broad dollar (inverted)", lambda df, i: f"USD {-i:+.1%} over 3M"),
    "Liquidity: NetLiq chg":  ("3-month change in Fed balance sheet − TGA − RRP", lambda df, i: f"{i:+.1%} over 3M (${df['NET_LIQ'].iloc[-1] / 1e3:.2f}tn)"),
}


def r2(s):
    return [None if pd.isna(v) else round(float(v), 3) for v in s]


def build_regime():
    px, fred = mrd.load_yahoo(), mrd.load_fred()
    # keep US trading days only (futures / FX trade on some US holidays); fill those series forward
    px = px.ffill().dropna(subset=["SPY"])
    ind, df = mrd.build_indicators(px, fred)
    z = ind.apply(mrd.zscore).clip(-3, 3)
    score = z.mean(axis=1).rolling(5).mean()          # same 1-week smoothing as the script
    start = score.first_valid_index()
    z, score, spy = z.loc[start:], score.loc[start:], df["SPY"].loc[start:]
    last = z.index[-1]

    def z_at(col, back):
        s = z[col].dropna()
        return float(s.iloc[-1 - back]) if len(s) > back else None

    inds = []
    for col in z.columns:
        desc, fmt = IND_META[col]
        raw = ind[col].dropna()
        inds.append({"name": col, "desc": desc, "reading": fmt(df, float(raw.iloc[-1])),
                     "z": z_at(col, 0), "z1w": z_at(col, 5), "z1m": z_at(col, 21)})
    # raw VIX term structure for its own chart (the z-score hides whether the curve is inverted)
    vix_ratio = (df["VIX3M"] / df["VIX"]).reindex(z.index)
    return {"d": [d.strftime("%Y-%m-%d") for d in z.index], "spy": r2(spy), "score": r2(score),
            "z": {c: r2(z[c]) for c in z.columns}, "ind": inds, "asof": last.strftime("%Y-%m-%d"),
            "vix": r2(vix_ratio), "vixlvl": r2(df["VIX"].reindex(z.index))}


def build_pairs(pairs=None):
    pairs = pairs or PAIRS + EXTRA_PAIRS + SECTOR_PAIRS
    legs = lambda p: [p.get("ta", p["a"]), p.get("tb", p["b"])]
    tick = sorted({t for p in pairs for leg in legs(p) for t in (leg if isinstance(leg, list) else [leg])})
    px = od.download(tick, start="1999-03-10", auto_adjust=True, progress=False)["Close"]

    def series(leg):
        if not isinstance(leg, list):
            return px[leg]
        r = px[leg].dropna().pct_change().mean(axis=1).fillna(0)   # equal weights, rebalanced daily
        return 100 * (1 + r).cumprod()

    out = []
    vol = numerator_volume(pairs)
    for p in pairs:
        ta, tb = legs(p)
        df = pd.concat({"A": series(ta), "B": series(tb)}, axis=1).dropna()
        ta, tb = "A", "B"
        if p.get("grp") == "sector":
            p = dict(p, note=f"{p['a']} history from {df.index[0]:%b %Y}. " + p["note"])
        rec = dict(p, d=[d.strftime("%Y-%m-%d") for d in df.index],
                   q=[round(float(v), 4) for v in df[ta]], s=[round(float(v), 4) for v in df[tb]])
        if p["key"] in vol:
            v, kind = vol[p["key"]]
            rec["v"] = [None if pd.isna(x) else int(x) for x in v.reindex(df.index)]
            rec["vk"] = kind
        out.append(rec)
    return out


def numerator_volume(pairs):
    """{pair key: (daily volume series, kind)} for the numerator leg, from OpenD (history from Oct 2006).
    A single ETF gets its split-adjusted share volume ("shares"); a basket gets the summed $ turnover of
    its members ("usd"); futures (copper) have no OpenD volume and are left out."""
    tick = sorted({t for p in pairs for t in (p.get("ta", p["a"]) if isinstance(p.get("ta", p["a"]), list)
                                              else [p.get("ta", p["a"])])})
    raw = od.volume(tick, start="2006-01-01")
    out = {}
    for p in pairs:
        leg = p.get("ta", p["a"])
        if isinstance(leg, list):
            if all(t in raw for t in leg):
                out[p["key"]] = (pd.concat([raw[t]["turnover"] for t in leg], axis=1).sum(axis=1, min_count=len(leg)), "usd")
        elif leg in raw:
            out[p["key"]] = (raw[leg]["volume"], "shares")
    return out


# Momentum tab: momentum-factor ETFs against broad-market benchmarks (the page computes ROC, relative
# returns and the daily proxy z-score from these closes)
MOM_ETFS = ["SPMO", "MTUM"]          # Invesco S&P 500 Momentum (Oct 2015), iShares MSCI USA Momentum (Apr 2013)
MOM_BENCH = ["SPY", "QQQ"]


def build_momentum():
    tick = MOM_ETFS + MOM_BENCH
    px = od.download(tick, start="2013-04-01", auto_adjust=True, progress=False)["Close"]
    src = {t: od.SOURCE.get(t, "?") for t in tick}
    px = px[px["SPY"].notna()]                      # US trading days only
    cols = {}
    for t in tick:
        s = px[t]
        first = s.first_valid_index()
        if first is not None:
            s = pd.concat([s.loc[:first].iloc[:-1], s.loc[first:].ffill()])   # fill gaps after the first bar only
        cols[t] = [None if pd.isna(v) else round(float(v), 4) for v in s]
    return {"d": [d.strftime("%Y-%m-%d") for d in px.index], "px": cols, "etfs": MOM_ETFS, "bench": MOM_BENCH, "src": src}


def build_data():
    regime, pairs = build_regime(), build_pairs()
    mom = build_momentum()
    asof = max([regime["asof"]] + [p["d"][-1] for p in pairs])
    ny = pd.Timestamp.now(tz="America/New_York")
    final = asof < ny.strftime("%Y-%m-%d") or (ny.hour, ny.minute) >= CLOSE_DONE
    sectors = sector_tools.build_sector_performance()
    options = option_tools.build_options_data(sector_data=sectors)
    return {"asof": asof, "final": final, "regime": regime, "pairs": pairs, "mom": mom, "sectorPerformance": sectors, "options": options}


def write(out, template, data):
    html = open(template, encoding="utf-8").read()
    html = sector_tools.install_tab(html)
    html = option_tools.install_options(html)
    blob = json.dumps(data, separators=(",", ":"))
    if "__DATA__" in html:
        html = html.replace("__DATA__", blob)
    else:
        html, n = re.subn(r"const ALL = \{.*?\};\n", lambda m: "const ALL = " + blob + ";\n", html, count=1, flags=re.S)
        if n != 1:
            raise RuntimeError("could not find the data block in " + template)
    tmp = out + ".tmp"
    open(tmp, "w", encoding="utf-8").write(html)
    os.replace(tmp, out)


def sector_snapshots(page, asof):
    """one PNG per sector ratio, from the page's #snap view: ratio; vs 200-day avg + RSI; drawdown vs 200-day avg.
    Saved as Sector_Charts/<asof>/<TICKER>_SPY.png; a later run on the same date overwrites them."""
    if not os.path.exists(CHROME):
        print("Google Chrome not found; sector images skipped")
        return
    out_dir = os.path.join(SNAP_DIR, asof)
    os.makedirs(out_dir, exist_ok=True)
    url = "file://" + urllib.parse.quote(os.path.abspath(page))
    with tempfile.TemporaryDirectory() as prof:
        base = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--user-data-dir=" + prof,
                "--virtual-time-budget=8000"]
        for p in SECTOR_PAIRS:
            link = f"{url}#snap/{p['key']}/{SNAP_PERIOD}"
            # first pass reads the laid-out height, second takes the screenshot at exactly that size
            dom = subprocess.run(base + ["--window-size=1112,2400", "--dump-dom", link],
                                 capture_output=True, text=True, timeout=120).stdout
            m = re.search(r'data-h="(\d+)"', dom)
            png = os.path.join(out_dir, f"{p['a']}_{p['b']}.png")
            subprocess.run(base + [f"--window-size=1112,{int(m.group(1)) if m else 1700}", "--force-device-scale-factor=2",
                                   "--screenshot=" + png, link], capture_output=True, timeout=120)
    print("sector images:", out_dir)


if __name__ == "__main__":
    OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "Market_Regime_Dashboard.html")
    TEMPLATE = sys.argv[2] if len(sys.argv) > 2 else OUT
    data = build_data()
    write(OUT, TEMPLATE, data)
    rg = data["regime"]
    print("regime", rg["d"][0], "->", rg["d"][-1], "score", rg["score"][-1])
    for p in data["pairs"]:
        print(p["key"], len(p["d"]), p["d"][0], "->", p["d"][-1])
    m = data["mom"]
    print("momentum", m["d"][0], "->", m["d"][-1], {t: m["src"][t] for t in m["src"]})
    print("asof", data["asof"], "final" if data["final"] else "INTRADAY")
    print("price sources:\n" + od.sources())
    sector_snapshots(OUT, data["asof"])
