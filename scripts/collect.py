# -*- coding: utf-8 -*-
"""
거래량 레이더 — 데이터 수집 + '날씨 레이더' 계산 + 과거 시험
- 코스피/코스닥 전 종목 (네이버 증권 공개 데이터)
- 출력 (docs/data/):
    radar.json     : 오늘 시장 날씨, '사볼까?' 종목, 과거 시험 결과  ← 새 화면용
    result.json    : (예전 구조) 거래량 급증 종목 — 텔레그램 봇 호환용
    stocks.json, meta.json
- 규칙 (2년치 2,300종목 검증 결과로 정함):
    시장 날씨 = 20일선 위에 있는 종목 비율  (🥶 <40%  😐 40~60%  🔥 ≥60%)
    🥶 → J '한 달 바닥인데 사람 몰림'      (20일 최저가 근처 + 거래량 2배)
    😐 → G '반년 만에 최고가 넘김' + A '거래 터졌는데 조용'
    🔥 → G 만
    매수: 신호 다음날 종가 / 보유 12거래일 / 15% 오르면 바로 팔기 / 떨어지면 팔기 없음 / 3종목
투자 추천이 아닌 데이터 조회용.
"""
import json, os, sys, time, re, datetime as dt, random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as sw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data")
CACHE = os.path.join(ROOT, "cache")
os.makedirs(OUT, exist_ok=True); os.makedirs(CACHE, exist_ok=True)

HDR = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) Chrome/120 Mobile Safari/537.36",
       "Referer": "https://m.stock.naver.com/"}
S = requests.Session(); S.headers.update(HDR)

DAYS = 480
BASE = 20
MIN_CAP = 100e8
MIN_TV = 1e8
# ---- 매매 규칙 ----
HOLD = 12            # 보유 거래일 (산 다음날부터 세서 12일째 종가에 팔기)
TP = 15.0            # 이만큼 오르면 팔기 %
COST = 0.25          # 왕복 비용 %
SLOTS = 3            # 동시에 들고 있는 종목 수
COLD, HOT = 0.40, 0.60
START_CASH = 10_000_000
# 예전 화면/봇 호환용 스크리너
WINDOWS = [10, 20, 40]

def _np(o):
    if hasattr(o, "item"): return o.item()
    raise TypeError(type(o))
def dump(obj, name, **kw):
    json.dump(obj, open(os.path.join(OUT, name), "w", encoding="utf-8"), ensure_ascii=False, default=_np, **kw)

def get(url, tries=3, **kw):
    for i in range(tries):
        try:
            r = S.get(url, timeout=15, **kw)
            if r.status_code == 200: return r
        except Exception: pass
        time.sleep(0.5 * (i + 1))
    return None

# ---------- 1. 종목 리스트 ----------
def list_market(mk):
    out, page = [], 1
    while True:
        r = get(f"https://m.stock.naver.com/api/stocks/marketValue/{mk}?page={page}&pageSize=100")
        if not r: break
        d = r.json()
        for s in d.get("stocks", []):
            if s.get("stockEndType") != "stock": continue
            name = s["stockName"]
            if re.search(r"스팩|SPAC|ETN|ETF|리츠|REIT|인프라|펀드", name, re.I): continue
            if s.get("tradableStatus") != "tradable": continue
            if (s.get("tradeStopType") or {}).get("name") != "TRADING": continue
            out.append({"code": s["itemCode"], "name": name, "mk": "KOSPI" if mk == "KOSPI" else "KOSDAQ",
                        "cap": float(s.get("marketValueRaw") or 0)})
        if page * 100 >= int(d.get("totalCount", 0)): break
        page += 1
    return out

# ---------- 2. 일봉 ----------
def ohlcv(code):
    r = get(f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={DAYS}&requestType=0")
    if not r: return None
    rows = re.findall(r'data="([^"]+)"', r.text)
    arr = []
    for x in rows:
        p = x.split("|")
        if len(p) < 6: continue
        try: arr.append([int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5])])
        except: pass
    return np.array(arr) if len(arr) >= 130 else None

