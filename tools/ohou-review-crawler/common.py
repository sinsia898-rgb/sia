"""세 스크립트가 공유하는 리뷰 JSON 인식·정규화 로직."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 리뷰 레코드에 흔히 등장하는 키들. 많이 맞을수록 리뷰 목록일 가능성이 높다.
REVIEW_KEY_HINTS = {
    "star", "star_avg", "stars", "rating", "score", "point", "total_star",
    "review", "reviews", "review_id", "reviewId", "contents", "content", "text", "body",
    "writer", "user", "nickname", "author",
    "created_at", "createdAt", "date", "reg_date", "regDate",
    "images", "image", "photos", "photo", "attachments",
    "helped_count", "helpful_count", "like_count", "option", "option_name",
}
URL_HINTS = ("review", "comment", "evaluation")

# 실제 필드명은 사이트 개편마다 바뀐다. discover_api.py 가 출력한 "레코드 키"
# 목록을 보고 이 표만 고치면 CSV 열이 맞춰진다.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "review_id": ("id", "review_id", "reviewId", "seq", "no"),
    "created_at": ("created_at", "createdAt", "reg_date", "regDate", "date", "written_at"),
    "rating": ("star", "star_avg", "stars", "rating", "score", "point", "total_star"),
    "user": ("nickname", "writer", "user_name", "userName", "author", "name"),
    "option": ("option", "option_name", "optionName", "product_option", "goods_option", "variant"),
    "content": ("contents", "content", "review", "text", "body", "comment", "description"),
    "helpful_count": ("helped_count", "helpful_count", "like_count", "helpCount", "recommend_count"),
}
PHOTO_KEYS = ("images", "image", "photos", "photo", "attachments", "files", "image_urls")
CSV_COLUMNS = list(FIELD_ALIASES) + ["photo_count", "photo_urls"]

MAX_DEPTH = 6


def iter_list_of_dicts(node: Any, path: str = "$", depth: int = 0) -> Iterator[tuple[str, list[dict]]]:
    """JSON 안의 '딕셔너리들의 리스트'를 모두 (경로, 리스트)로 내놓는다."""
    if depth > MAX_DEPTH:
        return
    if isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node):
            yield path, node
        for i, child in enumerate(node[:3]):
            yield from iter_list_of_dicts(child, f"{path}[{i}]", depth + 1)
    elif isinstance(node, dict):
        for key, child in node.items():
            yield from iter_list_of_dicts(child, f"{path}.{key}", depth + 1)


def score_list(path: str, records: list[dict], url: str = "") -> int:
    """이 리스트가 '리뷰 목록'일 가능성 점수.

    길이만 보면 리뷰 한 건에 딸린 사진 배열(더 길 수 있다)을 잘못 고르므로,
    리뷰 특징 키가 몇 개나 맞는지를 주된 신호로 쓴다.
    """
    keys: set[str] = set()
    for rec in records[:5]:
        keys |= set(rec.keys())
    hits = keys & REVIEW_KEY_HINTS
    if not hits:
        return 0
    score = len(hits) * 10 + min(len(records), 20)
    if len(keys) <= 2:  # {"url": ...} 같은 사진 배열은 키가 거의 없다
        score -= 25
    if re.search("review", path, re.I):
        score += 15
    if url and re.search("|".join(URL_HINTS), url, re.I):
        score += 25
    return score


def best_list(payload: Any, url: str = "") -> tuple[int, str | None, list[dict]]:
    """(점수, 경로, 레코드들) — 가장 리뷰다운 리스트를 고른다."""
    best: tuple[int, str | None, list[dict]] = (0, None, [])
    for path, records in iter_list_of_dicts(payload):
        score = score_list(path, records, url)
        if score > best[0]:
            best = (score, path, records)
    return best


def dig(payload: Any, list_path: str | None = None, url: str = "") -> list[dict]:
    """경로가 주어지면 그 경로에서, 아니면 점수가 가장 높은 리스트에서 레코드를 꺼낸다."""
    if list_path:
        node = payload
        for seg in list_path.lstrip("$").lstrip(".").split("."):
            if not seg:
                continue
            key = seg.split("[")[0]
            if isinstance(node, dict):
                node = node.get(key)
            elif isinstance(node, list) and node and isinstance(node[0], dict):
                node = node[0].get(key)
            else:
                node = None
            if node is None:
                break
        if isinstance(node, list):
            return [x for x in node if isinstance(x, dict)]
    return best_list(payload, url)[2]


def flatten_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for k in ("nickname", "name", "url", "value", "text", "title"):
            if k in value:
                return flatten_scalar(value[k])
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return " | ".join(flatten_scalar(v) for v in value)
    return str(value)


def pick(rec: dict, aliases: Iterable[str]) -> str:
    aliases = tuple(aliases)
    for key in aliases:
        if rec.get(key) not in (None, ""):
            return flatten_scalar(rec[key])
    # 한 단계 중첩까지 (예: {"user": {"nickname": ...}})
    for value in rec.values():
        if isinstance(value, dict):
            for key in aliases:
                if value.get(key) not in (None, ""):
                    return flatten_scalar(value[key])
    return ""


def photo_urls(rec: dict) -> list[str]:
    urls: list[str] = []
    for key in PHOTO_KEYS:
        val = rec.get(key)
        if not val:
            continue
        for item in val if isinstance(val, list) else [val]:
            if isinstance(item, str):
                urls.append(item)
            elif isinstance(item, dict):
                for k in ("url", "src", "image_url", "path", "origin_url"):
                    if isinstance(item.get(k), str):
                        urls.append(item[k])
                        break
    return urls


def normalize(rec: dict) -> dict:
    row = {name: pick(rec, aliases) for name, aliases in FIELD_ALIASES.items()}
    photos = photo_urls(rec)
    row["photo_count"] = len(photos)
    row["photo_urls"] = " | ".join(photos)
    row["content"] = row["content"].replace("\r\n", "\n").strip()
    return row


def record_key(rec: dict) -> str:
    return pick(rec, FIELD_ALIASES["review_id"]) or json.dumps(rec, sort_keys=True, ensure_ascii=False)


def write_outputs(records: list[dict], out_prefix: str) -> list[dict]:
    """원본 JSON + 정규화 CSV를 저장하고, 열 채움률을 보고한다."""
    Path(f"{out_prefix}.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rows = [normalize(r) for r in records]
    # utf-8-sig: 엑셀에서 한글이 깨지지 않게
    with open(f"{out_prefix}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    filled = {c: sum(1 for r in rows if r[c] not in ("", 0)) for c in CSV_COLUMNS}
    print(f"\n총 {len(rows)}건 저장 → {out_prefix}.csv / {out_prefix}.json")
    print("열 채움률: " + ", ".join(f"{c}={filled[c]}/{len(rows)}" for c in CSV_COLUMNS))
    empty = [c for c in ("rating", "content", "created_at") if filled[c] == 0]
    if empty:
        print(
            f"\n⚠ {', '.join(empty)} 열이 비었습니다. {out_prefix}.json 에서 실제 키 이름을 확인해 "
            "common.py 의 FIELD_ALIASES 를 고쳐주세요."
        )
    return rows


def summarize_record(rec: dict) -> dict:
    """긴 값을 잘라 구조만 보이게 한다."""
    out: dict[str, Any] = {}
    for k, v in rec.items():
        if isinstance(v, str):
            out[k] = v[:80] + ("…" if len(v) > 80 else "")
        elif isinstance(v, (list, dict)):
            out[k] = f"<{type(v).__name__} len={len(v)}>"
        else:
            out[k] = v
    return out
