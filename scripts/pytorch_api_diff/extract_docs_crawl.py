#!/usr/bin/env python3
"""
Docs Crawl v2: scrape PyTorch official documentation (docs.pytorch.org).

Parses https://docs.pytorch.org/docs/<version>/pytorch-api.html to extract
all Python API names from the toctree and generated/ links, then fetches
individual API pages in parallel for signature data.

Output: {module: [short names], _signatures: {name: {params, returns}}, _sources: {name: url}} JSON.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://docs.pytorch.org/docs/{version}"
API_PAGE = "{base}/pytorch-api.html"
# Each <dt class="sig..."> block: group 1 = opening tag, group 2 = inner HTML.
# Class pages list every method as its own <dt> — iterate all of them so
# method signatures aren't lost.
DT_SIG_RE = re.compile(
    r'(<dt[^>]*class="[^"]*sig[^"]*"[^>]*>)(.*?)</dt>', re.DOTALL
)
ID_IN_TAG_RE = re.compile(r'id="(torch\.[^"]+)"')
# Match <dl class="py ..."><dt id="..."> blocks. Group 1 = type, Group 2 = id.
DL_ID_RE = re.compile(
    r'<dl class="(py \w+)"[^>]*>.*?<dt[^>]*id="(torch\.[^"]+)"',
    re.DOTALL,
)
# GitHub source link on API pages (for the _sources provenance field).
SOURCE_RE = re.compile(
    r'https://github\.com/pytorch/pytorch/blob/[^"]+'
)
PARAM_RE = re.compile(r'<em class="sig-param">(.*?)</em>', re.DOTALL)
RET_RE = re.compile(
    r'<span class="sig-return">.*?<span[^>]*class="[^"]*sig-return-typehint[^"]*"[^>]*>(.*?)</span>', re.DOTALL
)
TAG_RE = re.compile(r'<[^>]+>')
WS_RE = re.compile(r'\s+')

_cache_dir: str | None = None


def fetch(url: str, timeout: int = 30) -> str:
    """Fetch a URL, using local cache if available. Caches result on first fetch.

    Cache layout: {cache_dir}/{version}/generated/torch.abs.html
    The main page is cached as pytorch-api.html directly under the version dir.

    404 responses are negatively cached: a `{cache_file}.404` marker file is
    written so later runs raise HTTPError(404) without a network round-trip
    (the entry-page probe pytorch-api.html -> 404 -> index.html fallback hits
    this on every run for versions without pytorch-api.html).

    Version is self-extracted from the URL — no dependency on global state.
    """
    if _cache_dir:
        import re as _re
        m = _re.search(r'/docs/([\d.]+)/', url)
        if not m:
            return _fetch_http(url, timeout)
        version = m.group(1)

        if url.endswith("/pytorch-api.html") or url.endswith("/index.html"):
            rel = "pytorch-api.html" if url.endswith("/pytorch-api.html") else "index.html"
        elif "/generated/" in url:
            rel = url.split("/docs/" + version + "/")[-1]
        elif "/docs/" + version + "/" in url:
            # Module pages: torch.html, nn.html, etc.
            rel = url.split("/docs/" + version + "/")[-1]
        else:
            return _fetch_http(url, timeout)

        cache_file = os.path.join(_cache_dir, version, rel)
        if os.path.exists(cache_file):
            with open(cache_file, encoding="utf-8", errors="replace") as f:
                return f.read()
        neg_file = cache_file + ".404"
        if os.path.exists(neg_file):
            raise urllib.error.HTTPError(
                url, 404, "Not Found (negative cache)", None, None)
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        try:
            html = _fetch_http(url, timeout)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                with open(neg_file, "w", encoding="utf-8") as f:
                    f.write("404")
            raise
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(html)
        return html
    return _fetch_http(url, timeout)


def _fetch_http(url: str, timeout: int = 30) -> str:
    """Fetch a URL over HTTP, with retries on transient network errors only."""
    import time as _time
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ApiDiff/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError:
            raise  # 404 etc. — don't retry
        except Exception as e:
            last_err = e
            if attempt < 2:
                _time.sleep(2 ** attempt)
    raise last_err


def _normalize_name(name: str) -> str:
    """Normalize a Sphinx-generated API name.

    Sphinx occasionally emits duplicated module prefixes (e.g.
    "torch.torch.finfo" — the :module: directive concatenated with the
    object name). There is no torch.torch submodule, so one prefix is
    always safe to strip.
    """
    if name.startswith("torch.torch."):
        return name[len("torch."):]
    return name


def _extract_module_links(html: str) -> list[tuple[str, str]]:
    """Extract toctree links within the 'Python API' toctree-wrapper compound.

    v2.7 index.html has many toctree sections; only the one under
    <p class="caption">Python API</p> contains the module pages we want.
    Returns empty list if no such section found.
    """
    # Find the Python API toctree section (v2.7 uses <span class="caption-text">)
    m = re.search(
        r'<p class="caption"[^>]*>.*?Python API.*?</p>\s*<ul>(.*?)</ul>',
        html, re.DOTALL,
    )
    if not m:
        return []
    section_html = m.group(1)
    link_pattern = re.compile(
        r'<li class="toctree-l1[^"]*">\s*'
        r'<a[^>]*class="[^"]*reference[^"]*"[^>]*href="([^"]+)"[^>]*>'
        r'([^<]+)'
        r'</a>',
        re.DOTALL,
    )
    return [(m.group(1), m.group(2).strip()) for m in link_pattern.finditer(section_html)]


def _extract_apis_from_module_page(mod_html: str) -> tuple[set[str], dict[str, dict], dict[str, str], set[str]]:
    """Extract API names, signatures, href mapping, and inline dt names from a module page.

    v2.7:  inline id=\"torch.xxx\" on <dt class=\"sig\"> — names + signatures.
    v2.8-2.10: href=\"generated/xxx.html\" links — names + URL mapping.

    Returns (api_names, signatures, href_map, dt_names).
    href_map maps FQN → \"generated/...\" path for correct URL construction.
    dt_names are APIs documented inline on this page -- they may have no
    dedicated generated/ page (fetched URL would 404).
    """
    apis: set[str] = set()
    sigs: dict[str, dict] = {}
    href_map: dict[str, str] = {}
    dt_names: set[str] = set()

    # Pattern: <dt class="sig sig-object py" id="torch.xxx"> ... </dt>
    dt_pattern = re.compile(
        r'<dt[^>]*class="[^"]*sig[^"]*"[^>]*id="(torch\.[^"]+)"[^>]*>'
        r'(.*?)</dt>',
        re.DOTALL,
    )
    for m in dt_pattern.finditer(mod_html):
        api_name = m.group(1)
        # Sphinx quirk: on the top-level torch module page the ids get a
        # doubled prefix (torch.torch.default_generator → torch.default_generator).
        # There is no torch.torch module, so strip it.
        if api_name.startswith("torch.torch."):
            api_name = api_name[len("torch."):]
        if "(" in api_name:
            continue
        apis.add(api_name)
        dt_names.add(api_name)

        # Extract parameter info from the dt content
        sig_html = m.group(2)
        params = []
        for pm in PARAM_RE.finditer(sig_html):
            text = WS_RE.sub(' ', TAG_RE.sub(' ', pm.group(1))).strip()
            if text and text != '*':
                parsed = _parse_param(text)
                if parsed:
                    params.append(parsed)
        ret_m = RET_RE.search(sig_html)
        returns = None
        if ret_m:
            returns = WS_RE.sub(' ', TAG_RE.sub(' ', ret_m.group(1))).strip()
        sigs[api_name] = {"params": params, "returns": returns}

    # Extract generated/ links and record href mapping (v2.8-2.10)
    # Format: href="generated/torch.abs.html" or href="generated/torch.abs.html#torch.abs"
    for m in re.finditer(r'href="(generated/[^"]+\.html)(?:#[^"]*)?"', mod_html):
        clean_href = m.group(1)  # "generated/torch.abs.html" (no anchor)
        api_name = _normalize_name(clean_href[len("generated/"):-len(".html")].replace("/", "."))
        if api_name.startswith("torch.") and "(" not in api_name:
            apis.add(api_name)
            # Prefer first-seen href (usually from the primary module page)
            if api_name not in href_map:
                href_map[api_name] = clean_href

    return apis, sigs, href_map, dt_names


def _resolve_suffix_pairs(module_apis: dict[str, set[str]]) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Resolve _class / _function Sphinx disambiguation suffixes."""
    suffix_pairs: set[str] = set()
    for names in module_apis.values():
        has_func: set[str] = set()
        has_class: set[str] = set()
        for n in names:
            if n.endswith("_function"):
                has_func.add(n[:-len("_function")].lower())
            elif n.endswith("_class"):
                has_class.add(n[:-len("_class")].lower())
        suffix_pairs.update(has_func & has_class)

    href_map: dict[str, str] = {}
    resolved: dict[str, set[str]] = defaultdict(set)
    for mod, names in module_apis.items():
        for n in names:
            href_fname = "generated/" + n + ".html"
            if n.endswith("_class"):
                base = n[:-len("_class")]
                if base.lower() in suffix_pairs:
                    resolved[mod].add(base)
                    href_map[base] = href_fname
                else:
                    resolved[mod].add(n)
            elif n.endswith("_function") and n[:-len("_function")].lower() in suffix_pairs:
                resolved[mod].add(n[:-len("_function")])
                href_map[n[:-len("_function")]] = href_fname
            elif n.endswith("_function"):
                resolved[mod].add(n)
            else:
                resolved[mod].add(n)
    return dict(resolved), href_map


