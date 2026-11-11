#!/usr/bin/env python3
"""Extract registered PyTorch API names from objects.inv without fetching HTML."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import unquote, urldefrag, urljoin
import zlib

BASE_URL = "https://docs.pytorch.org/docs/{version}"
_cache_dir = None

API_ROLES = {'py:function', 'py:class', 'py:exception', 'py:method',
             'py:attribute', 'py:property', 'py:data'}


def normalize_name(name):
    if name.startswith('Tensor.'):
        return 'torch.' + name
    return name[len('torch.'):] if name.startswith('torch.torch.') else name


def fetch_inventory(base_url):
    """Read binary inventory; keep numeric-version caching and fresh main reads."""
    url = base_url + '/objects.inv'
    version = base_url.rsplit('/', 1)[-1]
    cached = (os.path.join(_cache_dir, version, 'objects.inv')
              if _cache_dir and re.fullmatch(r'[\d.]+', version) else None)
    if cached and os.path.isfile(cached):
        with open(cached, 'rb') as stream:
            return stream.read()
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'ApiDiff/1.0'})
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            break
        except urllib.error.HTTPError:
            raise
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    if cached:
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with open(cached, 'wb') as stream:
            stream.write(data)
    return data


def inventory_targets(data, base_url):
    """Read canonical API targets registered in the official inventory."""
    header = data.split(b'\n', 4)
    if len(header) != 5 or header[0].rstrip(b'\r') != b'# Sphinx inventory version 2':
        raise ValueError('Unsupported or malformed objects.inv')
    targets = {}
    for line in zlib.decompress(header[4]).decode('utf-8').splitlines():
        match = re.match(r'(.+?)\s+(\S+)\s+(-?\d+)\s+(\S+)\s+(.*)$', line)
        if not match:
            raise ValueError('Malformed inventory record: ' + line)
        original, role, _, uri, _ = match.groups()
        if role not in API_ROLES or not original.startswith(('torch.', 'Tensor.')):
            continue
        if uri.endswith('$'):
            uri = uri[:-1] + original
        page, anchor = urldefrag(urljoin(base_url + '/', uri))
        name = normalize_name(unquote(anchor))
        if not name.startswith('torch.') or not page.startswith(base_url + '/'):
            raise ValueError('API entry has no local declaration target: ' + original)
        if name in targets and targets[name] != page:
            raise ValueError('Conflicting declaration pages for ' + name)
        targets[name] = page
    if not targets:
        raise ValueError('Inventory contains no documented PyTorch APIs')
    return targets


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

    print(f"Fetching {base}/objects.inv ...")
    inventory = fetch_inventory(base)
    targets = inventory_targets(inventory, base)
    result = {}
    for name in targets:
        module, short = name.rsplit('.', 1)
        result.setdefault(module, []).append(short)
    sigs, sources = {}, {}
    for module in result:
        result[module] = sorted(set(result[module]))
    result["_signatures"] = sigs
    result["_sources"] = sources
    print(f"  Found {len(targets)} APIs in {len(result) - 2} modules")

    # Keep the established JSON structure and deterministic ordering.
    sorted_result = {k: result[k] for k in sorted(result.keys(),
        key=lambda k: (k.startswith("_"), k))}
    sorted_result["_signatures"] = {k: sigs[k] for k in sorted(sigs)}
    sorted_result["_sources"] = {k: sources[k] for k in sorted(sources)}
    with open(output, "w", encoding="utf-8") as f:
        json.dump(sorted_result, f, indent=2, ensure_ascii=False)

    print(f"\nOutput: {output}")


if __name__ == "__main__":
    main()
