#!/usr/bin/env python3
"""2단계: 찾아낸 리뷰 엔드포인트를 마지막 페이지까지 돌면서 CSV/JSON으로 저장한다.

사용법:
    # URL 안의 페이지 번호를 {page} 로 바꿔서 넘긴다
    python fetch_reviews.py \
        --url 'https://.../reviews?production_id=979406&page={page}&order=recent&per=20' \
        --list-path '$.reviews' --pages 100 --out reviews_979406

    # 페이지 파라미터만 알면 --page-param 으로 붙일 수도 있다
    python fetch_reviews.py --url 'https://.../reviews?production_id=979406' --page-param page

결과:
    reviews_979406.csv   정규화된 표 (엑셀/시트에서 바로 열림)
    reviews_979406.json  원본 레코드 전체 (필드 누락 없이 보관)
    reviews_979406_raw/  페이지별 원본 응답 (검증·재처리용)
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

from common import UA, dig, record_key, write_outputs


def build_url(template: str, page: int, page_param: str | None) -> str:
    if "{page}" in template:
        return template.replace("{page}", str(page))
    if page_param:
        sep = "&" if "?" in template else "?"
        return f"{template}{sep}{page_param}={page}"
    raise SystemExit("URL에 {page} 자리표시자가 없습니다. --page-param 으로 파라미터명을 지정하세요.")


def make_session(referer: str, cookie: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": referer,
        }
    )
    if cookie:
        session.headers["Cookie"] = cookie
    return session


def main() -> int:
    ap = argparse.ArgumentParser(description="오늘의집 리뷰 페이징 수집기")
    ap.add_argument("--url", required=True, help="리뷰 API URL ({page} 자리표시자 권장)")
    ap.add_argument("--list-path", help="discover_api.py 가 알려준 리스트 경로 (예: $.reviews)")
    ap.add_argument("--page-param", help="URL에 {page}가 없을 때 붙일 페이지 파라미터명")
    ap.add_argument("--start-page", type=int, default=1)
    ap.add_argument("--pages", type=int, default=50, help="최대 페이지 수 (안전장치)")
    ap.add_argument("--delay", type=float, default=1.2, help="요청 간 기본 대기 초")
    ap.add_argument("--referer", default="https://store.ohou.se/", help="Referer 헤더")
    ap.add_argument("--cookie", default="", help="로그인이 필요한 경우 브라우저 쿠키 문자열")
    ap.add_argument("--out", default="reviews", help="출력 파일 접두사")
    args = ap.parse_args()

    session = make_session(args.referer, args.cookie)
    raw_dir = Path(f"{args.out}_raw")
    raw_dir.mkdir(parents=True, exist_ok=True)

    all_records: list[dict] = []
    seen: set[str] = set()

    for page in range(args.start_page, args.start_page + args.pages):
        url = build_url(args.url, page, args.page_param)
        try:
            resp = session.get(url, timeout=20)
        except requests.RequestException as exc:
            print(f"[{page}] 요청 실패: {exc} — 5초 후 1회 재시도", file=sys.stderr)
            time.sleep(5)
            try:
                resp = session.get(url, timeout=20)
            except requests.RequestException as exc2:
                print(f"[{page}] 재시도 실패: {exc2} — 중단", file=sys.stderr)
                break

        if resp.status_code == 429:
            print(f"[{page}] 429 Too Many Requests — 30초 대기 후 같은 페이지 재시도", file=sys.stderr)
            time.sleep(30)
            resp = session.get(url, timeout=20)
        if resp.status_code >= 400:
            print(f"[{page}] HTTP {resp.status_code} — 중단", file=sys.stderr)
            (raw_dir / f"page_{page:04d}.err.txt").write_text(resp.text[:5000], encoding="utf-8")
            break

        try:
            payload = resp.json()
        except ValueError:
            print(f"[{page}] JSON이 아닌 응답 — 중단 (URL/헤더 확인)", file=sys.stderr)
            (raw_dir / f"page_{page:04d}.html").write_text(resp.text[:5000], encoding="utf-8")
            break

        (raw_dir / f"page_{page:04d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        records = dig(payload, args.list_path, url)
        if not records:
            print(f"[{page}] 리뷰 0건 — 마지막 페이지로 판단하고 종료")
            break

        new = 0
        for rec in records:
            key = record_key(rec)
            if key in seen:
                continue
            seen.add(key)
            all_records.append(rec)
            new += 1

        print(f"[{page}] {len(records)}건 수신 / 신규 {new}건 / 누적 {len(all_records)}건")
        if new == 0:
            print(f"[{page}] 신규 0건 — 같은 페이지가 반복됩니다. 페이지 파라미터를 확인하세요.")
            break

        time.sleep(args.delay + random.uniform(0, 0.6))

    if not all_records:
        print("수집된 리뷰가 없습니다.", file=sys.stderr)
        return 1

    write_outputs(all_records, args.out)
    print(f"원본 응답: {raw_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
