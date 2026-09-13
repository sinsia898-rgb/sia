# 오늘의집 상품 리뷰 크롤링

대상 예시: `https://store.ohou.se/goods/979406`

## 핵심 원리

오늘의집 상품 페이지의 리뷰는 HTML에 박혀 있지 않고, **리뷰 탭을 열 때 브라우저가
별도 JSON API를 호출해서** 채운다. 그래서 `requests.get(상품페이지)` + BeautifulSoup
방식은 빈 껍데기만 받는다. 리뷰를 얻는 길은 둘 중 하나다.

1. **그 JSON API를 직접 호출한다** ← 빠르고 깔끔. 기본 방법.
2. **브라우저를 띄워 페이지가 스스로 호출하게 하고 응답을 주워 모은다** ← 느리지만
   토큰·쿠키·봇차단에 안 걸린다. 폴백.

문제는 API 주소가 사이트 개편마다 바뀐다는 점이다. 그래서 이 도구는 **주소를 하드코딩하지
않고 매번 페이지에서 직접 찾아낸다.**

## 설치

```bash
pip install -r requirements.txt
playwright install chromium
```

## 사용법

### 1단계 — 리뷰 API 찾기

```bash
python discover_api.py https://store.ohou.se/goods/979406
```

브라우저를 띄워 리뷰 탭을 누르고 스크롤하면서 오가는 XHR/fetch 응답을 전부 검사한 뒤,
"리뷰처럼 생긴" JSON을 점수순으로 보여준다. 출력 예시:

```
[00] score=115  GET 200
     URL        : https://.../production_reviews.json?production_id=979406&page=1&per=20
     리스트 경로: $.reviews
     레코드 키  : contents, created_at, helped_count, id, images, option, star, user
     샘플       : {"id": 12345, "star": 5, "contents": "배송 빠르고…", "images": "<list len=2>"}
     원문       : discovery/candidate_00.json
```

눈으로 확인하려면 `--headful`을 붙인다. 후보 응답 원문은 `discovery/`에 저장되니
필드 이름을 직접 확인할 수 있다.

> 손으로 하려면: 크롬에서 상품 페이지 → F12 → **Network** 탭 → **Fetch/XHR** 필터 →
> 리뷰 탭 클릭 → 목록에서 응답에 리뷰 텍스트가 담긴 요청을 찾아 **Copy → Copy link address**.
> `discover_api.py`가 하는 일이 정확히 이것이다.

### 2단계 — 페이지 끝까지 수집

1단계 URL에서 **페이지 번호 값을 `{page}`로 바꿔서** 넘긴다.

```bash
python fetch_reviews.py \
  --url 'https://.../production_reviews.json?production_id=979406&page={page}&per=20' \
  --list-path '$.reviews' \
  --pages 100 --delay 1.5 \
  --out reviews_979406
```

결과물:

| 파일 | 내용 |
|---|---|
| `reviews_979406.csv` | 정규화된 표. 엑셀·구글시트에서 바로 열림 (UTF-8 BOM) |
| `reviews_979406.json` | 원본 레코드 전체. 필드 누락 없이 보관 |
| `reviews_979406_raw/` | 페이지별 원본 응답. 검증·재처리용 |

CSV 열: `review_id, created_at, rating, user, option, content, helpful_count, photo_count, photo_urls`

종료 조건은 자동이다 — 빈 페이지가 오거나, 신규 리뷰가 0건이면(= API가 `page`를 무시하는
경우) 멈춘다. `--pages`는 그 위의 안전장치다.

### 3단계 — 막히면 폴백

`fetch_reviews.py`가 403/401을 받거나 서명 파라미터가 필요해 보이면:

```bash
python crawl_browser.py https://store.ohou.se/goods/979406 --out reviews_979406
```

브라우저가 직접 페이징하고, 그 응답에서 리뷰만 걸러 같은 CSV/JSON을 만든다.

## CSV 열이 비어 있으면

오늘의집이 필드명을 바꾼 경우다. `fetch_reviews.py`가 실행 끝에 **열 채움률**을 찍어주니
비어 있는 열이 보이면 `reviews_979406.json`에서 실제 키 이름을 확인하고
`common.py`의 `FIELD_ALIASES`에 그 이름을 추가하면 된다.

```python
FIELD_ALIASES = {
    "rating": ("star", "star_avg", "rating", "score", ...),  # ← 여기에 추가
    ...
}
```

원본 JSON은 항상 통째로 저장되므로, 매핑을 고친 뒤 다시 수집할 필요 없이 재처리만 하면 된다.

## 검증 상태

- `common.py`의 인식·정규화 로직: `python test_common.py` — 7건 통과.
  (사진 배열을 리뷰 목록으로 착각하던 버그를 이 테스트로 잡았다)
- `fetch_reviews.py`의 페이징·중복제거·종료조건·CSV 출력: 로컬 목 API로 end-to-end 확인.
  47건 3페이지 정상 수집, `page`를 무시하는 API에서도 2페이지에서 안전 종료.
- **`ohou.se` 실제 응답으로는 검증하지 못했다.** 이 작업을 한 세션의 네트워크 정책이
  `ohou.se`/`store.ohou.se`를 차단해서다. 그래서 엔드포인트를 기억에 의존해 적어두는
  대신 1단계 자동 탐색 방식으로 설계했다. **로컬에서 1단계부터 실행하면 된다.**

## 주의

- `--delay`를 1초 미만으로 낮추지 말 것. 리뷰 1,000건이면 50페이지 = 1분 남짓이다.
  서버를 때릴 이유가 없다.
- 리뷰 작성자 닉네임·사진은 개인정보다. 사내 분석용으로만 쓰고 재배포하지 말 것.
- 수집 전 `https://ohou.se/robots.txt`와 이용약관을 한 번 확인할 것. 공개 페이지 조회라도
  약관상 제약이 있을 수 있다.
