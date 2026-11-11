"""Bounded recovery for OBS metadata queries; download and selection stay unchanged."""
import logging
import re
import subprocess
import time

QUERY_ATTEMPTS = 2
QUERY_RETRY_INTERVAL = 1


def failure_kind(output):
    codes = re.findall(r'(?:status\s*code|statuscode|http\s*status)\s*[:=]?\s*(\d{3})\b',
                       output, re.IGNORECASE)
    if any(c in {'400', '401', '403'} for c in codes):
        return 'fatal'
    if '404' in codes or re.search(r'\bNoSuchKey\b', output, re.IGNORECASE):
        return 'missing'
    if any(c in {'408', '429', '500', '502', '503', '504'} for c in codes):
        return 'retry'
    if re.search(r'timed?\s*out|timeout|connection\s+(?:reset|refused|aborted)|'
                 r'temporary failure|network is unreachable|unexpected EOF', output, re.IGNORECASE):
        return 'retry'
    return 'fatal'


def query(run, args, *, timeout, error_type, allow_missing=False):
    for attempt in range(1, QUERY_ATTEMPTS + 1):
        try:
            proc = run(args, timeout=timeout)
        except error_type as error:
            if not isinstance(error.__cause__, subprocess.TimeoutExpired) or attempt == QUERY_ATTEMPTS:
                raise
        else:
            if proc.returncode == 0:
                return proc
            detail = '\n'.join(x for x in (proc.stderr, proc.stdout) if x)
            kind = failure_kind(detail)
            if allow_missing and kind == 'missing':
                return proc  # The original stat() handles the explicit missing result.
            if kind != 'retry' or attempt == QUERY_ATTEMPTS:
                raise error_type(f'obsutil {args[0]} 失败 (exit={proc.returncode}): '
                                 f'{args[1]}\n{detail[-1500:]}')
        logging.getLogger(__name__).warning('OBS %s transient failure; retry %s/%s for %s',
                                           args[0], attempt + 1, QUERY_ATTEMPTS, args[1])
        time.sleep(QUERY_RETRY_INTERVAL)
