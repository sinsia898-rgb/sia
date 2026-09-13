#!/usr/bin/env python3
"""1단계: 오늘의집 상품 페이지가 실제로 호출하는 리뷰 API를 자동으로 찾아낸다.

리뷰 API 주소는 사이트 개편 때마다 바뀐다. 주소를 외워 쓰는 대신 브라우저를 띄워
리뷰 영역을 열고, 그때 오가는 XHR/fetch 응답 중 "리뷰처럼 생긴" JSON을 골라낸다.

사용법:
    python discover_api.py https://store.ohou.se/goods/979406
    python discover_api.py https://store.ohou.se/goods/979406 --headful   # 눈으로 확인
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import Response, sync_playwright

from common import UA, best_list, summarize_record

REVIEW_TAB_LABELS = ("리뷰", "후기", "review")
NEXT_LABELS = ("다음", "더보기", "더 보기", "next")


def discover(url: str, headful: bool, out_dir: Path, wait_ms: int) -> list[dict]:
    captured: list[dict] = []

    def on_response(resp: Response) -> None:
        if "json" not in (resp.headers or {}).get("content-type", "").lower():
            return
        if resp.request.resource_type not in ("xhr", "fetch"):
            return
        try:
            payload = resp.json()
        except Exception:
            return
        score, path, records = best_list(payload, resp.url)
        if score <= 0:
            return
        keys: set[str] = set()
        for rec in records[:5]:
            keys |= set(rec.keys())
        captured.append(
            {
                "score": score,
                "method": resp.request.method,
                "status": resp.status,
                "url": resp.url,
                "list_path": path,
                "record_keys": sorted(keys),
                "sample": summarize_record(records[0]) if records else {},
                "post_data": resp.request.post_data,
                "payload": payload,
            }
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        ctx = browser.new_context(user_agent=UA, locale="ko-KR", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        # 리뷰 영역은 보통 탭을 눌러야, 또는 뷰포트에 들어와야 호출된다.
        for label in REVIEW_TAB_LABELS:
            try:
                tab = page.get_by_text(re.compile(label, re.I)).first
                if tab.count() and tab.is_visible():
                    tab.click(timeout=3_000)
                    page.wait_for_timeout(2_000)
                    break
            except Exception:
                continue

        for _ in range(12):  # 지연 로딩 / 무한 스크롤 유도
            page.mouse.wheel(0, 1_600)
            page.wait_for_timeout(600)

        # 다음 페이지를 한 번 눌러서 페이징 파라미터를 노출시킨다.
        for label in NEXT_LABELS:
            try:
                btn = page.get_by_role("button", name=re.compile(label, re.I)).first
                if btn.count() and btn.is_visible():
                    btn.click(timeout=3_000)
                    page.wait_for_timeout(2_500)
                    break
            except Exception:
                continue

        page.wait_for_timeout(wait_ms)
        browser.close()

    captured.sort(key=lambda c: c["score"], reverse=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, cap in enumerate(captured[:10]):
        (out_dir / f"candidate_{i:02d}.json").write_text(
            json.dumps(cap["payload"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return captured


def main() -> int:
    ap = argparse.ArgumentParser(description="오늘의집 리뷰 API 엔드포인트 탐색")
    ap.add_argument("url", help="상품 페이지 URL (예: https://store.ohou.se/goods/979406)")
    ap.add_argument("--headful", action="store_true", help="브라우저 창을 띄워 동작 확인")
    ap.add_argument("--out-dir", default="discovery", help="후보 응답 원문 저장 위치")
    ap.add_argument("--wait-ms", type=int, default=3_000, help="마지막 대기 시간(ms)")
    args = ap.parse_args()

    caps = discover(args.url, args.headful, Path(args.out_dir), args.wait_ms)
    if not caps:
        print(
            "리뷰처럼 보이는 JSON 응답을 찾지 못했습니다.\n"
            " - --headful 로 실행해 리뷰 탭이 실제로 열리는지 확인하세요.\n"
            " - 리뷰가 서버 렌더링(HTML)이라면 crawl_browser.py 폴백을 쓰세요.",
            file=sys.stderr,
        )
        return 1

    print(f"\n리뷰 API 후보 {len(caps)}건 (점수 높은 순)\n" + "=" * 72)
    for i, cap in enumerate(caps[:10]):
        print(f"\n[{i:02d}] score={cap['score']}  {cap['method']} {cap['status']}")
        print(f"     URL        : {cap['url']}")
        print(f"     리스트 경로: {cap['list_path']}")
        print(f"     레코드 키  : {', '.join(cap['record_keys'])}")
        if cap["post_data"]:
            print(f"     POST body  : {cap['post_data'][:300]}")
        print("     샘플       : " + json.dumps(cap["sample"], ensure_ascii=False)[:500])
        print(f"     원문       : {args.out_dir}/candidate_{i:02d}.json")

    top = caps[0]
    print("\n" + "=" * 72)
    print("2단계: 위 [00] URL에서 페이지 번호 값을 {page} 로 바꿔 아래처럼 실행하세요.")
    print(f"  python fetch_reviews.py --url '{top['url']}' --list-path '{top['list_path']}' --pages 100")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
