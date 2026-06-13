#!/usr/bin/env python3
"""
Cycle Floor Proximity - data fetcher (BGeometrics ?token= format).
Runs on GitHub Actions (server-side: no browser CORS limits, key stays in a secret).
Writes data.json, which the HTML tool reads from the repo's raw URL.

Auto-fetched:
  Kraken (no key):        price, 200-week SMA %
  BGeometrics (?token=):  mvrv, nupl, mvrv_zscore, puell, reserve_risk,
                          fear-greed, funding-rate
Manual in the tool:       Weekly Supertrend, exchange flow.

Never crashes on a single bad source: logs the error and keeps going, so the
rest of the data still updates and any failed field falls back to manual.

BGeometrics free tier = 15 requests/day. This uses ~7/day.
"""

import json, os, time, datetime, urllib.request

OUT = "data.json"
TOKEN = os.environ.get("BG_API_KEY", "").strip()
BASE = "https://api.bgeometrics.com/v1"

def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "cfp/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def bg_value(metric, errors):
    """Fetch one BGeometrics metric, return latest float or None."""
    try:
        j = get_json(f"{BASE}/{metric}?token={TOKEN}")
        val = None
        if isinstance(j, list) and j:
            last = j[-1]
            if isinstance(last, dict):
                for k in (metric, "value", "v", "y"):
                    if k in last:
                        val = last[k]; break
                if val is None:
                    for v in reversed(list(last.values())):
                        try: val = float(v); break
                        except (TypeError, ValueError): pass
            else:
                val = last
        elif isinstance(j, dict):
            for k in (metric, "value", "v", "y", "last"):
                if k in j:
                    val = j[k]; break
        if val is None:
            errors.append(f"{metric}: unrecognized shape -> {str(j)[:160]}")
            return None
        return float(val)
    except Exception as e:
        errors.append(f"{metric}: {e}")
        return None

data = {
    "updated_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "errors": [],
}
err = data["errors"]

# ---- Kraken weekly OHLC -> price + 200-week SMA (no key) ----
try:
    j = get_json("https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=10080")
    res = j["result"]
    key = [k for k in res.keys() if k != "last"][0]
    closes = [float(c[4]) for c in res[key]]
    price = closes[-1]
    window = closes[-200:] if len(closes) >= 200 else closes
    sma = sum(window) / len(window)
    data["price_usd"] = round(price, 2)
    data["sma_200w"] = round(sma, 2)
    data["pct_above_200w"] = round((price / sma - 1) * 100, 1)
    if len(closes) < 200:
        err.append(f"200w: only {len(closes)} weekly candles; SMA partial")
except Exception as e:
    err.append(f"kraken: {e}")

if not TOKEN:
    err.append("BG_API_KEY not set; on-chain metrics skipped (enter manually)")
else:
    # ---- core valuation ----
    for name, key in [("mvrv","mvrv"), ("nupl","nupl"),
                      ("mvrv-zscore","mvrv_zscore"),
                      ("puell-multiple","puell"),
                      ("reserve-risk","reserve_risk")]:
        v = bg_value(name, err)
        if v is not None:
            data[key] = round(v, 6)
        time.sleep(2)

    # ---- Fear & Greed (BGeometrics primary) ----
    fg = bg_value("fear-greed", err)
    if fg is not None:
        data["fng"] = round(fg)
    time.sleep(2)

    # ---- Funding rate -> number + best-guess label ----
    fr = bg_value("funding-rate", err)
    if fr is not None:
        data["funding_value"] = round(fr, 6)
        # NOTE: units unverified on first run. These thresholds assume a small
        # percentage-style rate. If the magnitude looks off after the first run,
        # tell Claude the raw funding_value and the thresholds get fixed once.
        if fr < -0.005:   lab = "negative"
        elif fr <= 0.02:  lab = "flat"
        elif fr <= 0.05:  lab = "mild"
        else:             lab = "elevated"
        data["funding_label"] = lab

# ---- F&G fallback if BGeometrics didn't supply it ----
if "fng" not in data:
    try:
        j = get_json("https://api.alternative.me/fng/?limit=1")
        data["fng"] = int(j["data"][0]["value"])
    except Exception as e:
        err.append(f"fng-fallback: {e}")

with open(OUT, "w") as f:
    json.dump(data, f, indent=2)
print(json.dumps(data, indent=2))

