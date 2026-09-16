#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import mimetypes
import re
import shutil
import time
from pathlib import Path
from urllib.parse import (
    urljoin,
    urlparse,
    urlunparse,
    parse_qsl,
    urlencode,
)

import requests
from bs4 import BeautifulSoup


BASE = "https://jeon.kaist.ac.kr/"

PAGES = {
    "/": "index.html",
    "/research": "research.html",
    "/people": "people.html",
    "/publications": "publications.html",
    "/news": "news.html",
    "/contact": "contact.html",
}

UA = (
    "Mozilla/5.0 "
    "(compatible; BMFL-site-archiver/2.0; +https://jeon.kaist.ac.kr/)"
)

CSS_URL_RE = re.compile(
    r'url\(\s*(["\']?)(.*?)\1\s*\)',
    re.I,
)

CSS_IMPORT_RE = re.compile(
    r'@import\s+(?:url\(\s*)?(["\']?)([^"\')\s;]+)\1\s*\)?([^;]*);',
    re.I,
)

URLISH_RE = re.compile(
    r"^(?:https?:)?//|^/|^\.\.?/",
    re.I,
)

IMAGE_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".avif",
    ".bmp",
    ".ico",
}


class Archiver:
    def __init__(self, out: Path):
        self.out = out
        self.assets = out / "assets" / "mirror"
        self.assets.mkdir(parents=True, exist_ok=True)

        self.s = requests.Session()
        self.s.headers.update(
            {
                "User-Agent": UA,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

        self.cache: dict[str, str] = {}
        self.failures: list[tuple[str, str]] = []
        self.downloaded = 0
        self.css_in_progress: set[str] = set()

    def fetch(self, url, *, binary=False):
        last = None

        for i in range(4):
            try:
                r = self.s.get(
                    url,
                    timeout=40,
                    allow_redirects=True,
                )
                r.raise_for_status()

                return (
                    r.content if binary else r.text
                ), r

            except Exception as e:
                last = e
                time.sleep(1.5 * (i + 1))

        raise last

    @staticmethod
    def originalize_image_url(url: str) -> str:
        """
        Weebly/Artcltn CDN이 작은 리사이즈 이미지를 주는 경우
        width/height/quality 등의 옵션을 제거해서
        가능한 원본 이미지를 요청한다.
        """

        p = urlparse(url)

        if (
            "artcltn.com" not in p.netloc
            and "articulation.website" not in p.netloc
        ):
            return url

        ext = Path(p.path).suffix.lower()

        if ext not in IMAGE_EXTS:
            return url

        qs = [
            (k, v)
            for k, v in parse_qsl(
                p.query,
                keep_blank_values=True,
            )
            if k.lower()
            not in {
                "w",
                "h",
                "width",
                "height",
                "fit",
                "crop",
                "quality",
                "q",
                "format",
            }
        ]

        return urlunparse(
            (
                p.scheme,
                p.netloc,
                p.path,
                p.params,
                urlencode(qs),
                p.fragment,
            )
        )

    @staticmethod
    def best_image_url(tag, page_url):
        candidates = []

        for attr in (
            "data-src",
            "data-image",
            "data-original",
            "data-image-url",
            "data-background-image-url",
            "src",
        ):
            v = tag.get(attr)

            if (
                isinstance(v, str)
                and v
                and not v.startswith("data:")
            ):
                candidates.append(
                    (
                        0,
                        urljoin(page_url, v),
                    )
                )

        for attr in (
            "data-srcset",
            "srcset",
        ):
            v = tag.get(attr)

            if not v:
                continue

            for part in v.split(","):
                bit = part.strip().split()

                if (
                    not bit
                    or bit[0].startswith("data:")
                ):
                    continue

                score = 0

                if len(bit) > 1:
                    m = re.match(
                        r"(\d+)(w|x)",
                        bit[1],
                    )

                    if m:
                        score = int(m.group(1))

                        if m.group(2) == "x":
                            score *= 1000

                candidates.append(
                    (
                        score,
                        urljoin(page_url, bit[0]),
                    )
                )

        if not candidates:
            return None

        candidates.sort(
            key=lambda x: x[0],
            reverse=True,
        )

        return Archiver.originalize_image_url(
            candidates[0][1]
        )

    def name_for(
        self,
        url,
        content_type=None,
    ):
        p = urlparse(url)

        base = Path(p.path).name or "asset"

        base = re.sub(
            r"[^A-Za-z0-9._-]+",
            "_",
            base,
        )

        if "." not in base and content_type:
            ext = (
                mimetypes.guess_extension(
                    content_type
                    .split(";")[0]
                    .strip()
                )
                or ""
            )

            base += ext

        h = hashlib.sha1(
            url.encode()
        ).hexdigest()[:12]

        return f"{h}-{base}"

    def mirror_asset(
        self,
        url,
        ref_page=None,
    ):
        if not url:
            return url

        if url.startswith(
            (
                "data:",
                "mailto:",
                "tel:",
                "javascript:",
                "#",
            )
        ):
            return url

        url = urljoin(
            ref_page or BASE,
            url,
        )

        url = self.originalize_image_url(
            url
        )

        if url in self.cache:
            return self.cache[url]

        try:
            data, r = self.fetch(
                url,
                binary=True,
            )

            ct = r.headers.get(
                "content-type",
                "application/octet-stream",
            )

            if (
                "text/html" in ct
                and not url.lower().endswith(
                    (
                        ".html",
                        ".htm",
                    )
                )
            ):
                raise RuntimeError(
                    f"unexpected HTML content type {ct}"
                )

            name = self.name_for(
                r.url,
                ct,
            )

            dst = self.assets / name
            dst.write_bytes(data)

            rel = "assets/mirror/" + name

            self.cache[url] = rel
            self.cache[r.url] = rel

            self.downloaded += 1

            return rel

        except Exception as e:
            self.failures.append(
                (
                    url,
                    str(e),
                )
            )

            return url

    def mirror_stylesheet(
        self,
        url,
        page_url,
    ):
        url = urljoin(
            page_url,
            url,
        )

        if url in self.cache:
            return self.cache[url]

        if url in self.css_in_progress:
            return url

        self.css_in_progress.add(url)

        try:
            text, r = self.fetch(
                url,
                binary=False,
            )

            name = self.name_for(
                r.url,
                "text/css",
            )

            if not name.endswith(".css"):
                name += ".css"

            rel = "assets/mirror/" + name

            # 먼저 cache에 등록해서
            # CSS import 순환 참조 방지
            self.cache[url] = rel
            self.cache[r.url] = rel

            text = self.rewrite_css_text(
                text,
                r.url,
            )

            (
                self.assets / name
            ).write_text(
                text,
                encoding="utf-8",
            )

            self.downloaded += 1

            return rel

        except Exception as e:
            self.failures.append(
                (
                    url,
                    str(e),
                )
            )

            self.cache.pop(
                url,
                None,
            )

            return url

        finally:
            self.css_in_progress.discard(
                url
            )

    def rewrite_css_text(
        self,
        text,
        css_url,
    ):
        def import_repl(m):
            raw = m.group(2).strip()
            tail = m.group(3) or ""

            if not raw:
                return m.group(0)

            if raw.startswith(
                (
                    "data:",
                    "#",
                )
            ):
                return m.group(0)

            local = self.mirror_stylesheet(
                raw,
                css_url,
            )

            if local.startswith(
                "assets/mirror/"
            ):
                local = Path(local).name

            return (
                f'@import url("{local}")'
                f"{tail};"
            )

        text = CSS_IMPORT_RE.sub(
            import_repl,
            text,
        )

        def repl(m):
            raw = m.group(2).strip()

            if not raw:
                return m.group(0)

            if raw.startswith(
                (
                    "data:",
                    "#",
                )
            ):
                return m.group(0)

            local = self.mirror_asset(
                raw,
                css_url,
            )

            if local.startswith(
                "assets/mirror/"
            ):
                local = Path(local).name

            return f'url("{local}")'

        return CSS_URL_RE.sub(
            repl,
            text,
        )

    def rewrite_inline_css(
        self,
        text,
        page_url,
    ):
        def repl(m):
            raw = m.group(2).strip()

            if not raw:
                return m.group(0)

            if raw.startswith(
                (
                    "data:",
                    "#",
                )
            ):
                return m.group(0)

            local = self.mirror_asset(
                raw,
                page_url,
            )

            return f'url("{local}")'

        return CSS_URL_RE.sub(
            repl,
            text,
        )

    def rewrite_background_data_attrs(
        self,
        soup,
        page_url,
    ):
        """
        Weebly banner / hero / section background는
        일반 img 태그가 아니라 data-* attribute에
        이미지 주소를 저장한 뒤 JS로 적용하는 경우가 많다.
        """

        interesting = {
            "data-background-image",
            "data-background-image-url",
            "data-bg",
            "data-bg-url",
            "data-image",
            "data-image-url",
            "data-src",
            "data-original",
            "data-url",
        }

        for tag in soup.find_all(True):

            for attr in list(tag.attrs):

                if (
                    attr not in interesting
                    and not (
                        "background" in attr
                        and attr.startswith("data-")
                    )
                ):
                    continue

                value = tag.attrs.get(
                    attr
                )

                if (
                    not isinstance(
                        value,
                        str,
                    )
                    or not value
                ):
                    continue

                if value.startswith(
                    (
                        "data:",
                        "#",
                    )
                ):
                    continue

                # attribute 자체가 CSS인 경우
                if "url(" in value:
                    tag.attrs[attr] = (
                        self.rewrite_inline_css(
                            value,
                            page_url,
                        )
                    )

                    continue

                looks_like_image = any(
                    value.lower()
                    .split("?", 1)[0]
                    .endswith(ext)
                    for ext in IMAGE_EXTS
                )

                if (
                    URLISH_RE.search(value)
                    or looks_like_image
                ):
                    local = self.mirror_asset(
                        value,
                        page_url,
                    )

                    tag.attrs[attr] = local

                    # Weebly JS가 없어도
                    # background가 보이도록 style에도 직접 삽입
                    if (
                        "background" in attr
                        or attr
                        in {
                            "data-bg",
                            "data-bg-url",
                            "data-image-url",
                        }
                    ):
                        existing = tag.get(
                            "style",
                            "",
                        )

                        if (
                            "background-image"
                            not in existing.lower()
                        ):
                            prefix = ""

                            if existing:
                                prefix = (
                                    existing.rstrip(";")
                                    + ";"
                                )

                            tag["style"] = (
                                prefix
                                + "background-image:"
                                + f'url("{local}");'
                            )

    def archive_page(
        self,
        path,
        outname,
    ):
        url = urljoin(
            BASE,
            path,
        )

        html, r = self.fetch(
            url,
            binary=False,
        )

        page_url = r.url

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        # 일반 이미지 + lazy image
        for img in soup.find_all(
            "img"
        ):
            best = self.best_image_url(
                img,
                page_url,
            )

            if best:
                local = self.mirror_asset(
                    best,
                    page_url,
                )

                img["src"] = local

                for a in (
                    "data-src",
                    "data-image",
                    "data-original",
                    "data-image-url",
                    "srcset",
                    "data-srcset",
                ):
                    img.attrs.pop(
                        a,
                        None,
                    )

        # picture / source
        for source in soup.find_all(
            "source"
        ):
            src = source.get("src")
            srcset = source.get(
                "srcset"
            )

            chosen = None

            if srcset:
                parts = []

                for x in srcset.split(
                    ","
                ):
                    bit = (
                        x.strip()
                        .split()
                    )

                    if bit:
                        parts.append(
                            bit[0]
                        )

                if parts:
                    chosen = urljoin(
                        page_url,
                        parts[-1],
                    )

            elif src:
                chosen = urljoin(
                    page_url,
                    src,
                )

            if chosen:
                local = self.mirror_asset(
                    chosen,
                    page_url,
                )

                source[
                    "srcset"
                ] = local

                source.attrs.pop(
                    "src",
                    None,
                )

        # video poster
        for video in soup.find_all(
            "video",
            poster=True,
        ):
            video["poster"] = (
                self.mirror_asset(
                    video["poster"],
                    page_url,
                )
            )

        # social / preview images
        for meta in soup.find_all(
            "meta"
        ):
            if (
                meta.get("property")
                in {
                    "og:image",
                    "twitter:image",
                }
                and meta.get(
                    "content"
                )
            ):
                meta[
                    "content"
                ] = self.mirror_asset(
                    meta["content"],
                    page_url,
                )

        # CSS / favicon
        for link in soup.find_all(
            "link"
        ):
            href = link.get(
                "href"
            )

            if not href:
                continue

            rels = {
                str(x).lower()
                for x in link.get(
                    "rel",
                    [],
                )
            }

            if "stylesheet" in rels:
                link[
                    "href"
                ] = self.mirror_stylesheet(
                    href,
                    page_url,
                )

            elif rels & {
                "icon",
                "shortcut",
                "apple-touch-icon",
                "mask-icon",
            }:
                link[
                    "href"
                ] = self.mirror_asset(
                    href,
                    page_url,
                )

        # JavaScript도 로컬 저장
        for script in soup.find_all(
            "script"
        ):
            src = script.get(
                "src"
            )

            if (
                src
                and not src.startswith(
                    "data:"
                )
            ):
                script[
                    "src"
                ] = self.mirror_asset(
                    src,
                    page_url,
                )

        # inline style
        for tag in soup.find_all(
            style=True
        ):
            tag[
                "style"
            ] = self.rewrite_inline_css(
                tag["style"],
                page_url,
            )

        # <style> 내부 background-image 등
        for style in soup.find_all(
            "style"
        ):
            text = (
                style.string
                if style.string
                is not None
                else style.get_text()
            )

            if text:
                style.clear()

                style.append(
                    self.rewrite_css_text(
                        text,
                        page_url,
                    )
                )

        # 핵심:
        # Weebly hero/banner/section background 처리
        self.rewrite_background_data_attrs(
            soup,
            page_url,
        )

        # SVG image
        for image in soup.find_all(
            "image"
        ):
            for attr in (
                "href",
                "xlink:href",
            ):
                if image.get(attr):
                    image[
                        attr
                    ] = self.mirror_asset(
                        image[attr],
                        page_url,
                    )

        # 내부 링크는 GitHub Pages용
        # local html로 변경
        base_host = urlparse(
            BASE
        ).netloc

        for a in soup.find_all(
            "a",
            href=True,
        ):
            href = a["href"]

            if href.startswith(
                (
                    "mailto:",
                    "tel:",
                    "javascript:",
                    "#",
                )
            ):
                continue

            absolute = urljoin(
                page_url,
                href,
            )

            p = urlparse(
                absolute
            )

            if p.netloc == base_host:

                clean = (
                    p.path.rstrip("/")
                    or "/"
                )

                if clean.endswith(
                    ".html"
                ):
                    clean = (
                        clean[:-5]
                        or "/"
                    )

                if clean in PAGES:
                    target = PAGES[
                        clean
                    ]

                    if p.fragment:
                        target += (
                            "#"
                            + p.fragment
                        )

                    a[
                        "href"
                    ] = target

        # canonical / og:url 정리
        for m in soup.find_all(
            "meta"
        ):
            if (
                m.get("property")
                == "og:url"
            ):
                m[
                    "content"
                ] = outname

        can = soup.find(
            "link",
            rel="canonical",
        )

        if can:
            can[
                "href"
            ] = outname

        (
            self.out / outname
        ).write_text(
            str(soup),
            encoding="utf-8",
        )

        print(
            f"Archived {page_url} -> {outname}"
        )

    def run(self):

        for p, n in PAGES.items():
            self.archive_page(
                p,
                n,
            )

        (
            self.out / ".nojekyll"
        ).write_text(
            "",
            encoding="utf-8",
        )

        report = [
            "# BMFL live-site archive report",
            "",
            f"- Source: {BASE}",
            f"- Pages archived: {len(PAGES)}",
            (
                "- Assets downloaded locally: "
                f"{self.downloaded}"
            ),
            (
                "- Failed asset requests: "
                f"{len(self.failures)}"
            ),
            "",
        ]

        if self.failures:
            report += [
                "## Failed assets",
                "",
            ]

            report += [
                f"- `{u}` — {e}"
                for u, e
                in self.failures
            ]

        (
            self.out
            / "MIRROR_REPORT.md"
        ).write_text(
            "\n".join(report),
            encoding="utf-8",
        )

        print(
            "Done: "
            f"{self.downloaded} assets, "
            f"{len(self.failures)} failures"
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--out",
        default="docs",
    )

    args = ap.parse_args()

    out = Path(
        args.out
    )

    if out.exists():
        shutil.rmtree(
            out
        )

    out.mkdir(
        parents=True
    )

    Archiver(
        out
    ).run()