def _crawl_module_pages(module_hrefs: list[str], base_url: str,
                        module_apis: dict[str, set[str]],
                        inline_sigs: dict[str, dict],
                        href_map: dict[str, str]) -> set[str]:
    """Crawl module-level pages (e.g. distributed.html), merging their APIs.

    Module pages contribute API names via inline <dt id="torch.xxx"> blocks and
    generated/ links, plus inline signatures for APIs that have no dedicated
    generated/ page (e.g. v2.8 Store methods).

    Returns the set of names documented inline on the crawled pages.
    """
    module_hrefs = list(dict.fromkeys(module_hrefs))  # dedupe, keep order
    dt_names: set[str] = set()
    print(f"  Crawling {len(module_hrefs)} module pages ...", end="", flush=True)
    for i, mod_href in enumerate(module_hrefs):
        mod_url = f"{base_url}/{mod_href}"
        try:
            mod_html = fetch(mod_url)
            apis, sigs, mod_href_map, page_dt_names = _extract_apis_from_module_page(mod_html)
            for api_name in apis:
                parts = api_name.rsplit(".", 1)
                mod = parts[0] if len(parts) > 1 else "torch"
                module_apis[mod].add(api_name)
            inline_sigs.update(sigs)
            dt_names.update(page_dt_names)
            # Collect href mappings for correct URL construction
            for fqn, path in mod_href_map.items():
                if fqn not in href_map:
                    href_map[fqn] = path
        except Exception:
            pass  # skip unreachable pages
        if (i + 1) % 10 == 0:
            print(f" {i + 1}", end="", flush=True)
    print(f" {len(module_hrefs)} done", flush=True)
    return dt_names


