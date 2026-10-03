# -*- coding: utf-8 -*-
"""봇 토큰을 Supabase 함수에 등록하고 텔레그램 웹훅 연결 (매번 실행해도 안전)"""
import os, requests
TOKEN = os.environ.get("TG_TOKEN", "")
if not TOKEN:
    print("토큰 없음 → 건너뜀"); raise SystemExit(0)
r = requests.post("https://wqkskznhhublwasrodhv.supabase.co/functions/v1/radar/setup",
                  json={"token": TOKEN}, timeout=60)
print(r.status_code, r.text[:300])
