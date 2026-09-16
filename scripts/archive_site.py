#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, mimetypes, os, re, shutil, sys, time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

BASE = 'https://jeon.kaist.ac.kr/'
PAGES = {
    '/': 'index.html',
    '/research': 'research.html',
    '/people': 'people.html',
    '/publications': 'publications.html',
    '/news': 'news.html',
    '/contact': 'contact.html',
}
UA = 'Mozilla/5.0 (compatible; BMFL-site-archiver/1.0; +https://jeon.kaist.ac.kr/)'
CSS_URL_RE = re.compile(r'url\(\s*([\"\']?)(.*?)\1\s*\)', re.I)

class Archiver:
    def __init__(self, out: Path):
        self.out = out
        self.assets = out / 'assets' / 'mirror'
        self.assets.mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers.update({'User-Agent': UA})
        self.cache: dict[str,str] = {}
        self.failures: list[tuple[str,str]] = []
        self.downloaded = 0

    def fetch(self, url, *, binary=False):
        last = None
        for i in range(4):
            try:
                r = self.s.get(url, timeout=35, allow_redirects=True)
                r.raise_for_status()
                return r.content if binary else r.text, r
            except Exception as e:
                last = e
                time.sleep(1.5*(i+1))
        raise last

    @staticmethod
    def best_image_url(tag, page_url):
        candidates=[]
        for attr in ('data-src','data-image','data-original','src'):
            v=tag.get(attr)
            if v and not v.startswith('data:'):
                candidates.append(urljoin(page_url,v))
        for attr in ('data-srcset','srcset'):
            v=tag.get(attr)
            if v:
                for part in v.split(','):
                    bit=part.strip().split()
                    if bit and not bit[0].startswith('data:'):
                        score=0
                        if len(bit)>1:
                            m=re.match(r'(\d+)(w|x)',bit[1])
                            if m: score=int(m.group(1))*(1000 if m.group(2)=='x' else 1)
                        candidates.append((score,urljoin(page_url,bit[0])))
        scored=[]
        for c in candidates:
            if isinstance(c,tuple): scored.append(c)
            else: scored.append((0,c))
        if not scored: return None
        scored.sort(key=lambda x:x[0], reverse=True)
        u=scored[0][1]
        # Articulation CDN serves resized copies via ?w=. Remove resize query to retain original upload.
        p=urlparse(u)
        if 'artcltn.com' in p.netloc or 'articulation.website' in p.netloc:
            u=urlunparse((p.scheme,p.netloc,p.path,p.params,'',p.fragment))
        return u

    def name_for(self,url,content_type=None):
        p=urlparse(url)
        base=Path(p.path).name or 'asset'
        base=re.sub(r'[^A-Za-z0-9._-]+','_',base)
        if '.' not in base and content_type:
            ext=mimetypes.guess_extension(content_type.split(';')[0].strip()) or ''
            base += ext
        h=hashlib.sha1(url.encode()).hexdigest()[:12]
        return f'{h}-{base}'

    def mirror_asset(self,url,ref_page=None):
        if not url or url.startswith(('data:','mailto:','tel:','javascript:','#')):
            return url
        url=urljoin(ref_page or BASE,url)
        if url in self.cache: return self.cache[url]
        try:
            data,r=self.fetch(url,binary=True)
            ct=r.headers.get('content-type','application/octet-stream')
            # Do not save HTML error/landing pages as assets.
            if 'text/html' in ct and not url.lower().endswith(('.html','.htm')):
                raise RuntimeError(f'unexpected HTML content type {ct}')
            name=self.name_for(r.url,ct)
            dst=self.assets/name
            dst.write_bytes(data)
            rel='assets/mirror/'+name
            self.cache[url]=rel
            self.cache[r.url]=rel
            self.downloaded += 1
            return rel
        except Exception as e:
            self.failures.append((url,str(e)))
            return url

    def rewrite_css_text(self,text,css_url):
        def repl(m):
            raw=m.group(2).strip()
            if not raw or raw.startswith(('data:','#')): return m.group(0)
            local=self.mirror_asset(urljoin(css_url,raw),css_url)
            # CSS files live inside assets/mirror, so local sibling is just basename.
            if local.startswith('assets/mirror/'):
                local=Path(local).name
            return f'url("{local}")'
        return CSS_URL_RE.sub(repl,text)

    def mirror_stylesheet(self,url,page_url):
        url=urljoin(page_url,url)
        if url in self.cache: return self.cache[url]
        try:
            text,r=self.fetch(url,binary=False)
            name=self.name_for(r.url,'text/css')
            if not name.endswith('.css'): name += '.css'
            # Reserve cache before recursive url() downloads.
            rel='assets/mirror/'+name
            self.cache[url]=rel; self.cache[r.url]=rel
            text=self.rewrite_css_text(text,r.url)
            (self.assets/name).write_text(text,encoding='utf-8')
            self.downloaded += 1
            return rel
        except Exception as e:
            self.failures.append((url,str(e)))
            return url

    def rewrite_inline_css(self,text,page_url):
        def repl(m):
            raw=m.group(2).strip()
            if not raw or raw.startswith(('data:','#')): return m.group(0)
            local=self.mirror_asset(urljoin(page_url,raw),page_url)
            return f'url("{local}")'
        return CSS_URL_RE.sub(repl,text)

    def archive_page(self,path,outname):
        url=urljoin(BASE,path)
        html,r=self.fetch(url,binary=False)
        page_url=r.url
        soup=BeautifulSoup(html,'html.parser')

        # images and lazy images
        for img in soup.find_all('img'):
            best=self.best_image_url(img,page_url)
            if best:
                local=self.mirror_asset(best,page_url)
                img['src']=local
                for a in ('data-src','data-image','data-original','srcset','data-srcset'):
                    img.attrs.pop(a,None)

        # picture/source
        for source in soup.find_all('source'):
            src=source.get('src')
            srcset=source.get('srcset')
            chosen=None
            if srcset:
                bits=[x.strip().split()[0] for x in srcset.split(',') if x.strip()]
                if bits: chosen=urljoin(page_url,bits[-1])
            elif src: chosen=urljoin(page_url,src)
            if chosen:
                local=self.mirror_asset(chosen,page_url)
                source['srcset']=local
                source.attrs.pop('src',None)

        # stylesheets/icons
        for link in soup.find_all('link'):
            href=link.get('href')
            if not href: continue
            rels={x.lower() for x in link.get('rel',[])}
            if 'stylesheet' in rels:
                link['href']=self.mirror_stylesheet(href,page_url)
            elif rels & {'icon','shortcut','apple-touch-icon','mask-icon'}:
                link['href']=self.mirror_asset(href,page_url)

        # JS: save locally so a future CDN shutdown does not remove basic interactions.
        for script in soup.find_all('script'):
            src=script.get('src')
            if src and not src.startswith('data:'):
                script['src']=self.mirror_asset(src,page_url)

        # background images in inline style attributes / style blocks
        for tag in soup.find_all(style=True):
            tag['style']=self.rewrite_inline_css(tag['style'],page_url)
        for style in soup.find_all('style'):
            if style.string:
                style.string.replace_with(self.rewrite_inline_css(style.string,page_url))

        # internal navigation -> local static pages
        for a in soup.find_all('a',href=True):
            href=a['href']
            if href.startswith(('mailto:','tel:','javascript:','#')): continue
            absolute=urljoin(page_url,href)
            p=urlparse(absolute)
            if p.netloc == urlparse(BASE).netloc:
                clean=p.path.rstrip('/') or '/'
                # account for old .html variants
                if clean.endswith('.html'): clean=clean[:-5] or '/'
                if clean in PAGES:
                    target=PAGES[clean]
                    if p.fragment: target += '#'+p.fragment
                    a['href']=target

        # meta canonical/open graph still pointing at old host isn't needed in archive.
        for m in soup.find_all('meta'):
            if m.get('property') in {'og:url'}: m['content']=outname
        can=soup.find('link',rel='canonical')
        if can: can['href']=outname

        (self.out/outname).write_text(str(soup),encoding='utf-8')
        print(f'Archived {page_url} -> {outname}')

    def run(self):
        for p,n in PAGES.items(): self.archive_page(p,n)
        (self.out/'.nojekyll').write_text('',encoding='utf-8')
        report=[
            '# BMFL live-site archive report','',
            f'- Source: {BASE}',
            f'- Pages archived: {len(PAGES)}',
            f'- Assets downloaded locally: {self.downloaded}',
            f'- Failed asset requests: {len(self.failures)}',''
        ]
        if self.failures:
            report += ['## Failed assets',''] + [f'- `{u}` — {e}' for u,e in self.failures]
        (self.out/'MIRROR_REPORT.md').write_text('\n'.join(report),encoding='utf-8')
        print(f'Done: {self.downloaded} assets, {len(self.failures)} failures')

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',default='docs')
    args=ap.parse_args()
    out=Path(args.out)
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True)
    Archiver(out).run()