def extract_apis_from_api_page(html: str, base_url: str = "") -> tuple[dict[str, set[str]], dict[str, str], dict[str, dict], str, set[str]]:
    """Parse API index page to extract all API names from the toctree.

    Supports three formats:
    - v2.11+: toctree l2/l3 links directly to generated/ pages → \"direct\"
    - v2.8-2.10: toctree l1 links to module pages → generated/ links → \"modules\"
    - v2.7: toctree l1 links to module pages → inline id=\"torch.xxx\" → \"inline\"

    Returns (module_apis, href_map, inline_sigs, format, inline_only).
    inline_only: names documented ONLY inline on module pages -- no
    generated/ link points to them, so their generated/ URL would 404.
    Their signatures come from the inline extraction (inline_sigs).
    """
    module_apis: dict[str, set[str]] = defaultdict(set)
    href_map: dict[str, str] = {}
    inline_sigs: dict[str, dict] = {}

    # ── Step 1: extract toctree links (l1/l2/l3) ──
    toc_pattern = re.compile(
        r'<li class="toctree-l[123][^"]*">\s*'
        r'<a[^>]*class="[^"]*reference[^"]*"[^>]*href="([^"]+)"[^>]*>'
        r'([^<]+)'
        r'</a>',
        re.DOTALL,
    )

    toc_links: list[tuple[str, str]] = [(m.group(1), m.group(2).strip())
                                         for m in toc_pattern.finditer(html)]

    # ── Step 2: direct generated/ links (v2.11+) ──
    gen_links = [(h, t) for h, t in toc_links if h.startswith("generated/")]
    direct_names: set[str] = set()
    if gen_links:
        for href, _link_text in gen_links:
            api_name = _normalize_name(href[len("generated/"):-len(".html")].replace("/", "."))
            if not api_name.startswith("torch.") or "(" in api_name:
                continue
            parts = api_name.rsplit(".", 1)
            mod = parts[0] if len(parts) > 1 else "torch"
            module_apis[mod].add(api_name)
            direct_names.add(api_name)

        # v2.11+ still documents some submodules (torch.distributed, ...) on
        # module-level pages instead of generated/ pages — crawl those too.
        module_hrefs = [h for h, _ in toc_links
                        if h.endswith(".html")
                        and "/" not in h
                        and not h.startswith(("generated/", "http"))]
        dt_names: set[str] = set()
        if module_hrefs and base_url:
            dt_names = _crawl_module_pages(module_hrefs, base_url, module_apis,
                                           inline_sigs, href_map)

        _extract_ref_links(html, module_apis)
        resolved, suffix_href_map = _resolve_suffix_pairs(module_apis)
        for fqn, path in suffix_href_map.items():
            if fqn not in href_map:
                href_map[fqn] = path
        # Names only known inline (no generated/ link anywhere) have no page.
        inline_only = dt_names - set(href_map.keys()) - direct_names
        return resolved, href_map, inline_sigs, "direct", inline_only

    # ── Step 3: module-level pages (v2.7–v2.10) ──
    # v2.7:  index.html Python API section → module pages with inline id="torch.xxx"
    # v2.8-2.10: pytorch-api.html → module pages with generated/ links
    py_api_links = _extract_module_links(html)
    if not py_api_links:
        py_api_links = toc_links
    module_hrefs = [h for h, _ in py_api_links
                    if h.endswith(".html")
                    and "/" not in h  # exclude user_guide/ etc.
                    and not h.startswith(("generated/", "http"))]
    dt_names = _crawl_module_pages(module_hrefs, base_url, module_apis,
                                   inline_sigs, href_map) if module_hrefs and base_url else set()

    import time as _t; _ts = _t.time()
    resolved, suffix_href_map = _resolve_suffix_pairs(module_apis)
    print(f" [{_t.time()-_ts:.1f}s]", end="", flush=True)
    # Merge: module-page href_map (from generated/ links) takes priority,
    # suffix-resolution entries fill in the rest
    for fqn, path in suffix_href_map.items():
        if fqn not in href_map:
            href_map[fqn] = path
    # v2.7: index.html entry, inline id="torch.xxx" anchors on module pages
    # v2.8-2.10: pytorch-api.html entry, generated/ links on module pages
    fmt = "modules"
    inline_only = dt_names - set(href_map.keys())
    return resolved, href_map, inline_sigs, fmt, inline_only


