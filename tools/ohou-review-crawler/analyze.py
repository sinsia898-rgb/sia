#!/usr/bin/env python3
"""3단계: 수집한 리뷰의 별점 분포와 키워드를 분석한다.

사용법:
    python analyze.py reviews_979406.json                      # 터미널 요약
    python analyze.py reviews_979406.json --html report.html    # HTML 리포트까지
    python analyze.py reviews_979406.csv --title '3인 소파 리뷰 분석'

키워드 품질을 올리려면 (선택):
    pip install kiwipiepy
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from common import normalize
from korean_text import (
    ASPECTS,
    aspect_hits,
    doc_features,
    kiwi_available,
    log_odds_ratio,
    prune_redundant,
    top_by_document_frequency,
)

HIGH_MIN = 4.0  # 이 이상을 고평점으로 본다 (5점 척도)
LOW_MAX = 2.0   # 이 이하를 저평점으로 본다

DATE_FORMATS = ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


# ─────────────────────────────── 입력 ───────────────────────────────


def load_rows(path: Path) -> list[dict]:
    """수집기가 만든 .json(원본) 또는 .csv(정규화)를 공통 형태로 읽는다."""
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):  # 페이지 원본을 그대로 넘긴 경우
            from common import dig

            payload = dig(payload)
        return [normalize(rec) for rec in payload]
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def to_month(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text[: len(fmt) + 4], fmt).strftime("%Y-%m")
        except ValueError:
            continue
    if m := re.match(r"(\d{4})[-./](\d{1,2})", text):
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return None


def prepare(rows: list[dict]) -> tuple[list[dict], float]:
    """별점·날짜·본문을 정리하고, 별점 척도(5점/10점)를 추정한다."""
    ratings = [r for r in (to_float(x.get("rating")) for x in rows) if r is not None]
    scale = 10.0 if ratings and max(ratings) > 5.0 else 5.0

    prepared = []
    for row in rows:
        rating = to_float(row.get("rating"))
        if rating is not None and scale == 10.0:
            rating = rating / 2.0  # 5점 척도로 환산해 비교 기준을 통일
        photos = to_float(row.get("photo_count")) or 0
        content = (row.get("content") or "").strip()
        prepared.append(
            {
                "review_id": row.get("review_id", ""),
                "rating": rating,
                "month": to_month(row.get("created_at")),
                "user": row.get("user", ""),
                "option": (row.get("option") or "").strip(),
                "content": content,
                "length": len(content),
                "photo_count": int(photos),
            }
        )
    return prepared, scale


# ─────────────────────────────── 분석 ───────────────────────────────


def bucket(rating: float | None) -> int | None:
    """별점을 1~5 정수 구간으로 묶는다.

    파이썬 기본 round()는 은행가 반올림이라 4.5를 4로 내린다. 반개 별점을
    쓰는 사이트에서 4.5점이 4점 칸에 들어가면 분포가 어긋나므로 올림 처리한다.
    """
    return None if rating is None else max(1, min(5, math.floor(rating + 0.5)))


def rating_distribution(reviews: list[dict]) -> dict[int, int]:
    dist = {i: 0 for i in range(1, 6)}
    for r in reviews:
        if (b := bucket(r["rating"])) is not None:
            dist[b] += 1
    return dist


def monthly_trend(reviews: list[dict]) -> list[dict]:
    grouped: dict[str, list[float]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for r in reviews:
        if not r["month"]:
            continue
        counts[r["month"]] += 1
        if r["rating"] is not None:
            grouped[r["month"]].append(r["rating"])
    return [
        {
            "month": month,
            "count": counts[month],
            "avg": statistics.fmean(grouped[month]) if grouped.get(month) else None,
        }
        for month in sorted(counts)
    ]


def group_stats(reviews: list[dict], key: str, limit: int = 8, min_count: int = 3) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in reviews:
        if r[key]:
            grouped[r[key]].append(r)
    out = []
    for name, items in grouped.items():
        rated = [i["rating"] for i in items if i["rating"] is not None]
        if len(items) < min_count:
            continue
        out.append(
            {
                "name": name,
                "count": len(items),
                "avg": statistics.fmean(rated) if rated else None,
            }
        )
    out.sort(key=lambda x: x["count"], reverse=True)
    return out[:limit]


def length_by_rating(reviews: list[dict]) -> dict[int, float]:
    grouped: dict[int, list[int]] = defaultdict(list)
    for r in reviews:
        if (b := bucket(r["rating"])) is not None:
            grouped[b].append(r["length"])
    return {b: statistics.fmean(v) for b, v in sorted(grouped.items()) if v}


def photo_effect(reviews: list[dict]) -> dict[str, dict]:
    out = {}
    for label, subset in (
        ("사진 있음", [r for r in reviews if r["photo_count"] > 0]),
        ("사진 없음", [r for r in reviews if r["photo_count"] == 0]),
    ):
        rated = [r["rating"] for r in subset if r["rating"] is not None]
        out[label] = {
            "count": len(subset),
            "avg": statistics.fmean(rated) if rated else None,
            "avg_length": statistics.fmean([r["length"] for r in subset]) if subset else 0,
        }
    return out


def aspect_analysis(reviews: list[dict]) -> list[dict]:
    """속성별 언급 수와 평균 별점. 평균이 낮은 속성이 곧 개선 우선순위다."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in reviews:
        for name in aspect_hits(r["content"]):
            grouped[name].append(r)

    overall = [r["rating"] for r in reviews if r["rating"] is not None]
    overall_avg = statistics.fmean(overall) if overall else None

    out = []
    for name in ASPECTS:
        items = grouped.get(name, [])
        if not items:
            continue
        rated = [i["rating"] for i in items if i["rating"] is not None]
        avg = statistics.fmean(rated) if rated else None
        out.append(
            {
                "name": name,
                "count": len(items),
                "share": len(items) / len(reviews) if reviews else 0,
                "avg": avg,
                "gap": (avg - overall_avg) if (avg is not None and overall_avg is not None) else None,
            }
        )
    # 전체 평균보다 낮은 속성부터 = 문제가 되는 속성부터
    out.sort(key=lambda x: (x["gap"] if x["gap"] is not None else 0))
    return out


