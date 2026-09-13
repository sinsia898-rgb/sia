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

### 3단계 — 별점·키워드 분석

```bash
python analyze.py reviews_979406.json --html report_979406.html \
  --title '3인 소파 리뷰 분석'
```

터미널에 요약이 나오고, `--html`을 주면 그대로 공유 가능한 리포트 파일이 나온다.
`.json`(원본)과 `.csv`(정규화) 둘 다 입력으로 받는다.

분석 항목:

| 항목 | 무엇을 알려주나 |
|---|---|
| 별점 분포 · 1~2점 비중 | 지금 상태. 평균 하나로는 안 보이는 양극화가 드러난다 |
| 월별 추이 | 리뷰 수와 평균 별점의 변화. 개선/악화 시점을 짚는다 |
| **속성별 언급·평균 별점** | 배송·조립·마감·쿠션감·냄새 등 11개 속성별 언급량과 평균 별점 |
| 자주 나온 키워드 | 문서빈도 상위 표현 (한 리뷰에서 반복해도 1건) |
| **고평점 vs 저평점 변별 키워드** | 칭찬/불만으로 갈리는 말을 통계적으로 분리 |
| 옵션별 | 어떤 색상·사이즈가 특히 불만이 많은지 |
| 사진 유무별 | 포토리뷰가 실제로 더 후한지 |
| 상세 리뷰 발췌 | 고·저평점에서 정보량이 많은 원문 |

가장 실용적인 건 **속성별 표**다. 전체 평균 대비 낮은 순으로 정렬되므로 위에서부터
개선 우선순위로 읽으면 된다. 예: `냄새 23건(9.6%) 평균 2.09 전체대비 -1.87` → 냄새를
언급한 리뷰는 평균보다 1.87점 낮다.

**변별 키워드**는 단순 빈도가 아니라 사전 보정 로그오즈비(Monroe et al. 2008)를 쓴다.
단순 빈도로 비교하면 "소파", "구매" 같은 양쪽 공통어가 상위를 차지하고, 한 번 나온
희귀어가 극단값을 갖는다. 이 방식은 전체 말뭉치를 사전분포로 써서 둘 다 눌러준다.

속성 사전은 소파·가구 기준으로 짜여 있다. 다른 카테고리를 볼 땐
`korean_text.py`의 `ASPECTS`를 고치면 된다.

#### 키워드 품질 올리기 (선택)

```bash
pip install kiwipiepy
```

설치돼 있으면 자동으로 형태소 분석을 쓴다. 없으면 조사·어미를 잘라내는 휴리스틱으로
동작하는데, `빨라서 좋았` 처럼 어간이 덜 정리된 표현이 섞인다. 읽는 데 지장은 없지만
형태소 분석 쪽이 확실히 깔끔하다.

### 4단계 — 막히면 폴백

`fetch_reviews.py`가 403/401을 받거나 서명 파라미터가 필요해 보이면:

```bash
python crawl_browser.py https://store.ohou.se/goods/979406 --out reviews_979406
```

브라우저가 직접 페이징하고, 그 응답에서 리뷰만 걸러 같은 CSV/JSON을 만든다.
결과물 형식이 같으므로 `analyze.py`를 그대로 이어서 쓰면 된다.

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

## 파일

| 파일 | 역할 |
|---|---|
| `discover_api.py` | 1단계. 리뷰 API 엔드포인트 탐색 |
| `fetch_reviews.py` | 2단계. 페이징 수집 → CSV/JSON |
| `analyze.py` | 3단계. 별점·키워드 분석 → 터미널 + HTML |
| `crawl_browser.py` | 폴백. 브라우저 구동형 수집 |
| `common.py` | 리뷰 목록 인식·필드 정규화 공통 로직 |
| `korean_text.py` | 한국어 토큰화, 속성 사전, 변별 키워드 통계 |
| `test_common.py` / `test_analysis.py` | 테스트 |

## 검증 상태

```bash
python test_common.py && python test_analysis.py
```

- `common.py` 인식·정규화: 7건 통과. (사진 배열을 리뷰 목록으로 착각하던 버그를 잡았다)
- `korean_text.py` / `analyze.py`: 16건 통과. (반개 별점 4.5가 은행가 반올림으로 4점 칸에
  들어가던 버그를 잡았다)
- `fetch_reviews.py` 페이징·중복제거·종료조건·CSV: 로컬 목 API로 end-to-end 확인.
  47건 3페이지 정상 수집, `page`를 무시하는 API에서도 2페이지에서 안전 종료.
- `analyze.py`: 합성 리뷰 240건으로 전체 파이프라인 확인. 속성 분석이 심어둔 불만
  요인(냄새·CS·크기)을 상위로, 변별 키워드가 긍/부정을 정확히 분리했다.
- `kiwipiepy` 형태소 경로는 이 컨테이너에서 모델 휠 빌드가 실패해 스텁으로만 검증했다.
- **`ohou.se` 실제 응답으로는 검증하지 못했다.** 이 작업을 한 세션의 네트워크 정책이
  `ohou.se`/`store.ohou.se`를 차단해서다. 그래서 엔드포인트를 기억에 의존해 적어두는
  대신 1단계 자동 탐색 방식으로 설계했다. **로컬에서 1단계부터 실행하면 된다.**

## 주의

- `--delay`를 1초 미만으로 낮추지 말 것. 리뷰 1,000건이면 50페이지 = 1분 남짓이다.
  서버를 때릴 이유가 없다.
- 리뷰 작성자 닉네임·사진은 개인정보다. 사내 분석용으로만 쓰고 재배포하지 말 것.
- 수집 전 `https://ohou.se/robots.txt`와 이용약관을 한 번 확인할 것. 공개 페이지 조회라도
  약관상 제약이 있을 수 있다.