def _extract_ref_links(html: str, module_apis: dict[str, set[str]]) -> None:
    """Extract reference internal links (edge cases outside toctree)."""
    ref_pattern = re.compile(
        r'<a[^>]*class="[^"]*reference internal[^"]*"[^>]*href="generated/([^"]+)"[^>]*>'
        r'([^<]+)'
        r'</a>',
        re.DOTALL,
    )
    for match in ref_pattern.finditer(html):
        href = match.group(1)
        api_name = _normalize_name(href[:-len(".html")].replace("/", "."))
        if api_name.startswith("torch.") and "(" not in api_name:
            parts = api_name.rsplit(".", 1)
            mod = parts[0] if len(parts) > 1 else "torch"
            module_apis[mod].add(api_name)


def _parse_param(text: str) -> dict | None:
    """Parse 'input :  Tensor  =  None' into {name, type, default}.

    Untyped params render as 'name = default' (no colon) — split those as
    well, otherwise the whole string would land in the name field and
    break name-based param diffing downstream.
    """
    text = text.strip()
    parts = text.split(":", 1)
    name = parts[0].strip()
    if not name:
        return None
    type_with_default = parts[1].strip() if len(parts) > 1 else ""
    default = None
    ptype = type_with_default
    if "=" in type_with_default:
        ptype, default = type_with_default.rsplit("=", 1)
        ptype = ptype.strip()
        default = default.strip()
    elif "=" in name:
        name, default = name.rsplit("=", 1)
        name = name.strip()
        default = default.strip()
    return {"name": name, "type": ptype or None, "default": default}