def keyword_analysis(reviews: list[dict], top: int, use_morph: bool) -> dict[str, Any]:
    docs, high_counts, low_counts = [], Counter(), Counter()
    for r in reviews:
        feats = doc_features(r["content"], use_morph=use_morph)
        if not feats:
            continue
        docs.append(feats)
        if r["rating"] is None:
            continue
        if r["rating"] >= HIGH_MIN:
            high_counts.update(feats)
        elif r["rating"] <= LOW_MAX:
            low_counts.update(feats)

    overall = top_by_document_frequency(docs, limit=top, min_df=2)

    positive: list[tuple[str, float, int, int]] = []
    negative: list[tuple[str, float, int, int]] = []
    if high_counts and low_counts:
        combined = high_counts + low_counts
        scores = log_odds_ratio(high_counts, low_counts)
        # 양쪽 합쳐 3회 미만인 말은 표본이 없어 변별력을 논할 수 없다
        ranked = sorted(
            ((w, z) for w, z in scores.items() if combined[w] >= 3),
            key=lambda x: x[1],
            reverse=True,
        )

        def dress(words: list[str]) -> list[tuple[str, float, int, int]]:
            table = dict(ranked)
            return [(w, table[w], high_counts[w], low_counts[w]) for w in words[:top]]

        order = [w for w, _ in ranked]
        positive = dress(prune_redundant(order, combined))
        negative = dress(prune_redundant(list(reversed(order)), combined))

    return {
        "overall": overall,
        "positive": positive,
        "negative": negative,
        "high_n": sum(1 for r in reviews if r["rating"] is not None and r["rating"] >= HIGH_MIN),
        "low_n": sum(1 for r in reviews if r["rating"] is not None and r["rating"] <= LOW_MAX),
    }


def excerpts(reviews: list[dict], limit: int = 3) -> dict[str, list[dict]]:
    """정보량이 많은(= 긴) 리뷰를 고·저평점에서 각각 뽑는다."""

    def pick(pred) -> list[dict]:
        items = [r for r in reviews if r["rating"] is not None and pred(r["rating"]) and r["length"] > 20]
        items.sort(key=lambda r: r["length"], reverse=True)
        return items[:limit]

    return {"low": pick(lambda x: x <= LOW_MAX), "high": pick(lambda x: x >= HIGH_MIN)}


