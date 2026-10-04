# -*- coding: utf-8 -*-
"""텔레그램(소유자): ① 오늘 TOP10 요약 ② 블로그 글 전문 (복사용)"""
import json, os, requests

TOKEN = os.environ.get("TG_TOKEN", "")
CHAT = os.environ.get("TG_CHAT", "")
if not TOKEN or not CHAT:
    print("텔레그램 토큰 없음 → 건너뜀"); raise SystemExit(0)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "docs", "data")
R = json.load(open(os.path.join(D, "result.json"), encoding="utf-8"))
M = json.load(open(os.path.join(D, "meta.json"), encoding="utf-8"))

def send(text):
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": CHAT, "text": text, "disable_web_page_preview": True}, timeout=20)
    print(r.status_code, r.text[:120]); return r.ok

RD = json.load(open(os.path.join(D, "radar.json"), encoding="utf-8"))
w, r = RD["weather"], RD["rule"]
d = str(RD["date"]); d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
sigs = " / ".join(f"{r['signals'][k]['icon']}{r['signals'][k]['name']}" for k in r["byWeather"][w["key"]])
msg = f"🌤 거래량 레이더 {d}\n오늘 시장 날씨: {w['icon']} {w['name']} ({w['pct']}%가 평균보다 비쌈)\n규칙: {sigs} → 내일 마감가 매수 → {r['hold']}일 보유 → 15%↑ 즉시 매도\n\n"
if RD["picks"]:
    msg += f"🛍 사볼까? ({RD['candidates']}개 중 {len(RD['picks'])}개)\n"
    for i, x in enumerate(RD["picks"], 1):
        q = "🔕" if not (x.get("news") or x.get("disc")) else "📰"
        msg += f"{i}. {q} {x['name']} {x['close']:,}원 ({x['d1']:+.1f}%) {x['icon']} → 목표 {x['target']:,}\n"
    if RD["bench"]: msg += "예비: " + ", ".join(x["name"] for x in RD["bench"][:5]) + "\n"
else:
    msg += "🛍 오늘은 규칙에 맞는 종목 없음 → 쉬는 날\n"
# 예전 방식(거래량 급증) 상위 5개도 참고로
K, CAP, FLOOR, WIN = 3, 3, -5, "20"
L = [dict(x, cur=x["w"][WIN]) for x in R if x["w"].get(WIN)]
L = [x for x in L if x["cur"]["r"] >= K and FLOOR <= x["cur"]["chg"] <= CAP]
L.sort(key=lambda x: -x["cur"]["r"])
if L:
    msg += "\n📊 참고) 거래량 급증 TOP5\n" + "\n".join(f"· {x['name']} {x['cur']['r']:.1f}배 ({x['cur']['chg']:+.1f}%)" for x in L[:5]) + "\n"
msg += "\nhttps://stock-radar-kr.netlify.app\n※ 투자 추천 아님, 데이터 조회용"
send(msg)

# 블로그 글 (4000자 단위로 나눠서)
bp = os.path.join(D, "blog.txt")
if os.path.exists(bp):
    blog = open(bp, encoding="utf-8").read()
    send("✍️ 오늘 블로그 글 (아래 내용 복사해서 네이버 블로그에 붙여넣기)")
    chunks, cur = [], ""
    for line in blog.split("\n"):
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur); cur = ""
        cur += line + "\n"
    if cur: chunks.append(cur)
    for ch in chunks: send(ch)