def _extract_dt_signatures(html: str) -> dict[str, dict]:
    """Extract signature for every <dt class="sig" id="torch.xxx"> block.

    Class pages (e.g. generated/torch.optim.Adam.html) contain one <dt> per
    method in addition to the class itself — all of them carry signatures.

    Overloaded functions render as several consecutive <dt> blocks: the first
    carries the id, its siblings have none. Sibling signatures are collected
    into the entry's "variants" list (params+returns each), keeping every
    Python-visible overload form instead of silently dropping it.
    """
    def _parse_sig_dt(sig_html: str) -> dict:
        params = []
        for pm in PARAM_RE.finditer(sig_html):
            text = WS_RE.sub(' ', TAG_RE.sub(' ', pm.group(1))).strip()
            if text and text != '*':
                parsed = _parse_param(text)
                if parsed:
                    params.append(parsed)
        ret_m = RET_RE.search(sig_html)
        returns = None
        if ret_m:
            returns = WS_RE.sub(' ', TAG_RE.sub(' ', ret_m.group(1))).strip()
        return {"params": params, "returns": returns}

    sigs: dict[str, dict] = {}
    last_id: str | None = None
    for m in DT_SIG_RE.finditer(html):
        id_m = ID_IN_TAG_RE.search(m.group(1))
        sig = _parse_sig_dt(m.group(2))
        if id_m:
            last_id = id_m.group(1)
            sigs[last_id] = sig
        elif last_id is not None:
            # id-less sibling <dt> = another overload of the same API
            sigs[last_id].setdefault("variants", []).append(sig)
    return sigs


def extract_signature_and_name(html: str) -> tuple[dict[str, dict], str | None, list[str], str | None]:
    """Extract all signatures on the page, canonical name, sub-API names, and source link.

    Returns (sigs, name, sub_entries, source) where sigs maps every
    <dt id="torch.xxx"> on the page to its {params, returns}.
    """
    # Source code link
    source = None
    sm = SOURCE_RE.search(html)
    if sm:
        source = sm.group()

    # All API names on this page (all dl types kept -- py attribute /
    # py data are public API too)
    all_matches = [(m.group(2), m.group(1)) for m in DL_ID_RE.finditer(html)]
    all_ids = [name for name, _ in all_matches]

    # Canonical name = first id
    name = all_ids[0] if all_ids else None

    sigs = _extract_dt_signatures(html)
    return sigs, name, all_ids[1:], source


