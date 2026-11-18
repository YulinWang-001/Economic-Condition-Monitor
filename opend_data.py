"""Daily closes from moomoo OpenD, with Yahoo Finance filling what OpenD can't serve.

Drop-in for the `yf.download(tickers, start=..., auto_adjust=True, progress=False)["Close"]` calls
in the dashboard scripts: `download()` returns the same shape (a "Close" column level, one column
per ticker, as Yahoo names them).

  - US stocks / ETFs (SPY, QQQ, SOXX, XLK ...) come from OpenD from OPEND_FROM onwards,
    forward-adjusted for splits and dividends (same basis as Yahoo's auto_adjust).
  - Before OPEND_FROM, Yahoo's history is spliced on, rescaled so the two join exactly: returns
    before the join are Yahoo's, everything after is OpenD's. OpenD's own history only starts in
    Oct 2006, and checked against Yahoo it misses dividend adjustments and has some bad bars before
    ~2016 (SPY: most quarterly ex-dividend days to 2013, stale bars in Aug 2011); from 2021 the two
    agree to the day, and OpenD picks up new dividends sooner.
  - Indices (^VIX, ^NDX, ^SOX, ^IRX ...), futures (HG=F, GC=F) and FX (AUDJPY=X) always come from
    Yahoo: OpenD doesn't serve US indices, and this account has no futures / FX quotes.
  - If OpenD isn't running and logged in, everything comes from Yahoo, as before.

Needs moomoo_OpenD running and logged in on 127.0.0.1:11111. Quote calls only, never trades.
OpenD allows history requests for 100 different tickers per 30 days (repeat requests are free).
"""
import re, time, socket, logging
import pandas as pd
import yfinance as yf

HOST, PORT = "127.0.0.1", 11111
OPEND_FROM = "2021-01-04"   # first day taken from OpenD (see above)
SOURCE = {}          # ticker -> where its data came from, filled by download()
_US = re.compile(r"^[A-Z]{1,5}([.-][A-Z])?$")      # plain US listing, e.g. SPY, BRK-B


def opend_up():
    try:
        socket.create_connection((HOST, PORT), timeout=2).close()
        return True
    except OSError:
        return False


def _kline(q, mm, code, start, end):
    """all daily bars for one code, page by page; waits out OpenD's limit of 60 requests per 30 s"""
    rows, key, tries = [], None, 0
    while True:
        ret, df, nxt = q.request_history_kline(code, start=start, end=end, ktype=mm.KLType.K_DAY,
                                               autype=mm.AuType.QFQ, max_count=1000, page_req_key=key)
        if ret != mm.RET_OK:
            if "frequency" in str(df) and tries < 4:
                tries += 1
                time.sleep(31)
                continue
            raise RuntimeError(df)
        rows.append(df)
        key = nxt
        if key is None:
            return pd.concat(rows)


def _opend_close(q, mm, ticker, start, end):
    code = "US." + ticker.replace("-", ".")
    df = _kline(q, mm, code, start, end)
    if df.empty:
        raise RuntimeError("no bars")
    return pd.Series(df["close"].values, index=pd.to_datetime(df["time_key"]).dt.normalize(), name=ticker)


def _yahoo(tickers, start, end, auto_adjust):
    if not tickers:
        return pd.DataFrame()
    raw = yf.download(list(tickers), start=start, end=end, auto_adjust=auto_adjust, progress=False)["Close"]
    if isinstance(raw, pd.Series):
        raw = raw.to_frame(tickers[0])
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    return raw


