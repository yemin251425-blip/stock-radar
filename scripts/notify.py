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

K, CAP, FLOOR, WIN = 3, 3, -5, "20"
L = [dict(x, cur=x["w"][WIN]) for x in R if x["w"].get(WIN)]
L = [x for x in L if x["cur"]["r"] >= K and FLOOR <= x["cur"]["chg"] <= CAP]
L.sort(key=lambda x: -x["cur"]["r"])
quiet = [x for x in L if x["disc"] == 0 and x["news"] == 0]

d = str(M["lastDate"]); d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
msg = f"📊 거래량 레이더 {d}\n조건: 거래 {K}배↑ / 주가 +{CAP}%↓ / 1개월\n통과 {len(L)}종목 · 뉴스없음 {len(quiet)}\n\n"
for i, x in enumerate(L[:10], 1):
    c = x["cur"]; q = "🔕" if x["disc"] == 0 and x["news"] == 0 else "📰"
    f = "🔥" if c["r2"] >= 1.5 else ""
    sd = str(c["sd"])[4:6] + "/" + str(c["sd"])[6:]
    msg += f"{i}. {q}{f} {x['name']} {c['r']:.1f}배 ({c['chg']:+.1f}%) 폭발일 {sd}\n"
msg += "\n🔕 뉴스·공시 없음 · 📰 뉴스 있음 · 🔥 다음날도 거래 이어짐\nhttps://stock-radar-kr.netlify.app\n※ 투자 추천 아님, 데이터 조회용"
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
