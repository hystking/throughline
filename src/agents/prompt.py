"""Research セッションに渡す入力プロンプトを組み立てる (`docs/design.md` §4-③)。

Triage が確定させた記事 1 本 (dict) を XML 断片に落とすだけ。**LLM は使わない**。
ここで加工・要約をしないのは、エージェントに一次情報をそのまま見せたいから。

    <article> … </article>      必須。何を調べるのか
    <sources> … </sources>      HN / Reddit の議論。→ 約束③
    <related_urls> … </related_urls>  Triage が同一トピックと判定した別記事
    <continues …> … </continues>      Triage が見つけた「続き」。→ 約束①
    <excerpt> … </excerpt>            RSS の summary が取れていれば

**空のブロックはタグごと省く。** 空タグを見せると「無いものを探せ」と読まれる。
`<continues>` が無ければエージェントはゼロから web_search で流れを探す。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from xml.sax.saxutils import escape, quoteattr

SOURCE_ATTRS = ("kind", "subreddit", "score", "num_comments", "discussion_url")
"""`<source>` の属性の並び順。ここに無いキーは後ろにそのまま付く。"""

CONTINUES_ATTRS = ("date", "relation")

_CONTINUES_BODY_KEYS = ("title", "what")
"""`<continues>` の本文にするキー。Triage の出力は ``title``。"""


class PromptError(ValueError):
    """プロンプトを組み立てるだけの材料が揃っていない。"""


def render_prompt(article: Mapping[str, Any]) -> str:
    """Triage が確定させた記事 1 本から、セッションに送る本文を作る。

    ``article`` に期待するキー (``title`` と ``url`` 以外は任意):

    ==================  ===========================================
    ``title``           原題
    ``url``             代表 URL (Triage の ``primary_seq``)
    ``published_at``    公開時刻
    ``category``        Triage が付けたカテゴリ
    ``sources``         ``{kind, score, num_comments, discussion_url, …}`` の配列
    ``related_urls``    同一トピックと判定された別記事の URL
    ``continues``       ``{date, title, relation}``
    ``excerpt``         RSS の summary
    ==================  ===========================================
    """
    if not isinstance(article, Mapping):
        raise PromptError(f"記事はマッピングであること (今 {type(article).__name__})")

    blocks = [_article_block(article)]
    if sources := article.get("sources"):
        blocks.append(_sources_block(sources))
    if related := article.get("related_urls"):
        blocks.append(_related_urls_block(related))
    if continues := article.get("continues"):
        blocks.append(_continues_block(continues))
    if excerpt := str(article.get("excerpt") or "").strip():
        blocks.append(_excerpt_block(excerpt))
    # 中身が無かったブロックは "" が返る。空行にせずそのまま落とす
    return "\n".join(block for block in blocks if block) + "\n"


# ---------------------------------------------------------------------------
# ブロックごと
# ---------------------------------------------------------------------------


def _article_block(article: Mapping[str, Any]) -> str:
    title = str(article.get("title") or "").strip()
    url = str(article.get("url") or "").strip()
    if not title or not url:
        raise PromptError("記事には title と url が要る")

    lines = [f"  <title>{escape(title)}</title>", f"  <url>{escape(url)}</url>"]
    for key in ("published_at", "category"):
        if value := str(article.get(key) or "").strip():
            lines.append(f"  <{key}>{escape(value)}</{key}>")
    return "<article>\n" + "\n".join(lines) + "\n</article>"


def _sources_block(sources: Any) -> str:
    lines = []
    for source in sources:
        if not isinstance(source, Mapping):
            raise PromptError(f"sources の要素はマッピングであること (今 {type(source).__name__})")
        if attrs := _attrs(source, SOURCE_ATTRS):
            lines.append(f"  <source {attrs} />")
    if not lines:
        return ""
    return "<sources>\n" + "\n".join(lines) + "\n</sources>"


def _related_urls_block(urls: Any) -> str:
    lines = [f"  <url>{escape(str(url))}</url>" for url in urls if str(url or "").strip()]
    if not lines:
        return ""
    return "<related_urls>\n" + "\n".join(lines) + "\n</related_urls>"


def _continues_block(continues: Mapping[str, Any]) -> str:
    if not isinstance(continues, Mapping):
        raise PromptError(f"continues はマッピングであること (今 {type(continues).__name__})")

    body = ""
    for key in _CONTINUES_BODY_KEYS:
        if body := str(continues.get(key) or "").strip():
            break
    rest = {key: value for key, value in continues.items() if key not in _CONTINUES_BODY_KEYS}
    attrs = _attrs(rest, CONTINUES_ATTRS)
    open_tag = f"<continues {attrs}>" if attrs else "<continues>"
    return f"{open_tag}\n  {escape(body)}\n</continues>"


def _excerpt_block(excerpt: str) -> str:
    body = "\n".join(f"  {line}" for line in escape(excerpt).splitlines())
    return f"<excerpt>\n{body}\n</excerpt>"


def _attrs(data: Mapping[str, Any], order: tuple[str, ...]) -> str:
    """``order`` を先に、残りは元の順で属性文字列にする。None と空文字は落とす。"""
    keys = [key for key in order if key in data]
    keys += [key for key in data if key not in order]
    parts = []
    for key in keys:
        value = data[key]
        if value is None or value == "":
            continue
        parts.append(f"{key}={quoteattr(str(value))}")
    return " ".join(parts)
