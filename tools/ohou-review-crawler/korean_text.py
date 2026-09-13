"""리뷰 한국어 텍스트 처리: 토큰화, 속성 사전, 변별 키워드 통계.

형태소 분석기 없이도 동작한다. `pip install kiwipiepy` 가 되어 있으면 자동으로
형태소 분석을 쓰고, 없으면 조사·어미를 잘라내는 휴리스틱으로 처리한다.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, Sequence

# ─────────────────────────────── 토큰화 ───────────────────────────────

TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")

# 길이가 긴 것부터 자른다 ("에서" 를 "서" 보다 먼저).
PARTICLES = (
    "에서는", "에서도", "으로는", "으로도", "이라는", "라는", "이라고", "라고",
    "까지", "부터", "처럼", "보다", "에서", "에게", "한테", "으로", "밖에", "조차", "마저",
    "이나", "라도", "이란", "이든",
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "로", "나",
)
ENDINGS = (
    "했습니다", "있습니다", "없습니다", "합니다", "했어요", "하네요", "했는데",
    "같아요", "같습니다", "해주셔서", "주셔서", "더라구요", "거예요",
    "있어요", "없어요", "이에요", "예요", "입니다", "네요", "군요", "구요",
    "해요", "해서", "하고", "하니", "하는", "지만", "는데", "은데",
    "어요", "아요", "어서", "아서", "라서", "습니다", "했다", "한다", "이다",
)

STOPWORDS = {
    "그리고", "그래서", "하지만", "그런데", "그냥", "정말", "진짜", "너무", "아주", "매우",
    "조금", "약간", "완전", "엄청", "많이", "잘못", "제가", "저는", "저희", "우리", "제품",
    "구매", "주문", "리뷰", "후기", "사진", "생각", "같이", "이번", "다음", "처음", "지금",
    "때문", "정도", "부분", "경우", "이거", "그거", "저거", "여기", "거기", "이런", "그런",
    "어떤", "무슨", "하나", "하는", "한번", "번째", "이제", "아직", "역시", "일단", "혹시",
    "근데", "그것", "이것", "좋은", "있는", "없는", "되는", "해서", "위해", "통해", "대해",
    "해요", "했어", "네요", "이라", "라서", "에요", "이나", "들이", "에는", "으로", "만족",
}

_KIWI = None
_KIWI_TRIED = False
# 내용어만 남긴다: 명사·동사·형용사·부사·외국어
_KIWI_TAGS = ("NNG", "NNP", "VV", "VA", "MAG", "SL", "XR")


def _kiwi():
    global _KIWI, _KIWI_TRIED
    if not _KIWI_TRIED:
        _KIWI_TRIED = True
        try:
            from kiwipiepy import Kiwi  # type: ignore

            _KIWI = Kiwi()
        except Exception:
            _KIWI = None
    return _KIWI


def kiwi_available() -> bool:
    return _kiwi() is not None


def strip_affixes(token: str) -> str:
    """조사·어미를 한 번 잘라낸다. 어간이 2자 미만으로 줄어들면 자르지 않는다."""
    for suffix in ENDINGS + PARTICLES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def tokenize(text: str, use_morph: bool = True) -> list[str]:
    """리뷰 한 건 → 내용어 토큰 리스트."""
    if not text:
        return []
    if use_morph and (kiwi := _kiwi()) is not None:
        tokens = []
        for tok in kiwi.tokenize(text):
            if tok.tag in _KIWI_TAGS and len(tok.form) >= 2:
                form = tok.form
                # 동사·형용사는 어간에 '다'를 붙여 읽기 쉽게
                if tok.tag in ("VV", "VA"):
                    form += "다"
                tokens.append(form)
        return [t for t in tokens if t not in STOPWORDS]

    tokens = [strip_affixes(t) for t in TOKEN_RE.findall(text)]
    return [t for t in tokens if len(t) >= 2 and t not in STOPWORDS]


def ngrams(tokens: Sequence[str], n: int) -> list[str]:
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def doc_features(text: str, use_morph: bool = True, bigrams: bool = True) -> list[str]:
    """리뷰 한 건의 특징: 단어 + 두 단어 연쇄."""
    tokens = tokenize(text, use_morph)
    feats = list(tokens)
    if bigrams:
        feats += ngrams(tokens, 2)
    return feats


# ─────────────────────── 가구 리뷰 속성(아스펙트) 사전 ───────────────────────
# 원문에 부분문자열로 매칭한다. 한국어는 어간이 앞에 오므로 "배송"이
# "배송이/배송은/배송기사" 를 모두 잡는다.

ASPECTS: dict[str, tuple[str, ...]] = {
    "배송·물류": ("배송", "택배", "기사님", "발송", "도착", "출고", "배달"),
    "조립·설치": ("조립", "설치", "나사", "부품", "설명서", "매뉴얼", "시공"),
    "마감·품질": ("마감", "박음질", "봉제", "스티치", "흠집", "스크래치", "하자", "불량", "터짐", "뜯"),
    "쿠션감·착좌": ("쿠션", "푹신", "딱딱", "탄탄", "꺼짐", "주저", "등받이", "허리", "편안", "앉"),
    "소재·촉감": ("가죽", "패브릭", "극세사", "촉감", "재질", "소재", "벨벳", "천"),
    "색상·디자인": ("색상", "컬러", "디자인", "예쁘", "이쁘", "인테리어", "분위기", "고급"),
    "냄새": ("냄새", "새집", "환기", "화학", "향"),
    "크기·공간": ("사이즈", "크기", "치수", "폭", "길이", "공간", "좁", "넓", "커서", "작아"),
    "가격·가성비": ("가격", "가성비", "저렴", "비싸", "할인", "값"),
    "고객응대·CS": ("문의", "상담", "응대", "교환", "반품", "환불", "연락", "AS"),
    "반려동물·아이": ("아이", "아기", "고양이", "강아지", "반려"),
}


def aspect_hits(text: str) -> list[str]:
    """리뷰 한 건이 언급한 속성들."""
    if not text:
        return []
    return [name for name, kws in ASPECTS.items() if any(kw in text for kw in kws)]


# ─────────────────── 변별 키워드: 사전 보정 로그오즈비 ───────────────────


def log_odds_ratio(
    counts_a: Counter[str],
    counts_b: Counter[str],
    prior: Counter[str] | None = None,
    alpha: float = 0.01,
) -> dict[str, float]:
    """두 집단을 가장 잘 가르는 단어를 z-점수로 돌려준다.

    Monroe et al. (2008) 의 informative Dirichlet prior 방식. 단순 빈도 비교는
    희귀어가 극단값을 차지해 쓸 수 없으므로, 전체 말뭉치를 사전분포로 써서
    빈도가 낮은 단어의 점수를 그만큼 눌러준다.

    양수 = A집단(예: 고평점) 특징어, 음수 = B집단(예: 저평점) 특징어.
    """
    prior = prior if prior is not None else counts_a + counts_b
    prior_total = sum(prior.values()) or 1
    n_a, n_b = sum(counts_a.values()), sum(counts_b.values())
    a0 = alpha * prior_total

    scores: dict[str, float] = {}
    for word in set(counts_a) | set(counts_b):
        a_w = alpha * prior.get(word, 0) + 1e-9
        y_a, y_b = counts_a.get(word, 0), counts_b.get(word, 0)
        num_a, den_a = y_a + a_w, n_a + a0 - y_a - a_w
        num_b, den_b = y_b + a_w, n_b + a0 - y_b - a_w
        if den_a <= 0 or den_b <= 0:
            continue
        delta = math.log(num_a / den_a) - math.log(num_b / den_b)
        variance = 1.0 / num_a + 1.0 / num_b
        scores[word] = delta / math.sqrt(variance)
    return scores


def document_frequency(docs: Iterable[list[str]]) -> Counter[str]:
    """한 리뷰에서 같은 단어를 반복해도 1회로 센다."""
    df: Counter[str] = Counter()
    for feats in docs:
        df.update(set(feats))
    return df


def prune_redundant(
    features: Sequence[str], freq: Counter[str] | dict[str, int], tol: float = 0.85
) -> list[str]:
    """같은 표현이 단어·구 형태로 겹쳐 올라오는 것을 걸러낸다.

    '쿠션이 푹신하고' 같은 문장이 많으면 `푹신`, `쿠션 푹신`, `쿠션`이 모두 상위에
    올라와 목록이 한 문장으로 채워진다. 두 가지 규칙으로 정리한다.

    1) 어떤 구에 거의 항상 붙어 나오는 단어는 버리고 구를 남긴다.
       (`푹신`이 나온 리뷰가 거의 다 `쿠션 푹신`이면 `푹신`은 정보가 없다)
    2) 한 단어를 공유하면서 빈도가 사실상 같은 구는 하나만 남긴다.
       (`배송 예상` / `예상 빨라서` — 한 문장을 토막낸 것)

    입력 순서(중요도순)를 유지한 채 살아남은 특징만 돌려준다.
    """
    bigram_tokens = {f: f.split() for f in features if " " in f}

    # 규칙 1: 구에 종속된 단어 찾기
    subsumed: set[str] = set()
    for phrase, tokens in bigram_tokens.items():
        phrase_freq = freq.get(phrase, 0)
        for token in tokens:
            if freq.get(token, 0) and phrase_freq >= tol * freq[token]:
                subsumed.add(token)

    kept: list[str] = []
    kept_bigrams: list[tuple[list[str], int]] = []
    for feat in features:
        if feat in subsumed and " " not in feat:
            continue
        if tokens := bigram_tokens.get(feat):
            count = freq.get(feat, 0)
            # 규칙 2: 이미 남긴 구와 단어를 공유하고 빈도까지 같으면 건너뛴다
            if any(
                set(tokens) & set(prev_tokens) and min(count, prev) >= tol * max(count, prev)
                for prev_tokens, prev in kept_bigrams
            ):
                continue
            kept_bigrams.append((tokens, count))
        kept.append(feat)
    return kept


def top_by_document_frequency(
    docs: Iterable[list[str]], limit: int = 30, min_df: int = 2, prune: bool = True
) -> list[tuple[str, int]]:
    """문서빈도 상위 특징 (중복 표현 제거 후)."""
    df = document_frequency(docs)
    ranked = [w for w, c in df.most_common() if c >= min_df]
    if prune:
        ranked = prune_redundant(ranked, df)
    return [(w, df[w]) for w in ranked[:limit]]
