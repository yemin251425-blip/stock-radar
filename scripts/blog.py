# -*- coding: utf-8 -*-
"""매일 블로그 글 자동 생성 → docs/data/blog.txt (텔레그램으로도 전송됨). 쉬운 말 버전."""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "docs", "data")
R = json.load(open(os.path.join(D, "radar.json"), encoding="utf-8"))

def fdl(x): x = str(x); return f"{x[:4]}-{x[4:6]}-{x[6:]}"
def pct(v): return f"{v:+.1f}%"
def cap_s(c): return f"{c/10000:.1f}조" if c >= 10000 else f"{c:,}억"

w, r, bt = R["weather"], R["rule"], R["backtest"]
p = bt["portfolio"]; m = bt["market"]
sigs = " / ".join(f"{r['signals'][k]['icon']} {r['signals'][k]['name']}" for k in r["byWeather"][w["key"]])
wx_txt = {"cold": "주식 대부분이 내리는 중이에요. 이럴 땐 '한 달 바닥인데 사람 몰리는 주식'을 봐요.",
          "mid": "오르는 것 반, 내리는 것 반이에요. '반년 만에 최고가 넘긴 주식'과 '조용히 거래 터진 주식'을 봐요.",
          "hot": "주식 대부분이 오르는 중이에요. 욕심 금물, '반년 만에 최고가 넘긴 주식'만 조심해서 봐요."}[w["key"]]

d = fdl(R["date"])
title = f"[{d}] 오늘 시장 날씨 {w['icon']}{w['name']} — 사볼까? 종목 {len(R['picks'])}개 (2년 검증 규칙)"
s = title + "\n\n"
s += f"🌤 오늘 시장 날씨: {w['icon']} {w['name']}\n"
s += f"코스피·코스닥 {R['universe']:,}개 회사 중 {w['pct']}%가 요즘 평균(20일)보다 비싼 가격이에요. {wx_txt}\n\n"
s += f"📏 오늘의 규칙\n{sigs} → 내일 장 마감 가격에 사기 → {r['hold']}일 들고 있기 → 15% 오르면 바로 팔기 → 한 번에 {r['slots']}개\n\n"
if R["picks"]:
    s += f"🛍 사볼까? (규칙에 맞는 {R['candidates']}개 중 거래 많은 순 {len(R['picks'])}개)\n\n"
    for i, x in enumerate(R["picks"], 1):
        s += f"{i}. {x['name']} ({x['mk']}, 시총 {cap_s(x['cap'])}) — {x['icon']} {x['sigName']}\n"
        s += f"· 오늘 {x['close']:,}원 ({pct(x['d1'])}) / 한 달 {pct(x['chg20'])} / 오늘 거래 평소의 {x['vr']}배\n"
        s += f"· 15% 목표가 {x['target']:,}원 / 늦어도 12거래일 뒤 팔기\n"
        if x.get("news") or x.get("disc"):
            t = (x.get("titles") or [""])[0]
            s += f"· 뉴스 {x['news']}건·공시 {x['disc']}건" + (f" — {t}" if t else "") + "\n"
        else:
            s += "· 최근 뉴스·공시 없음\n"
        s += "\n"
    if R["bench"]:
        s += "예비 후보: " + ", ".join(x["name"] for x in R["bench"][:5]) + "\n\n"
else:
    s += "🛍 오늘은 규칙에 맞는 종목이 없어요. 이런 날은 쉬는 게 규칙이에요.\n\n"
s += "📐 이 규칙, 과거엔 어땠나? (2년치 과거 시험)\n"
s += f"{fdl(bt['period']['from'])}~{fdl(bt['period']['to'])}에 1,000만 원으로 이 규칙대로 3종목씩 사고팔았다면 {pct(p['ret'])} ({p['final']:,}원). 같은 기간 아무 주식이나 다 들고 있었으면 {pct(m['ret'])}. "
s += f"{p['trades']}번 사고팔아 {p['win']}% 이겼고, 제일 많이 까졌을 땐 {p['mdd']}%였어요. 종목 고르는 운에 따라 {pct((bt['randomFinal']['min']/1e7-1)*100)}~{pct((bt['randomFinal']['max']/1e7-1)*100)}까지 달라졌어요.\n"
s += "좋은 달 몇 개가 대부분을 벌고 나쁜 달이 연달아 오기도 하니, 처음엔 꼭 연습 장부로 1~2달 해보세요.\n\n"
s += "왜 이 규칙이냐면: 2,300개 회사 2년치를 11가지 방법으로 시험해봤는데, '거래량 터진 주식'은 그 자체론 아무 효과가 없었고, 짧게(1~5일) 사고파는 건 전부 손해였고, 떨어지면 파는 손절도 전부 손해였어요. 남은 게 이 규칙이에요.\n\n"
s += "📲 매일 저녁 6시 알림 → t.me/sogeum_radar_bot\n"
s += "🔎 오늘 날씨·종목·연습 장부 → stock-radar-kr.netlify.app\n\n"
s += "※ 네이버 증권 공개 데이터를 규칙대로 걸러낸 결과일 뿐 종목 추천이 아닙니다. 과거 결과가 미래를 보장하지 않습니다. 투자 판단과 책임은 본인에게 있습니다.\n"
s += "#주식 #코스닥 #코스피 #신고가 #거래량 #백테스트 #주린이 #주식공부"

open(os.path.join(D, "blog.txt"), "w", encoding="utf-8").write(s)
print("blog.txt", len(s), "chars")
