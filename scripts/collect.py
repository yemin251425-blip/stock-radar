# -*- coding: utf-8 -*-
"""
거래량 스크리너 데이터 수집 + 조건 계산 + 백테스트
- 코스피/코스닥 전 종목 (네이버 증권 공개 데이터)
- 출력: docs/data/result.json, docs/data/backtest.json, docs/data/meta.json
투자 추천이 아닌 데이터 조회용.
"""
import json, os, sys, time, re, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data")
os.makedirs(OUT, exist_ok=True)

HDR = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) Chrome/120 Mobile Safari/537.36",
       "Referer": "https://m.stock.naver.com/"}
S = requests.Session(); S.headers.update(HDR)

DAYS = 160          # 수집 거래일 수 (백테스트용)
WINDOWS = [10, 20, 40]   # 스크리너 기간(거래일)
BASE = 20           # 평균 거래량 기준 기간
K_GRID = [2, 3, 4, 5]
CAP_GRID = [3, 5, 10, "up"]      # "up" = 이미 3% 넘게 오른 것 (반대 조건 검증용)
FLOOR_GRID = [-5, -100]
BUCKETS = ["all", "s", "m", "l"]  # 전체 / 300억 미만 / 300~1000억 / 1000억 이상
PERSIST = [0, 1]                  # 1 = 다음날 거래량도 1.5배 이상
def bucket_of(cap_won):
    b = cap_won / 1e8
    return "s" if b < 300 else ("m" if b < 1000 else "l")
MIN_CAP = 100e8     # 시총 100억 미만 제외
MIN_TV = 1e8        # 20일 평균 거래대금 1억 미만 제외

def get(url, tries=3, **kw):
    for i in range(tries):
        try:
            r = S.get(url, timeout=15, **kw)
            if r.status_code == 200:
                return r
        except Exception:
            pass
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
    return np.array(arr) if len(arr) >= BASE + max(WINDOWS) + 25 else None

# ---------- 3. 스크리너 ----------
def screen_at(c, v, end, win):
    """end: 평가일 인덱스(포함). 반환 (maxRatio, spikeIdx, chgPct) / None"""
    s = end - win + 1
    if s - BASE < 0: return None
    ratios = []
    for t in range(s, end + 1):
        base = v[t - BASE:t].mean()
        ratios.append(v[t] / base if base > 0 else 0)
    ratios = np.array(ratios)
    i = int(ratios.argmax())
    spike = s + i
    chg = (c[end] / c[s - 1] - 1) * 100 if c[s - 1] > 0 else 0
    # 다음날 거래량도 이어졌나 (평소 대비 배수). 폭발일이 마지막 날이면 아직 모름(-1)
    base = v[spike - BASE:spike].mean()
    r2 = float(v[spike + 1] / base) if spike + 1 <= end and base > 0 else -1.0
    return float(ratios[i]), spike, float(chg), r2

def clean(c, v):
    """액면병합/분할(하루 ±35% 초과), 거래량 너무 적은 종목 제외"""
    j = c[1:] / np.where(c[:-1] > 0, c[:-1], 1) - 1
    if np.any(np.abs(j) > 0.35): return False
    if v[-BASE:].mean() < 5000: return False
    return True

def disclosures(code, spike_ymd):
    """공시·뉴스: 급등일 기준 -1 ~ +1일 사이 건수"""
    d0 = dt.datetime.strptime(str(spike_ymd), "%Y%m%d")
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
    # 관리종목/투자경고 아이콘
    flags = []
    r = get(f"https://m.stock.naver.com/api/stock/{code}/integration")
    if r:
        try:
            for ic in (r.json().get("iconInfos") or []):
                flags.append(ic.get("text") or ic.get("name") or "")
        except Exception: pass
    return disc, news, titles, flags

