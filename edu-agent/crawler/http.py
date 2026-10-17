"""HTTP 抓取层：统一 UA、超时、重试、编码识别、按域名礼貌限速。

只用标准库，零依赖 —— 现场演示时不需要装任何东西就能跑。
"""
import re
import ssl
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

_last_hit = {}


class FetchError(RuntimeError):
    pass


def _decode(raw: bytes, content_type: str) -> str:
    m = re.search(r"charset=[\"']?([\w-]+)", content_type or "", re.I)
    if m:
        try:
            return raw.decode(m.group(1), "ignore")
        except LookupError:
            pass
    head = raw[:4096].decode("ascii", "ignore")
    m = re.search(r"charset=[\"']?([\w-]+)", head, re.I)
    if m:
        try:
            return raw.decode(m.group(1), "ignore")
        except LookupError:
            pass
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def _polite(host: str, delay: float):
    """同一域名两次请求之间至少间隔 delay 秒。"""
    if delay <= 0:
        return
    now = time.time()
    wait = _last_hit.get(host, 0.0) + delay - now
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()


def fetch(url: str, timeout: int = 25, retries: int = 2, delay: float = 1.0):
    """抓一个页面，返回 (html, status, final_url)。失败抛 FetchError。"""
    host = re.sub(r"^https?://", "", url).split("/")[0]
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    last_err = None
    for attempt in range(retries + 1):
        _polite(host, delay)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                raw = r.read()
                final = r.geturl()
                status = r.status
                ctype = r.headers.get("Content-Type", "")
            if url.lower().endswith(".pdf") or "application/pdf" in ctype:
                return raw, status, final
            return _decode(raw, ctype), status, final
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in (403, 404, 410):
                break
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise FetchError(f"{url} 抓取失败：{last_err}")


def fetch_binary(url: str, timeout: int = 40, delay: float = 1.0) -> bytes:
    """抓二进制内容（PDF 等）。"""
    host = re.sub(r"^https?://", "", url).split("/")[0]
    _polite(host, delay)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.read()