def download(tickers, start=None, end=None, auto_adjust=True, progress=False, **kw):
    if isinstance(tickers, str):
        tickers = tickers.split()
    tickers = list(dict.fromkeys(tickers))
    start = pd.Timestamp(start or "1990-01-01").strftime("%Y-%m-%d")
    end_s = (pd.Timestamp(end) if end else pd.Timestamp.now(tz="America/New_York").tz_localize(None)).strftime("%Y-%m-%d")
    cols, yahoo_only = {}, [t for t in tickers if not _US.match(t)]
    us = [t for t in tickers if _US.match(t)]

    if us and opend_up():
        import moomoo as mm
        logging.disable(logging.INFO)
        q = mm.OpenQuoteContext(host=HOST, port=PORT)
        try:
            for t in us:
                try:
                    cols[t] = _opend_close(q, mm, t, max(start, OPEND_FROM), end_s)
                    SOURCE[t] = "OpenD"
                except Exception as ex:
                    print(f"opend_data: {t} not available from OpenD ({ex}); using Yahoo")
                    yahoo_only.append(t)
        finally:
            q.close()
            logging.disable(logging.NOTSET)
    else:
        if us:
            print("opend_data: OpenD is not running; using Yahoo for", ", ".join(us))
        yahoo_only += us

    # Yahoo for non-US-listings, OpenD failures, and the history before OpenD's first bar
    yh = _yahoo(yahoo_only, start, end, auto_adjust)
    for t in yahoo_only:
        cols[t] = yh[t] if t in yh else pd.Series(dtype=float, name=t)
        SOURCE[t] = "Yahoo"
    early = [t for t, s in cols.items() if t not in yahoo_only and s.index[0] > pd.Timestamp(start) + pd.Timedelta(days=10)]
    # Yahoo only up to a few days past the join, so the two overlap on the join day
    ye = _yahoo(early, start, (pd.Timestamp(OPEND_FROM) + pd.Timedelta(days=10)).strftime("%Y-%m-%d"), True)
    for t in early:
        y, o = (ye[t].dropna() if t in ye else pd.Series(dtype=float)), cols[t]
        common = o.index.intersection(y.index)
        if not len(common):
            SOURCE[t] = "OpenD"      # nothing older on Yahoo either (fund launched later)
            continue
        j = common[0]
        cols[t] = pd.concat([y.loc[:j].iloc[:-1] * (o.loc[j] / y.loc[j]), o.loc[j:]])
        SOURCE[t] = f"Yahoo to {y.loc[:j].index[-2]:%Y-%m-%d}, OpenD from {j:%Y-%m-%d}" if len(y.loc[:j]) > 1 else "OpenD"

    out = pd.DataFrame({t: cols[t] for t in tickers}).sort_index()
    out.index.name = "Date"
    out.columns = pd.MultiIndex.from_product([["Close"], out.columns])
    return out


def history(codes, start, end=None):
    """Daily closes straight from OpenD for full moomoo codes (e.g. HK.800000, HK.02800), no Yahoo
    splice; forward-adjusted where that applies (indices are price-only). Codes OpenD refuses are
    left out and reported."""
    import moomoo as mm
    end_s = (pd.Timestamp(end) if end else pd.Timestamp.now(tz="America/New_York").tz_localize(None)).strftime("%Y-%m-%d")
    logging.disable(logging.INFO)
    q = mm.OpenQuoteContext(host=HOST, port=PORT)
    cols = {}
    try:
        for c in codes:
            try:
                df = _kline(q, mm, c, pd.Timestamp(start).strftime("%Y-%m-%d"), end_s)
                cols[c] = pd.Series(df["close"].values, index=pd.to_datetime(df["time_key"]).dt.normalize(), name=c)
                SOURCE[c] = "OpenD"
            except Exception as ex:
                print(f"opend_data: {c} not available from OpenD ({ex})")
    finally:
        q.close()
        logging.disable(logging.NOTSET)
    return pd.DataFrame(cols).sort_index()


def volume(tickers, start=None, end=None):
    """Daily share volume and $ turnover from OpenD for plain US tickers (no Yahoo fallback).
    Forward-adjusted bars, so share volume is split-adjusted (e.g. SOXX's 2024 3:1 split is continuous).
    Returns {ticker: DataFrame[volume, turnover]}; tickers OpenD can't serve are left out and reported."""
    if isinstance(tickers, str):
        tickers = tickers.split()
    us = [t for t in dict.fromkeys(tickers) if _US.match(t)]
    if not us or not opend_up():
        if us:
            print("opend_data: OpenD is not running; no volume for", ", ".join(us))
        return {}
    import moomoo as mm
    start = pd.Timestamp(start or "1990-01-01").strftime("%Y-%m-%d")
    end_s = (pd.Timestamp(end) if end else pd.Timestamp.now(tz="America/New_York").tz_localize(None)).strftime("%Y-%m-%d")
    logging.disable(logging.INFO)
    q = mm.OpenQuoteContext(host=HOST, port=PORT)
    out = {}
    try:
        for t in us:
            try:
                df = _kline(q, mm, "US." + t.replace("-", "."), start, end_s)
                idx = pd.to_datetime(df["time_key"]).dt.normalize()
                out[t] = pd.DataFrame({"volume": df["volume"].values, "turnover": df["turnover"].values}, index=idx.values)
            except Exception as ex:
                print(f"opend_data: no OpenD volume for {t} ({ex})")
    finally:
        q.close()
        logging.disable(logging.NOTSET)
    return out


def sources(tickers=None):
    """One line per ticker: where its prices came from."""
    return "\n".join(f"  {t:10s} {SOURCE[t]}" for t in (tickers or SOURCE) if t in SOURCE)