def main():
    t0 = time.time()
    stocks = list_market("KOSPI") + list_market("KOSDAQ")
    stocks = [s for s in stocks if s["cap"] >= MIN_CAP]
    print(f"종목 {len(stocks)}개", flush=True)

    data = {}
    with ThreadPoolExecutor(16) as ex:
        fut = {ex.submit(ohlcv, s["code"]): s for s in stocks}
        for f in as_completed(fut):
            a = f.result()
            if a is not None: data[fut[f]["code"]] = a
    print(f"일봉 {len(data)}개 ({time.time()-t0:.0f}s)", flush=True)

    meta = {s["code"]: s for s in stocks}
    results, last_date = [], 0
    for code, a in data.items():
        d, c, v = a[:, 0].astype(int), a[:, 4], a[:, 5]
        end = len(c) - 1
        last_date = max(last_date, int(d[end]))
        tv20 = (c[end - BASE:end] * v[end - BASE:end]).mean()
        if tv20 < MIN_TV: continue
        if not clean(c, v): continue
        row = {"code": code, "name": meta[code]["name"], "mk": meta[code]["mk"],
               "cap": round(meta[code]["cap"] / 1e8), "close": c[end],
               "d1": round((c[end] / c[end - 1] - 1) * 100, 2) if c[end - 1] else 0,
               "tv": round(tv20 / 1e8, 1), "w": {}}
        best = 0
        for win in WINDOWS:
            r = screen_at(c, v, end, win)
            if not r: continue
            ratio, spike, chg, r2 = r
            row["w"][str(win)] = {"r": round(ratio, 2), "sd": int(d[spike]), "chg": round(chg, 2), "r2": round(r2, 2),
                                  "sc": round((c[end] / c[spike] - 1) * 100, 2), "sv": int(v[spike]),
                                  "av": int(v[spike - BASE:spike].mean())}
            best = max(best, ratio)
        if best < 2: continue   # 슬라이더 최소값 2배 이상만 저장
        if not any(-15 <= x["chg"] <= 15 for x in row["w"].values()): continue
        row["spark"] = [round(x, 0) for x in c[end - 29:end + 1]]
        row["sparkv"] = [int(x) for x in v[end - 29:end + 1]]
        results.append(row)
    print(f"후보 {len(results)}개", flush=True)

    # 공시/뉴스 체크 (20일 기준 급등일)
    def enrich(row):
        w = row["w"].get("20") or row["w"].get("40") or row["w"].get("10")
        disc, news, titles, flags = disclosures(row["code"], w["sd"])
        row["disc"], row["news"], row["titles"], row["flags"] = disc, news, titles, flags
        return row
    with ThreadPoolExecutor(12) as ex:
        results = list(ex.map(enrich, results))
    results = [r for r in results if not any("관리" in f or "정지" in f for f in r["flags"])]
    results.sort(key=lambda r: -max(x["r"] for x in r["w"].values()))

    # ---------- 백테스트 (20일 창, 평가일마다 스크리너 재적용) ----------
    win = 20
    H = {}
    recent = []
    def key(K, cap, fl, b, p): return f"{K}_{cap}_{fl}_{b}_{p}"
    for K in K_GRID:
        for cap in CAP_GRID:
            for fl in FLOOR_GRID:
                for b in BUCKETS:
                    for p in PERSIST:
                        H[key(K, cap, fl, b, p)] = {"n": 0, "first": 0, "f5": [], "f20": []}
    mkt = {b: [] for b in BUCKETS}
    for code, a in data.items():
        d, c, v = a[:, 0].astype(int), a[:, 4], a[:, 5]
        n = len(c)
        if meta[code]["cap"] < MIN_CAP or not clean(c, v): continue
        bk = bucket_of(meta[code]["cap"])
        prev_pass = {}
        for end in range(BASE + win, n - 20):
            r20 = (c[end + 20] / c[end] - 1) * 100
            if end % 5 == 0:
                mkt["all"].append(r20); mkt[bk].append(r20)
            r = screen_at(c, v, end, win)
            if not r: continue
            ratio, spike, chg, r2 = r
            if ratio < 2:
                prev_pass = {}
                continue
            r5 = (c[end + 5] / c[end] - 1) * 100
            persist_ok = r2 >= 1.5
            for K in K_GRID:
                if ratio < K: continue
                for cap in CAP_GRID:
                    for fl in FLOOR_GRID:
                        if cap == "up":
                            ok_c = chg > 3
                        else:
                            ok_c = fl <= chg <= cap
                        if not ok_c: continue
                        for p in PERSIST:
                            if p == 1 and not persist_ok: continue
                            for b in ("all", bk):
                                k = key(K, cap, fl, b, p)
                                H[k]["n"] += 1
                                if not prev_pass.get(k):
                                    H[k]["first"] += 1; H[k]["f5"].append(r5); H[k]["f20"].append(r20)
                                    if K == 3 and cap == 3 and fl == -5 and b == "all" and p == 0:
                                        recent.append({"code": code, "name": meta[code]["name"], "date": int(d[end]),
                                                       "r": round(ratio, 1), "chg": round(chg, 1),
                                                       "r5": round(r5, 1), "r20": round(r20, 1)})
                                prev_pass[k] = True
            # 이번 평가일에 통과 못한 키는 리셋
            for k in list(prev_pass):
                K, cap, fl, b, p = k.split("_")
                K = float(K); fl = int(fl); p = int(p)
                ok = ratio >= K and ((chg > 3) if cap == "up" else (fl <= chg <= int(cap))) and (p == 0 or persist_ok)
                if not ok: prev_pass.pop(k)
    bt = {"window": win, "grid": {}, "market": {}, "recent": sorted(recent, key=lambda x: -x["date"])[:60]}
    for b in BUCKETS:
        arr = np.array(mkt[b]) if mkt[b] else np.array([0.0])
        bt["market"][b] = {"avg20": round(float(arr.mean()), 2), "win20": round(float((arr > 0).mean() * 100), 1), "n": len(mkt[b])}
    board = []
    for k, h in H.items():
        if h["first"] == 0:
            bt["grid"][k] = {"n": 0}; continue
        f5, f20 = np.array(h["f5"]), np.array(h["f20"])
        g = {"n": h["n"], "first": h["first"],
             "favg5": round(float(f5.mean()), 2), "favg20": round(float(f20.mean()), 2),
             "fmed20": round(float(np.median(f20)), 2),
             "fwin5": round(float((f5 > 0).mean() * 100), 1), "fwin20": round(float((f20 > 0).mean() * 100), 1),
             "big20": round(float((f20 >= 10).mean() * 100), 1), "bad20": round(float((f20 <= -10).mean() * 100), 1)}
        bt["grid"][k] = g
        K, cap, fl, b, p = k.split("_")
        if h["first"] >= 30 and not (cap == "up" and fl == "-5"):   # 'up'은 하락제한 무의미 → 중복 제거
            board.append({"k": k, "K": K, "cap": cap, "fl": fl, "b": b, "p": p, "n": h["first"],
                          "avg20": g["favg20"], "win20": g["fwin20"],
                          "diff": round(g["favg20"] - bt["market"][b]["avg20"], 2)})
    board.sort(key=lambda x: -x["diff"])
    bt["board"] = board[:15] + [{"sep": True}] + board[-5:]

    json.dump([{"code": x["code"], "name": x["name"], "mk": x["mk"]} for x in stocks],
              open(os.path.join(OUT, "stocks.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(results, open(os.path.join(OUT, "result.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(bt, open(os.path.join(OUT, "backtest.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump({"updated": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M"),
               "lastDate": last_date, "universe": len(data), "candidates": len(results)},
              open(os.path.join(OUT, "meta.json"), "w"), ensure_ascii=False)
    print(f"완료 {time.time()-t0:.0f}s, 기준일 {last_date}, 후보 {len(results)}", flush=True)

if __name__ == "__main__":
    main()