def clean(c, v):
    j = c[1:] / np.where(c[:-1] > 0, c[:-1], 1) - 1
    if np.any(np.abs(j) > 0.35): return False
    if v[-BASE:].mean() < 5000: return False
    return True

def disclosures(code, ymd):
    """공시·뉴스: 기준일 -1 ~ +1일 건수 + 관리/경고 플래그"""
    d0 = dt.datetime.strptime(str(ymd), "%Y%m%d")
    lo, hi = d0 - dt.timedelta(days=1), d0 + dt.timedelta(days=1)
    disc, news, titles = 0, 0, []
    r = get(f"https://m.stock.naver.com/api/stock/{code}/disclosure?pageSize=30&page=1")
    if r:
        for x in r.json() if isinstance(r.json(), list) else []:
            try: t = dt.datetime.fromisoformat(x["datetime"][:19])
            except: continue
            if lo <= t <= hi + dt.timedelta(days=1):
                disc += 1
                if len(titles) < 2: titles.append("[공시] " + x["title"])
    r = get(f"https://m.stock.naver.com/api/news/stock/{code}?pageSize=40&page=1")
    if r:
        try:
            for grp in r.json():
                for x in grp.get("items", []):
                    t = dt.datetime.strptime(x["datetime"][:8], "%Y%m%d")
                    if lo <= t <= hi:
                        news += 1
                        if len(titles) < 3: titles.append("[뉴스] " + x["title"])
        except Exception: pass
    flags = []
    r = get(f"https://m.stock.naver.com/api/stock/{code}/integration")
    if r:
        try:
            for ic in (r.json().get("iconInfos") or []):
                flags.append(ic.get("text") or ic.get("name") or "")
        except Exception: pass
    return disc, news, titles, flags

# ---------- 3. 피처 ----------
def rmean(x, w):
    cs = np.cumsum(np.insert(x, 0, 0.0)); out = np.full(len(x), np.nan); out[w-1:] = (cs[w:] - cs[:-w]) / w; return out
def rmax(x, w):
    out = np.full(len(x), np.nan); out[w-1:] = sw(x, w).max(1); return out
def rmin(x, w):
    out = np.full(len(x), np.nan); out[w-1:] = sw(x, w).min(1); return out
def bucket_of(cap): b = cap / 1e8; return "s" if b < 300 else ("m" if b < 1000 else "l")

def features(a, cap):
    o, h, l, c, v = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]; n = len(c)
    m20 = rmean(v, 20); vr = np.zeros(n); vr[1:] = np.nan_to_num(v[1:] / np.where(m20[:-1] > 0, m20[:-1], np.nan))
    s = dict(d=a[:, 0].astype(int), o=o, h=h, l=l, c=c, v=v, n=n, b=bucket_of(cap),
             tv=rmean(c * v, 20), vr=vr, vr20max=rmax(vr, 20), ma20=rmean(c, 20), ma60=rmean(c, 60),
             hi120=rmax(c, 120), lo20=rmin(c, 20))
    chg20 = np.full(n, np.nan); chg20[20:] = (c[20:] / c[:-20] - 1) * 100; s["chg20"] = chg20
    return s

SIGNALS = {
    "G": ("반년 만에 최고가 넘김", "🚀", lambda s: (s["c"] >= s["hi120"]) & (np.roll(s["c"], 1) < np.roll(s["hi120"], 1))),
    "J": ("한 달 바닥인데 사람 몰림", "🛒", lambda s: (s["c"] <= s["lo20"] * 1.02) & (s["vr"] >= 2)),
    "A": ("거래 터졌는데 조용함", "🤫", lambda s: (s["vr20max"] >= 3) & (s["chg20"] >= -5) & (s["chg20"] <= 3)),
}
RULE = {"cold": ["J"], "mid": ["G", "A"], "hot": ["G"]}
WEATHER = {"cold": ("🥶", "추움"), "mid": ("😐", "보통"), "hot": ("🔥", "따뜻")}
def regime(b): return "cold" if b < COLD else ("hot" if b >= HOT else "mid")