def fetch_signatures(result: dict, base_url: str,
                     max_workers: int = 16,
                     href_map: dict[str, str] | None = None,
                     inline_only: set[str] | None = None) -> tuple[dict, dict[str, str]]:
    """Fetch generated/ pages in parallel. Returns (signatures, name_corrections).

    name_corrections maps href-derived names to canonical names from page content.
    href_map maps resolved_fqn → original href filename (for _class/_function suffixes).
    inline_only names are skipped -- they have no generated/ page (404) and
    their signatures are backfilled from the module-page inline extraction.
    """
    api_urls = {}
    skipped = 0
    for mod, names in result.items():
        for name in names:
            # Reconstruct full qualified name for URL (name may be short)
            fqn = name if name.startswith(mod) else f"{mod}.{name}"
            if inline_only and fqn in inline_only:
                skipped += 1
                continue
            if href_map and fqn in href_map:
                api_urls[fqn] = f"{base_url}/{href_map[fqn]}"  # href_map has "generated/..." path
            else:
                api_urls[fqn] = f"{base_url}/generated/{fqn}.html"
    if skipped:
        print(f"  Skipping {skipped} inline-only APIs (no generated/ page; "
              f"signatures from module pages)")

    signatures = {}
    name_corrections: dict[str, str] = {}
    sub_apis: set[str] = set()
    sources: dict[str, str] = {}
    done = 0
    errors = 0
    total = len(api_urls)

    def fetch_one(name, url):
        try:
            sigs, canonical, sub_entries, source = extract_signature_and_name(
                fetch(url, timeout=10))
            correction = None
            if canonical and canonical != name:
                correction = canonical
            return name, sigs, correction, sub_entries or [], source
        except Exception:
            return name, None, None, [], None

    print(f"  Fetching signatures for {total} APIs "
          f"({max_workers} workers, cache: {_cache_dir}) ...")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, name, urls): name
                   for name, urls in api_urls.items()}
        for future in as_completed(futures):
            name, sigs, correction, subs, source = future.result()
            done += 1
            if sigs is None:
                errors += 1
            else:
                for sig_name, sig in sigs.items():
                    # A page may be fetched multiple times (method's own page
                    # and its class page) — never let an empty signature
                    # overwrite a non-empty one.
                    if sig_name not in signatures or (
                            not signatures[sig_name].get("params")
                            and sig.get("params")):
                        signatures[sig_name] = sig
            if correction:
                name_corrections[name] = correction
            sub_apis.update(subs)
            if source:
                sources[name] = source
            if done % 500 == 0 or done == total:
                print(f"    {done}/{total} ({len(signatures)} sigs, "
                      f"{errors} errors, "
                      f"{len(name_corrections)} corrections, "
                      f"{len(sub_apis)} sub-APIs)")

    print(f"    Done: {len(signatures)} signatures, "
          f"{errors} errors, "
          f"{len(name_corrections)} corrections, "
          f"{len(sub_apis)} sub-APIs")
    return signatures, name_corrections, sub_apis, sources


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <version> [output.json] [--cache-dir DIR]", file=sys.stderr)
        print(f"Example: {sys.argv[0]} 2.13.0 output/pytorch_diff/2026-08-07/v2.13.0_docs_api.json --cache-dir .docs_cache", file=sys.stderr)
        sys.exit(1)

    args = sys.argv[1:]
    global _cache_dir
    # 缓存目录：默认 .docs_cache（相对项目根），--cache-dir 可覆盖
    _cache_dir = ".docs_cache"
    if "--cache-dir" in args:
        idx = args.index("--cache-dir")
        _cache_dir = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    version = args[0]
    output = args[1] if len(args) > 1 else "api.json"

    # Docs URL uses 'major.minor' (e.g., '2.13', '2.12')
    parts = version.split(".")
    version_short = ".".join(parts[:2])

    base = BASE_URL.format(version=version_short)

    # ── Determine entry page ──
    # v2.8+ use pytorch-api.html; v2.7 and earlier use index.html
    # (pytorch-api.html 404s there). Probe instead of hardcoding the
    # version list so older releases keep working.
    api_page_url = f"{base}/pytorch-api.html"
    print(f"Fetching {api_page_url} ...")
    try:
        html = fetch(api_page_url)
    except urllib.error.HTTPError:
        api_page_url = f"{base}/index.html"
        print(f"  404 -- falling back to {api_page_url} ...")
        html = fetch(api_page_url)

    print("Extracting APIs from toctree ...")
    module_apis, href_map, inline_sigs, _, inline_only = extract_apis_from_api_page(html, base)
    result = {}
    for mod, apis in module_apis.items():
        prefix = mod + "."
        short_apis = set()
        for api in apis:
            if api.startswith(prefix):
                short_apis.add(api[len(prefix):])
            else:
                short_apis.add(api)
        result[mod] = sorted(short_apis)
    total = sum(len(v) for v in result.values())
    print(f"  Found {total} APIs in {len(result)} modules")

    top = sorted(result.items(), key=lambda x: len(x[1]), reverse=True)
    print("  Top modules:", ", ".join(f"{m}:{len(n)}" for m, n in top[:8]))

    # ── Fetch signatures ──
    # All formats follow the same flow: fetch generated/ pages in parallel
    # (skipping inline-only names that have no dedicated page), then backfill
    # the rest from the module-page inline extraction.
    sigs, name_corrections, sub_apis, sources = fetch_signatures(
        result, base, max_workers=16, href_map=href_map, inline_only=inline_only)
    # Backfill signatures extracted inline on module pages for APIs whose
    # generated/ page fetch failed or was skipped (inline-only APIs have no
    # dedicated pages). Fetched signatures take priority.
    if inline_sigs:
        backfilled = 0
        for name, sig in inline_sigs.items():
            if name not in sigs or (
                    not sigs[name].get("params") and sig.get("params")):
                sigs[name] = sig
                backfilled += 1
        if backfilled:
            print(f"  Backfilled {backfilled} signatures from module pages")

    # Add sub-APIs discovered from individual pages (class methods etc.)
    if sub_apis:
        added = 0
        for name in sub_apis:
            if name.startswith("torch.") and "(" not in name:
                mod = ".".join(name.rsplit(".", 1)[:-1]) or "torch"
                short = name.rsplit(".", 1)[-1]
                if short not in result.get(mod, []):
                    result.setdefault(mod, []).append(short)
                    added += 1
                # Propagate source link from parent API
                for parent, url in sources.items():
                    if name.startswith(parent + "."):
                        sources[name] = url
                        break
        if added:
            print(f"  Added {added} sub-APIs from page content")
            for mod in result:
                if mod not in ("_signatures", "_sources"):
                    result[mod] = sorted(result[mod])

    # Apply canonical name corrections from page content
    if name_corrections:
        print(f"\nApplying {len(name_corrections)} name corrections ...")
        for old_name, new_name in name_corrections.items():
            # Remove old name from its module (values are short names)
            old_mod = ".".join(old_name.rsplit(".", 1)[:-1]) or "torch"
            old_short = old_name.rsplit(".", 1)[-1]
            if old_mod in result and old_short in result[old_mod]:
                result[old_mod].remove(old_short)
            # Add new name (as short name, stripping new_mod prefix)
            new_mod = ".".join(new_name.rsplit(".", 1)[:-1]) or "torch"
            new_short = new_name.rsplit(".", 1)[-1]
            result.setdefault(new_mod, []).append(new_short)
            # Move signature and source
            if old_name in sigs:
                sigs[new_name] = sigs.pop(old_name)
            if old_name in sources:
                sources[new_name] = sources.pop(old_name)
            print(f"  {old_name} → {new_name}")
        # Re-sort
        for mod in result:
            if mod not in ("_signatures", "_sources"):
                result[mod] = sorted(set(result[mod]))
        result["_signatures"] = sigs

    result["_signatures"] = sigs
    result["_sources"] = sources

    # Sort keys for stable output: module keys A-Z, then _signatures / _sources
    # at end. _signatures keys are sorted too — insertion order follows thread
    # completion order in fetch_signatures, which varies run to run.
    sorted_result = {k: result[k] for k in sorted(result.keys(),
        key=lambda k: (k.startswith("_"), k))}
    sorted_result["_signatures"] = {k: sigs[k] for k in sorted(sigs)}
    sorted_result["_sources"] = {k: sources[k] for k in sorted(sources)}
    with open(output, "w", encoding="utf-8") as f:
        json.dump(sorted_result, f, indent=2, ensure_ascii=False)

    print(f"\nOutput: {output}")


if __name__ == "__main__":
    main()