def build_report(reviews: list[dict], scale: float, top: int, use_morph: bool) -> dict[str, Any]:
    rated = [r["rating"] for r in reviews if r["rating"] is not None]
    months = sorted(r["month"] for r in reviews if r["month"])
    return {
        "total": len(reviews),
        "rated": len(rated),
        "scale": scale,
        "avg": statistics.fmean(rated) if rated else None,
        "median": statistics.median(rated) if rated else None,
        "period": (months[0], months[-1]) if months else None,
        "with_photo": sum(1 for r in reviews if r["photo_count"] > 0),
        "distribution": rating_distribution(reviews),
        "monthly": monthly_trend(reviews),
        "options": group_stats(reviews, "option"),
        "length_by_rating": length_by_rating(reviews),
        "photo_effect": photo_effect(reviews),
        "aspects": aspect_analysis(reviews),
        "keywords": keyword_analysis(reviews, top, use_morph),
        "excerpts": excerpts(reviews),
        "morph": use_morph and kiwi_available(),
    }


# ─────────────────────────── 터미널 출력 ───────────────────────────


def bar(value: float, maximum: float, width: int = 28) -> str:
    if maximum <= 0:
        return ""
    filled = int(round(value / maximum * width))
    return "█" * filled + "·" * (width - filled)


def fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def print_report(rep: dict[str, Any]) -> None:
    line = "─" * 68
    print(f"\n{line}\n리뷰 분석 요약\n{line}")
    print(f"총 리뷰        : {rep['total']}건 (별점 있는 리뷰 {rep['rated']}건)")
    if rep["period"]:
        print(f"기간           : {rep['period'][0]} ~ {rep['period'][1]}")
    print(f"평균 별점      : {fmt(rep['avg'])} / 5.00   (중앙값 {fmt(rep['median'], 1)})")
    if rep["scale"] == 10.0:
        print("                 ※ 원본이 10점 척도로 보여 5점 척도로 환산했습니다")
    share = rep["with_photo"] / rep["total"] * 100 if rep["total"] else 0
    print(f"포토리뷰       : {rep['with_photo']}건 ({share:.1f}%)")
    print(f"키워드 추출    : {'형태소 분석(kiwipiepy)' if rep['morph'] else '휴리스틱 (pip install kiwipiepy 권장)'}")

    print(f"\n{line}\n별점 분포\n{line}")
    dist = rep["distribution"]
    peak = max(dist.values()) or 1
    for star in (5, 4, 3, 2, 1):
        count = dist[star]
        pct = count / rep["rated"] * 100 if rep["rated"] else 0
        print(f"{star}점 {bar(count, peak)} {count:>5}건 ({pct:5.1f}%)")
    negative = dist[1] + dist[2]
    if rep["rated"]:
        print(f"\n1~2점 비중: {negative / rep['rated'] * 100:.1f}%  ({negative}건)")

    if len(rep["monthly"]) > 1:
        print(f"\n{line}\n월별 추이\n{line}")
        peak = max(m["count"] for m in rep["monthly"]) or 1
        for m in rep["monthly"][-18:]:
            print(f"{m['month']}  {bar(m['count'], peak, 20)} {m['count']:>4}건   평균 {fmt(m['avg'])}")

    if rep["options"]:
        print(f"\n{line}\n옵션별\n{line}")
        for opt in rep["options"]:
            print(f"평균 {fmt(opt['avg'])}  {opt['count']:>4}건   {opt['name'][:44]}")

    if rep["aspects"]:
        print(f"\n{line}\n속성별 언급·평균 별점 (전체 평균 대비 낮은 순 = 개선 우선순위)\n{line}")
        for a in rep["aspects"]:
            gap = f"{a['gap']:+.2f}" if a["gap"] is not None else "  —  "
            print(f"{a['name']:<14} {a['count']:>4}건 ({a['share'] * 100:4.1f}%)  평균 {fmt(a['avg'])}  전체대비 {gap}")

    kw = rep["keywords"]
    if kw["overall"]:
        print(f"\n{line}\n자주 나온 키워드 (몇 건의 리뷰에 등장했는지)\n{line}")
        for i, (word, count) in enumerate(kw["overall"], 1):
            print(f"{i:>2}. {word:<22} {count:>4}건", end="\n" if i % 2 == 0 else "   ")
        if len(kw["overall"]) % 2:
            print()

    if kw["positive"] or kw["negative"]:
        print(f"\n{line}\n고평점({HIGH_MIN:.0f}점↑ {kw['high_n']}건) vs 저평점({LOW_MAX:.0f}점↓ {kw['low_n']}건) 변별 키워드\n{line}")
        print("칭찬으로 이어지는 말                    불만으로 이어지는 말")
        for i in range(max(len(kw["positive"][:12]), len(kw["negative"][:12]))):
            left = right = ""
            if i < len(kw["positive"][:12]):
                w, z, h, l = kw["positive"][i]
                left = f"{w} ({h}/{l}, z={z:+.1f})"
            if i < len(kw["negative"][:12]):
                w, z, h, l = kw["negative"][i]
                right = f"{w} ({h}/{l}, z={z:+.1f})"
            print(f"{left:<38} {right}")
        print("\n※ (고평점 등장수/저평점 등장수, z=변별력). 단순 빈도가 아니라 로그오즈비 기준입니다.")

    for label, items in (("저평점 상세 리뷰", rep["excerpts"]["low"]), ("고평점 상세 리뷰", rep["excerpts"]["high"])):
        if not items:
            continue
        print(f"\n{line}\n{label}\n{line}")
        for r in items:
            head = f"[{fmt(r['rating'], 1)}점"
            if r["month"]:
                head += f" · {r['month']}"
            if r["option"]:
                head += f" · {r['option'][:24]}"
            print(f"{head}]")
            body = r["content"].replace("\n", " ")
            print(f"  {body[:260]}{'…' if len(body) > 260 else ''}\n")


