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
CAP_GRID = [3, 5, 10]
FLOOR_GRID = [-5, -100]
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
    return float(ratios[i]), spike, float(chg)

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
            ratio, spike, chg = r
            row["w"][str(win)] = {"r": round(ratio, 2), "sd": int(d[spike]), "chg": round(chg, 2),
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
    for K in K_GRID:
        for cap in CAP_GRID:
            for fl in FLOOR_GRID:
                H[f"{K}_{cap}_{fl}"] = {"n": 0, "r5": [], "r20": [], "first": 0, "f5": [], "f20": []}
    for code, a in data.items():
        d, c, v = a[:, 0].astype(int), a[:, 4], a[:, 5]
        n = len(c)
        if meta[code]["cap"] < MIN_CAP or not clean(c, v): continue
        # 평가일: 20일 뒤 결과가 있는 날까지
        prev_pass = {k: False for k in H}
        for end in range(BASE + win, n - 20):
            r = screen_at(c, v, end, win)
            if not r: continue
            ratio, spike, chg = r
            if ratio < 2:
                for k in prev_pass: prev_pass[k] = False
                continue
            r5 = (c[end + 5] / c[end] - 1) * 100
            r20 = (c[end + 20] / c[end] - 1) * 100
            for K in K_GRID:
              for cap in CAP_GRID:
                for fl in FLOOR_GRID:
                    k = f"{K}_{cap}_{fl}"
                    ok = ratio >= K and fl <= chg <= cap
                    if ok:
                        H[k]["n"] += 1; H[k]["r5"].append(r5); H[k]["r20"].append(r20)
                        if not prev_pass[k]:
                            H[k]["first"] += 1; H[k]["f5"].append(r5); H[k]["f20"].append(r20)
                            if K == 3 and cap == 3 and fl == -5:
                                recent.append({"code": code, "name": meta[code]["name"], "date": int(d[end]),
                                               "r": round(ratio, 1), "chg": round(chg, 1),
                                               "r5": round(r5, 1), "r20": round(r20, 1)})
                    prev_pass[k] = ok
    bt = {"window": win, "grid": {}, "recent": sorted(recent, key=lambda x: -x["date"])[:60]}
    for k, h in H.items():
        if h["n"] == 0:
            bt["grid"][k] = {"n": 0}; continue
        r5, r20 = np.array(h["r5"]), np.array(h["r20"])
        f5, f20 = np.array(h["f5"]), np.array(h["f20"])
        bt["grid"][k] = {"n": h["n"], "first": h["first"],
                         "favg5": round(float(f5.mean()), 2), "favg20": round(float(f20.mean()), 2),
                         "fwin5": round(float((f5 > 0).mean() * 100), 1), "fwin20": round(float((f20 > 0).mean() * 100), 1),
                         "avg5": round(float(r5.mean()), 2), "avg20": round(float(r20.mean()), 2),
                         "med5": round(float(np.median(r5)), 2), "med20": round(float(np.median(r20)), 2),
                         "win5": round(float((r5 > 0).mean() * 100), 1), "win20": round(float((r20 > 0).mean() * 100), 1),
                         "big20": round(float((r20 >= 10).mean() * 100), 1), "bad20": round(float((r20 <= -10).mean() * 100), 1)}
    # 시장 평균(비교용): 아무 조건 없는 전 종목 20일 수익률
    allr = []
    for code, a in data.items():
        c, v = a[:, 4], a[:, 5]
        if meta[code]["cap"] < MIN_CAP or not clean(c, v): continue
        for end in range(BASE + win, len(c) - 20, 5):
            allr.append((c[end + 20] / c[end] - 1) * 100)
    allr = np.array(allr)
    bt["market"] = {"avg20": round(float(allr.mean()), 2), "win20": round(float((allr > 0).mean() * 100), 1), "n": len(allr)}

    json.dump(results, open(os.path.join(OUT, "result.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(bt, open(os.path.join(OUT, "backtest.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump({"updated": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M"),
               "lastDate": last_date, "universe": len(data), "candidates": len(results)},
              open(os.path.join(OUT, "meta.json"), "w"), ensure_ascii=False)
    print(f"완료 {time.time()-t0:.0f}s, 기준일 {last_date}, 후보 {len(results)}", flush=True)

if __name__ == "__main__":
    main()
