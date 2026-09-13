#!/usr/bin/env python3
"""폴백: API 주소를 몰라도 브라우저를 직접 몰아서 리뷰를 모은다.

fetch_reviews.py 가 막히는 경우(서명/토큰 파라미터, 쿠키 필수, 봇 차단)에 쓴다.
요청을 우리가 만드는 게 아니라 실제 페이지가 보내게 하고, 오가는 응답에서
리뷰 JSON만 주워 모으므로 인증·헤더 문제가 생기지 않는다. 대신 느리다.

사용법:
    python crawl_browser.py https://store.ohou.se/goods/979406 --out reviews_979406
    python crawl_browser.py https://store.ohou.se/goods/979406 --headful   # 동작 확인
"""

from __future__ import annotations

import argparse
import re

from playwright.sync_api import Response, sync_playwright

from common import REVIEW_KEY_HINTS, UA, best_list, record_key, write_outputs

NEXT_LABELS = ("다음", "더보기", "더 보기", "리뷰 더보기", "next")
STALL_LIMIT = 5


def looks_like_reviews(records: list[dict]) -> bool:
    keys: set[str] = set()
    for rec in records[:5]:
        keys |= set(rec.keys())
    return len(keys & REVIEW_KEY_HINTS) >= 3


def main() -> int:
    ap = argparse.ArgumentParser(description="브라우저 구동형 리뷰 수집 (폴백)")
    ap.add_argument("url", help="상품 페이지 URL")
    ap.add_argument("--max-steps", type=int, default=80, help="다음 클릭/스크롤 최대 횟수")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--out", default="reviews_browser", help="출력 파일 접두사")
    ap.add_argument("--settle-ms", type=int, default=1_500, help="한 단계마다 대기 시간(ms)")
    args = ap.parse_args()

    collected: list[dict] = []
    seen: set[str] = set()

    def on_response(resp: Response) -> None:
        if "json" not in (resp.headers or {}).get("content-type", "").lower():
            return
        if resp.request.resource_type not in ("xhr", "fetch"):
            return
        try:
            payload = resp.json()
        except Exception:
            return
        records = best_list(payload, resp.url)[2]
        if not records or not looks_like_reviews(records):
            return
        for rec in records:
            key = record_key(rec)
            if key not in seen:
                seen.add(key)
                collected.append(rec)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        ctx = browser.new_context(user_agent=UA, locale="ko-KR", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.on("response", on_response)
        page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)

        for label in ("리뷰", "후기"):
            try:
                tab = page.get_by_text(re.compile(label)).first
                if tab.count() and tab.is_visible():
                    tab.click(timeout=3_000)
                    page.wait_for_timeout(args.settle_ms)
                    break
            except Exception:
                continue

        stalled = 0
        for step in range(args.max_steps):
            before = len(collected)

            clicked = False
            for label in NEXT_LABELS:
                try:
                    btn = page.get_by_role("button", name=re.compile(label, re.I)).first
                    if btn.count() and btn.is_visible() and btn.is_enabled():
                        btn.click(timeout=3_000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                page.mouse.wheel(0, 2_000)

            page.wait_for_timeout(args.settle_ms)
            gained = len(collected) - before
            print(f"[{step + 1}] {'클릭' if clicked else '스크롤'} / 신규 {gained}건 / 누적 {len(collected)}건")

            stalled = stalled + 1 if gained == 0 else 0
            if stalled >= STALL_LIMIT:
                print(f"{STALL_LIMIT}회 연속 신규 없음 — 끝까지 본 것으로 판단하고 종료")
                break

        browser.close()

    if not collected:
        print("리뷰를 찾지 못했습니다. --headful 로 실행해 리뷰 영역이 열리는지 확인하세요.")
        return 1

    write_outputs(collected, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
