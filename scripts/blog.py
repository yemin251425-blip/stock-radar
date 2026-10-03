# -*- coding: utf-8 -*-
"""매일 블로그 글 자동 생성 → docs/data/blog.txt (텔레그램으로도 전송됨)"""
import json, os, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "docs", "data")
R = json.load(open(os.path.join(D, "result.json"), encoding="utf-8"))
B = json.load(open(os.path.join(D, "backtest.json"), encoding="utf-8"))
M = json.load(open(os.path.join(D, "meta.json"), encoding="utf-8"))

K, CAP, FLOOR, WIN, N = 3, 3, -5, "20", 7
BN = {"all": "전체", "s": "300억↓", "m": "300~1000억", "l": "1000억↑"}

def fd(x): x = str(x); return f"{x[4:6]}/{x[6:]}"
def cap_s(c): return f"{c/10000:.1f}조" if c >= 10000 else f"{c:,}억"
def pct(v): return f"{v:+.1f}%"

L = [dict(x, cur=x["w"][WIN]) for x in R if x["w"].get(WIN)]
L = [x for x in L if x["cur"]["r"] >= K and FLOOR <= x["cur"]["chg"] <= CAP]
L.sort(key=lambda x: -x["cur"]["r"])
quiet = [x for x in L if x["disc"] == 0 and x["news"] == 0]
hot = [x for x in L if x["cur"]["r2"] >= 1.5]
d = str(M["lastDate"]); d = f"{d[:4]}-{d[4:6]}-{d[6:]}"

title = f"[{d}] 거래량은 터졌는데 주가는 조용한 종목 {min(N, len(L))}선 — 뉴스 없는 종목 {len(quiet)}개"
s = title + "\n\n"
s += f"📊 오늘의 요약\n"
s += f"코스피·코스닥 {M['universe']:,}종목 중 \"최근 1개월 안에 평소보다 거래량이 {K}배 이상 터졌는데, 주가는 +{CAP}%도 안 오른(그리고 -5%보다 덜 빠진)\" 종목은 {len(L)}개였습니다. 그중 폭발일 전후로 뉴스도 공시도 없는 '이유 없는 거래량'은 {len(quiet)}개, 폭발 다음날까지 거래가 이어진 종목은 {len(hot)}개입니다.\n\n"
s += "누군가 물량을 조용히 받아내고 있을 수도 있고, 반대로 큰손이 털고 나가는 중일 수도 있습니다. 참고용 데이터일 뿐 종목 추천이 아닙니다.\n\n"
s += f"🔥 거래 폭발 TOP {min(N, len(L))}\n\n"
for i, x in enumerate(L[:N], 1):
    c = x["cur"]
    s += f"{i}. {x['name']} ({x['mk']}, 시총 {cap_s(x['cap'])})\n"
    s += f"· 폭발일 {fd(c['sd'])}: 평소 {c['av']:,}주 → {c['sv']:,}주 ({c['r']:.1f}배)\n"
    s += f"· 1개월 주가 {pct(c['chg'])} / 현재가 {int(x['close']):,}원\n"
    nd = "아직 모름" if c["r2"] < 0 else (f"평소의 {c['r2']:.1f}배로 이어짐 🔥" if c["r2"] >= 1.5 else f"평소의 {c['r2']:.1f}배로 조용해짐")
    s += f"· 다음날 거래: {nd}\n"
    if x["disc"] == 0 and x["news"] == 0:
        s += "· 폭발일 전후 뉴스·공시 없음 (이유 없는 거래량)\n"
    else:
        t = (x.get("titles") or [""])[0]
        s += f"· 뉴스 {x['news']}건·공시 {x['disc']}건" + (f" — {t}" if t else "") + "\n"
    s += "\n"
# 백테스트 한 줄
g = B["grid"].get("3_3_-5_all_0", {}); mk = B["market"]["all"]
if g.get("n"):
    s += "📐 이 조건, 과거엔 통했나?\n"
    s += f"지난 약 6개월 동안 같은 조건으로 골라서 20일 들고 있었다면 평균 {pct(g['favg20'])} (오른 비율 {g['fwin20']}%), 같은 기간 시장 전체 평균은 {pct(mk['avg20'])}였습니다. "
    diff = g["favg20"] - mk["avg20"]
    s += ("조건 자체는 시장보다 조금 좋았습니다." if diff > 1 else "조건 자체만으로는 시장을 이기지 못했습니다. '거래 폭발 + 안 오름'이 항상 매집은 아니라는 뜻입니다." if diff < -1 else "시장과 거의 차이가 없었습니다.") + "\n"
    board = [x for x in B.get("board", []) if not x.get("sep")][:3]
    if board:
        s += "같은 기간 제일 잘 통한 조합: " + " / ".join(
            f"{x['K']}배↑·{'이미 +3% 오름' if x['cap']=='up' else '+'+x['cap']+'%↓'}·{BN[x['b']]}{'·다음날🔥' if x['p']=='1' else ''} ({x['diff']:+.1f}%p)" for x in board) + "\n"
    s += "\n"
s += "📲 매일 저녁 6시 거래량 폭발 종목 알림 받기 → t.me/sogeum_radar_bot\n"
s += "🔎 조건 직접 바꿔보기 (슬라이더) → stock-radar-kr.netlify.app\n\n"
s += "※ 네이버 증권 공개 데이터를 조건대로 걸러낸 결과일 뿐 종목 추천이 아닙니다. 투자 판단과 책임은 본인에게 있습니다.\n"
s += "#거래량 #거래량급증 #주식스크리너 #세력매집 #코스닥 #코스피 #주린이"

open(os.path.join(D, "blog.txt"), "w", encoding="utf-8").write(s)
print("blog.txt", len(s), "chars")