# ─────────────────────────── HTML 리포트 ───────────────────────────


def write_html(rep: dict[str, Any], path: Path, title: str, source: str) -> None:
    e = html.escape

    def bars(rows: list[tuple[str, float, str]]) -> str:
        peak = max((v for _, v, _ in rows), default=0) or 1
        return "".join(
            f'<div class="row"><span class="lbl">{e(label)}</span>'
            f'<span class="track"><span class="fill" style="width:{value / peak * 100:.1f}%"></span></span>'
            f'<span class="val">{e(note)}</span></div>'
            for label, value, note in rows
        )

    dist = rep["distribution"]
    dist_rows = [
        (
            f"{s}점",
            dist[s],
            f"{dist[s]}건 ({dist[s] / rep['rated'] * 100:.1f}%)" if rep["rated"] else f"{dist[s]}건",
        )
        for s in (5, 4, 3, 2, 1)
    ]
    monthly_rows = [
        (m["month"], m["count"], f"{m['count']}건 · 평균 {fmt(m['avg'])}") for m in rep["monthly"][-18:]
    ]

    aspect_rows = "".join(
        f"<tr><td>{e(a['name'])}</td><td class='n'>{a['count']}</td>"
        f"<td class='n'>{a['share'] * 100:.1f}%</td><td class='n'>{fmt(a['avg'])}</td>"
        f"<td class='n {'neg' if (a['gap'] or 0) < 0 else 'pos'}'>"
        f"{('%+.2f' % a['gap']) if a['gap'] is not None else '—'}</td></tr>"
        for a in rep["aspects"]
    )

    kw = rep["keywords"]
    overall_chips = "".join(
        f'<span class="chip">{e(w)}<b>{c}</b></span>' for w, c in kw["overall"]
    )

    def kw_list(items) -> str:
        return "".join(
            f"<li><span>{e(w)}</span><em>{h}/{l} · z={z:+.1f}</em></li>" for w, z, h, l in items[:15]
        )

    option_rows = "".join(
        f"<tr><td>{e(o['name'])}</td><td class='n'>{o['count']}</td><td class='n'>{fmt(o['avg'])}</td></tr>"
        for o in rep["options"]
    )

    def excerpt_html(items) -> str:
        out = []
        for r in items:
            meta = " · ".join(x for x in [f"{fmt(r['rating'], 1)}점", r["month"] or "", r["option"]] if x)
            out.append(f'<blockquote><cite>{e(meta)}</cite><p>{e(r["content"][:400])}</p></blockquote>')
        return "".join(out)

    photo = rep["photo_effect"]
    period = f"{rep['period'][0]} ~ {rep['period'][1]}" if rep["period"] else "—"
    negative_share = (dist[1] + dist[2]) / rep["rated"] * 100 if rep["rated"] else 0

    doc = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<style>
  :root {{ --bg:#fbfaf8; --card:#fff; --ink:#1c1a17; --muted:#6b6660; --line:#e7e3dd;
           --accent:#8a6d3b; --pos:#2f6f4f; --neg:#a4453a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); padding:32px 20px 72px;
          font:15px/1.6 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif; }}
  .wrap {{ max-width:920px; margin:0 auto; }}
  h1 {{ font-size:26px; margin:0 0 6px; letter-spacing:-.02em; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:28px; }}
  section {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
             padding:22px 24px; margin-bottom:18px; }}
  h2 {{ font-size:15px; margin:0 0 16px; color:var(--accent); letter-spacing:.02em; }}
  .kpis {{ display:flex; flex-wrap:wrap; gap:14px; }}
  .kpi {{ flex:1 1 150px; border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  .kpi b {{ display:block; font-size:24px; font-weight:600; letter-spacing:-.02em; }}
  .kpi span {{ color:var(--muted); font-size:12px; }}
  .row {{ display:flex; align-items:center; gap:12px; margin-bottom:7px; font-size:13px; }}
  .lbl {{ width:72px; flex:none; color:var(--muted); }}
  .track {{ flex:1; height:9px; background:#efece7; border-radius:99px; overflow:hidden; }}
  .fill {{ display:block; height:100%; background:var(--accent); border-radius:99px; }}
  .val {{ width:150px; flex:none; text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; }}
  th {{ color:var(--muted); font-weight:500; font-size:12px; }}
  td.n {{ text-align:right; font-variant-numeric:tabular-nums; }}
  td.pos {{ color:var(--pos); }} td.neg {{ color:var(--neg); }}
  .chip {{ display:inline-flex; gap:6px; align-items:center; background:#f4f1ec; border-radius:99px;
           padding:5px 12px; margin:0 6px 8px 0; font-size:13px; }}
  .chip b {{ color:var(--muted); font-weight:500; font-size:11px; }}
  .cols {{ display:flex; gap:24px; flex-wrap:wrap; }}
  .col {{ flex:1 1 260px; }}
  .col h3 {{ font-size:13px; margin:0 0 10px; }}
  .col h3.p {{ color:var(--pos); }} .col h3.n {{ color:var(--neg); }}
  ol {{ margin:0; padding-left:20px; }}
  ol li {{ display:flex; justify-content:space-between; gap:10px; padding:4px 0; font-size:13px; }}
  ol li em {{ color:var(--muted); font-style:normal; font-size:11px; font-variant-numeric:tabular-nums; }}
  blockquote {{ margin:0 0 14px; padding:12px 16px; background:#faf8f5;
                border-left:3px solid var(--line); border-radius:0 8px 8px 0; }}
  blockquote cite {{ display:block; font-style:normal; font-size:11px; color:var(--muted); margin-bottom:6px; }}
  blockquote p {{ margin:0; font-size:13px; }}
  .note {{ color:var(--muted); font-size:12px; margin-top:14px; }}
  @media (max-width:520px) {{ .val {{ width:96px; }} .lbl {{ width:52px; }} }}
</style></head><body><div class="wrap">
<h1>{e(title)}</h1>
<div class="sub">{e(source)} · 리뷰 {rep['total']}건 · {e(period)} · 키워드 추출: {'형태소 분석' if rep['morph'] else '휴리스틱'}</div>

<section><h2>한눈에</h2><div class="kpis">
  <div class="kpi"><b>{fmt(rep['avg'])}</b><span>평균 별점 / 5.00</span></div>
  <div class="kpi"><b>{rep['total']}</b><span>총 리뷰</span></div>
  <div class="kpi"><b>{negative_share:.1f}%</b><span>1~2점 비중</span></div>
  <div class="kpi"><b>{rep['with_photo'] / rep['total'] * 100 if rep['total'] else 0:.0f}%</b><span>포토리뷰</span></div>
</div></section>

<section><h2>별점 분포</h2>{bars(dist_rows)}</section>

{f'<section><h2>월별 리뷰 수와 평균 별점</h2>{bars(monthly_rows)}</section>' if len(monthly_rows) > 1 else ''}

<section><h2>속성별 언급과 평균 별점</h2>
<table><thead><tr><th>속성</th><th class="n">언급</th><th class="n">비중</th>
<th class="n">평균 별점</th><th class="n">전체대비</th></tr></thead><tbody>{aspect_rows}</tbody></table>
<p class="note">전체대비가 음수인 속성이 불만의 원인입니다. 위에서부터 개선 우선순위로 읽으세요.</p></section>

<section><h2>자주 나온 키워드</h2>{overall_chips}
<p class="note">숫자는 그 말이 등장한 리뷰 수입니다 (한 리뷰에서 반복해도 1건).</p></section>

<section><h2>고평점 vs 저평점 변별 키워드</h2><div class="cols">
  <div class="col"><h3 class="p">칭찬으로 이어지는 말 ({kw['high_n']}건 기준)</h3><ol>{kw_list(kw['positive'])}</ol></div>
  <div class="col"><h3 class="n">불만으로 이어지는 말 ({kw['low_n']}건 기준)</h3><ol>{kw_list(kw['negative'])}</ol></div>
</div><p class="note">단순 빈도가 아니라 사전 보정 로그오즈비(z)입니다. 양 집단에 흔한 말은 자동으로 걸러집니다.
숫자는 고평점/저평점 등장 횟수입니다.</p></section>

{f'<section><h2>옵션별</h2><table><thead><tr><th>옵션</th><th class="n">건수</th><th class="n">평균 별점</th></tr></thead><tbody>{option_rows}</tbody></table></section>' if option_rows else ''}

<section><h2>사진 유무별</h2><table><thead><tr><th>구분</th><th class="n">건수</th>
<th class="n">평균 별점</th><th class="n">평균 글자수</th></tr></thead><tbody>
{"".join(f"<tr><td>{e(k)}</td><td class='n'>{v['count']}</td><td class='n'>{fmt(v['avg'])}</td><td class='n'>{v['avg_length']:.0f}</td></tr>" for k, v in photo.items())}
</tbody></table></section>

{f'<section><h2>저평점 상세 리뷰</h2>{excerpt_html(rep["excerpts"]["low"])}</section>' if rep["excerpts"]["low"] else ''}
{f'<section><h2>고평점 상세 리뷰</h2>{excerpt_html(rep["excerpts"]["high"])}</section>' if rep["excerpts"]["high"] else ''}
</div></body></html>"""
    path.write_text(doc, encoding="utf-8")


# ─────────────────────────────── CLI ───────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="수집한 오늘의집 리뷰의 별점·키워드 분석")
    ap.add_argument("input", help="fetch_reviews.py 가 만든 .json 또는 .csv")
    ap.add_argument("--html", help="HTML 리포트 저장 경로 (예: report.html)")
    ap.add_argument("--title", default="오늘의집 리뷰 분석", help="리포트 제목")
    ap.add_argument("--top", type=int, default=30, help="키워드 표시 개수")
    ap.add_argument("--no-morph", action="store_true", help="kiwipiepy가 있어도 쓰지 않음")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"입력 파일이 없습니다: {path}", file=sys.stderr)
        return 1

    rows = load_rows(path)
    if not rows:
        print("리뷰가 비어 있습니다.", file=sys.stderr)
        return 1

    reviews, scale = prepare(rows)
    rep = build_report(reviews, scale, args.top, use_morph=not args.no_morph)
    print_report(rep)

    if args.html:
        out = Path(args.html)
        write_html(rep, out, args.title, path.name)
        print(f"\nHTML 리포트 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