# ---------- 4. 과거 시험 (3종목 계좌 시뮬레이션) ----------
def backtest(F, breadth, dates):
    for s in F.values(): s["di"] = {d: i for i, d in enumerate(s["d"])}
    masks = {k: {code: np.nan_to_num(SIGNALS[k][2](s)).astype(bool) for code, s in F.items()} for k in SIGNALS}
    cands = defaultdict(list); per_sig = defaultdict(list)   # per_sig[(k,regime)] -> 수익률
    for code, s in F.items():
        for k in masks:
            m = masks[k][code]; prev = False
            for i in range(120, s["n"] - 1):
                ok = m[i]
                if ok and not prev:
                    d = s["d"][i]; rg = regime(breadth.get(d, 0.5))
                    if k in RULE[rg]: cands[d].append((code, float(np.nan_to_num(s["tv"][i]))))
                    # 신호별 성적 (다음날 종가 매수, 12일, 익절)
                    ei = i + 1; st = ei + 1
                    if st + HOLD <= s["n"] and s["c"][ei] > 0:
                        ep = s["c"][ei]; hh = (s["h"][st:st+HOLD] / ep - 1) * 100; oo = (s["o"][st:st+HOLD] / ep - 1) * 100
                        r = (s["c"][st+HOLD-1] / ep - 1) * 100; hit = np.where(hh >= TP)[0]
                        if len(hit): kk = hit[0]; r = oo[kk] if (kk > 0 and oo[kk] >= TP) else TP
                        per_sig[(k, rg)].append(r - COST)
                prev = ok
    def simulate(pick="liquid", seed=0):
        rnd = random.Random(seed); cash = START_CASH; pos = []; eq = []; trades = []
        for ti, d in enumerate(dates):
            keep = []
            for p in pos:
                s = F[p["code"]]; i = s["di"].get(d)
                if i is None: keep.append(p); continue
                ep = p["ep"]; o, h, c = s["o"][i], s["h"][i], s["c"][i]; px = None; why = None
                if i > p["ei"]:
                    if (o / ep - 1) * 100 >= TP: px, why = o, "tp"
                    elif (h / ep - 1) * 100 >= TP: px, why = ep * (1 + TP / 100), "tp"
                if px is None and i >= p["ei"] + HOLD: px, why = c, "exp"
                if px is not None:
                    cash += p["qty"] * px * (1 - 0.0025)
                    trades.append({"date": int(d), "code": p["code"], "ret": round((px / ep - 1) * 100 - 0.25, 2), "why": why, "sig": p["sig"]})
                else: keep.append(p)
            pos = keep
            prev_d = dates[ti - 1] if ti > 0 else None
            free = SLOTS - len(pos)
            if free > 0 and prev_d in cands:
                held = {p["code"] for p in pos}
                cl = [x for x in cands[prev_d] if x[0] not in held]
                if pick == "liquid": cl.sort(key=lambda x: -x[1])
                else: rnd.shuffle(cl)
                for code, _ in cl[:free]:
                    s = F[code]; i = s["di"].get(d)
                    if i is None: continue
                    px = s["c"][i]; amt = cash / free
                    if px <= 0 or amt < px: continue
                    qty = amt // px; cash -= qty * px; free -= 1
                    sig = next((k for k in SIGNALS if masks[k][code][i-1]), "?") if i > 0 else "?"
                    pos.append(dict(code=code, ep=px, ei=i, qty=qty, sig=sig))
            v = cash
            for p in pos:
                s = F[p["code"]]; i = s["di"].get(d); v += p["qty"] * (s["c"][i] if i is not None else p["ep"])
            eq.append((int(d), v))
        return eq, trades
    def summary(eq, trades):
        v = np.array([x[1] for x in eq]); peak = np.maximum.accumulate(v); mdd = float(((v - peak) / peak).min() * 100)
        months = defaultdict(list)
        for d, vv in eq: months[str(d)[:6]].append(vv)
        mret = []; prev = START_CASH
        for m in sorted(months): mret.append({"m": m, "ret": round((months[m][-1] / prev - 1) * 100, 1)}); prev = months[m][-1]
        r = np.array([t["ret"] for t in trades]) if trades else np.array([0.0])
        return {"final": int(v[-1]), "ret": round((v[-1] / START_CASH - 1) * 100, 1), "mdd": round(mdd, 1),
                "trades": len(trades), "win": round(float((r > 0).mean() * 100), 1), "avgTrade": round(float(r.mean()), 2),
                "monthly": mret, "plusMonths": sum(1 for x in mret if x["ret"] > 0),
                "equity": [[d, int(vv)] for d, vv in eq[::3]]}
    # 종목 선택 운을 빼기 위해 무작위 선택 7회 → 중간값 run을 대표로, 범위도 같이
    runs = [simulate("random", sd) for sd in range(7)]
    runs.sort(key=lambda r: r[0][-1][1])
    eq, tr = runs[len(runs) // 2]
    main = summary(eq, tr)
    rand = [r[0][-1][1] for r in runs]
    liquid = summary(*simulate("liquid"))
    # 시장: 전 종목 똑같이 들고 있기
    mk = [START_CASH]
    for ti in range(1, len(dates)):
        rs = []
        for s in F.values():
            i = s["di"].get(dates[ti]); j = s["di"].get(dates[ti-1])
            if i is not None and j is not None and s["c"][j] > 0: rs.append(s["c"][i] / s["c"][j] - 1)
        mk.append(mk[-1] * (1 + (np.mean(rs) if rs else 0)))
    sig_stats = {}
    for (k, rg), arr in per_sig.items():
        a = np.array(arr); sig_stats[f"{k}_{rg}"] = {"n": len(a), "avg": round(float(a.mean()), 2), "win": round(float((a > 0).mean() * 100), 1)}
    return {"portfolio": main, "randomFinal": {"avg": int(np.mean(rand)), "min": int(min(rand)), "max": int(max(rand))},
            "liquidPick": {k: liquid[k] for k in ("final", "ret", "mdd", "trades", "win", "avgTrade")},
            "market": {"final": int(mk[-1]), "ret": round((mk[-1] / START_CASH - 1) * 100, 1),
                       "equity": [[int(d), int(v)] for d, v in zip(dates, mk)][::3]},
            "signals": sig_stats, "recentTrades": sorted(tr, key=lambda x: -x["date"])[:40],
            "period": {"from": int(dates[0]), "to": int(dates[-1]), "months": round(len(dates) / 21, 1)}}

# ---------- 5. 예전 구조 (봇 호환) ----------
def screen_at(c, v, end, win):
    s = end - win + 1
    if s - BASE < 0: return None
    ratios = []
    for t in range(s, end + 1):
        base = v[t - BASE:t].mean(); ratios.append(v[t] / base if base > 0 else 0)
    ratios = np.array(ratios); i = int(ratios.argmax()); spike = s + i
    chg = (c[end] / c[s - 1] - 1) * 100 if c[s - 1] > 0 else 0
    base = v[spike - BASE:spike].mean()
    r2 = float(v[spike + 1] / base) if spike + 1 <= end and base > 0 else -1.0
    return {"ratio": float(ratios[i]), "spike": spike, "chg": float(chg), "r2": r2}

def legacy_rows(data, meta):
    results = []
    for code, a in data.items():
        d, c, v = a[:, 0].astype(int), a[:, 4], a[:, 5]; end = len(c) - 1
        tv20 = (c[end - BASE:end] * v[end - BASE:end]).mean()
        if tv20 < MIN_TV or not clean(c, v): continue
        row = {"code": code, "name": meta[code]["name"], "mk": meta[code]["mk"], "cap": round(meta[code]["cap"] / 1e8),
               "close": c[end], "d1": round((c[end] / c[end - 1] - 1) * 100, 2) if c[end - 1] else 0,
               "tv": round(tv20 / 1e8, 1), "w": {}}
        best = 0
        for win in WINDOWS:
            r = screen_at(c, v, end, win)
            if not r: continue
            row["w"][str(win)] = {"r": round(r["ratio"], 2), "sd": int(d[r["spike"]]), "chg": round(r["chg"], 2), "r2": round(r["r2"], 2),
                                  "sc": round((c[end] / c[r["spike"]] - 1) * 100, 2), "sv": int(v[r["spike"]]),
                                  "av": int(v[r["spike"] - BASE:r["spike"]].mean())}
            best = max(best, r["ratio"])
        if best < 2: continue
        if not any(-15 <= x["chg"] <= 15 for x in row["w"].values()): continue
        row["spark"] = [round(x, 0) for x in c[end - 29:end + 1]]; row["sparkv"] = [int(x) for x in v[end - 29:end + 1]]
        results.append(row)
    return results

# ---------- main ----------
def main():
    t0 = time.time()
    offline = os.environ.get("OFFLINE") == "1" and os.path.exists(os.path.join(CACHE, "ohlcv.npz"))
    if offline:
        z = np.load(os.path.join(CACHE, "ohlcv.npz")); data = {}
        for k in z.files:
            if k.startswith("d_"): continue
            a = z[k].astype(np.float64)
            if "d_" + k in z.files: a[:, 0] = z["d_" + k]      # 날짜는 int32로 따로 저장됨 (float32는 날짜가 깨짐)
            data[k] = a
        meta = json.load(open(os.path.join(CACHE, "meta.json"), encoding="utf-8"))
        stocks = list(meta.values()); print(f"[오프라인] 캐시 {len(data)}개", flush=True)
    else:
        stocks = list_market("KOSPI") + list_market("KOSDAQ")
        stocks = [s for s in stocks if s["cap"] >= MIN_CAP]
        print(f"종목 {len(stocks)}개", flush=True)
        data = {}
        with ThreadPoolExecutor(8) as ex:
            fut = {ex.submit(ohlcv, s["code"]): s for s in stocks}
            for f in as_completed(fut):
                a = f.result()
                if a is not None: data[fut[f]["code"]] = a
        print(f"일봉 {len(data)}개 ({time.time()-t0:.0f}s)", flush=True)
        meta = {s["code"]: s for s in stocks}
        try:
            np.savez_compressed(os.path.join(CACHE, "ohlcv.npz"), **{k: v.astype(np.float32) for k, v in data.items()},
                                **{"d_" + k: v[:, 0].astype(np.int32) for k, v in data.items()})
            json.dump({k: meta[k] for k in data}, open(os.path.join(CACHE, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
        except Exception as e: print("캐시 저장 실패:", e, flush=True)

    # 피처
    F = {}
    last_date = 0
    for code, a in data.items():
        if meta[code]["cap"] < MIN_CAP or len(a) < 130: continue
        c, v = a[:, 4], a[:, 5]
        if not clean(c, v): continue
        F[code] = features(a, meta[code]["cap"]); last_date = max(last_date, int(a[-1, 0]))
    # 날씨 (날짜별 20일선 위 비율)
    up, tot = defaultdict(int), defaultdict(int)
    for s in F.values():
        for i in range(60, s["n"]):
            tot[s["d"][i]] += 1
            if s["c"][i] > s["ma20"][i]: up[s["d"][i]] += 1
    breadth = {d: up[d] / tot[d] for d in tot}
    dates = sorted(breadth)
    today_b = breadth[last_date]; rg = regime(today_b)
    print(f"기준일 {last_date} 날씨 {WEATHER[rg][1]} ({today_b*100:.0f}%)", flush=True)

    # 오늘 '사볼까?' 후보
    cands = []
    for code, s in F.items():
        i = s["n"] - 1
        if s["d"][i] != last_date or s["tv"][i] < MIN_TV: continue
        for k in RULE[rg]:
            m = np.nan_to_num(SIGNALS[k][2](s)).astype(bool)
            if m[i] and not m[i-1]:
                cands.append({"code": code, "name": meta[code]["name"], "mk": meta[code]["mk"], "cap": round(meta[code]["cap"] / 1e8),
                              "sig": k, "sigName": SIGNALS[k][0], "icon": SIGNALS[k][1],
                              "close": int(s["c"][i]), "d1": round((s["c"][i] / s["c"][i-1] - 1) * 100, 1),
                              "chg20": round(float(np.nan_to_num(s["chg20"][i])), 1), "vr": round(float(s["vr"][i]), 1),
                              "tv": round(float(s["tv"][i]) / 1e8, 1),
                              "target": int(round(s["c"][i] * (1 + TP / 100))),
                              "spark": [int(x) for x in s["c"][i-29:i+1]]})
                break
    cands.sort(key=lambda x: -x["tv"])
    # 뉴스/공시/관리종목 체크 (후보 상위 12개만)
    def enrich(row):
        disc, news, titles, flags = disclosures(row["code"], last_date)
        row.update(disc=disc, news=news, titles=titles, flags=flags); return row
    top = cands[:12]
    if not offline:
        with ThreadPoolExecutor(6) as ex: top = list(ex.map(enrich, top))
        top = [r for r in top if not any("관리" in f or "정지" in f for f in r["flags"])]
    else:
        for r in top: r.update(disc=0, news=0, titles=[], flags=[])
    picks, bench = top[:SLOTS], top[SLOTS:10]
    print(f"후보 {len(cands)}개 → 사볼까 {len(picks)}개", flush=True)

    # 과거 시험
    bt = backtest(F, breadth, dates)
    print(f"과거 시험: 3종목 {bt['portfolio']['ret']:+.1f}% / 시장 {bt['market']['ret']:+.1f}% ({time.time()-t0:.0f}s)", flush=True)

    radar = {"date": last_date, "weather": {"key": rg, "icon": WEATHER[rg][0], "name": WEATHER[rg][1], "pct": round(today_b * 100),
                                            "history": [[d, round(breadth[d] * 100)] for d in dates[-60:]]},
             "rule": {"hold": HOLD, "tp": TP, "slots": SLOTS, "cost": COST, "signals": {k: {"name": v[0], "icon": v[1]} for k, v in SIGNALS.items()},
                      "byWeather": RULE},
             "picks": picks, "bench": bench, "candidates": len(cands), "backtest": bt,
             "universe": len(F), "updated": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")}
    dump(radar, "radar.json", separators=(",", ":"))

    # 예전 구조 (봇)
    results = legacy_rows(data, meta)
    results.sort(key=lambda r: -max(x["r"] for x in r["w"].values()))
    if not offline:
        def enrich_old(row):
            w = row["w"].get("20") or row["w"].get("40") or row["w"].get("10")
            disc, news, titles, flags = disclosures(row["code"], w["sd"])
            row.update(disc=disc, news=news, titles=titles, flags=flags); return row
        with ThreadPoolExecutor(12) as ex: results = list(ex.map(enrich_old, results))
        results = [r for r in results if not any("관리" in f or "정지" in f for f in r["flags"])]
    else:
        for r in results: r.update(disc=0, news=0, titles=[], flags=[])
    dump(results, "result.json", separators=(",", ":"))
    dump([{"code": x["code"], "name": x["name"], "mk": x["mk"]} for x in stocks], "stocks.json", separators=(",", ":"))
    dump({"updated": radar["updated"], "lastDate": last_date, "universe": len(data), "candidates": len(results),
          "weather": radar["weather"]["name"], "picks": [p["name"] for p in picks]}, "meta.json")
    dump({"deprecated": True, "see": "radar.json"}, "backtest.json")
    print(f"완료 {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
