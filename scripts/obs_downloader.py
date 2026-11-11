"""AI_LOOP-owned obsutil transport; credentials stay in obsutil configuration."""

from __future__ import annotations

import subprocess
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from obs_query_retry import query

ROOT = Path(__file__).resolve().parents[1]

# 超时分级：stat/ls 是控制面查询；下载按最坏包体（779MB 源码包）× 链路
# 劣化（~500KB/s ≈ 26 分钟）校准。原先全局 3600s 在链路挂死时会让单个
# 任务烧 1 小时（2026-09-18 实证）；900s 又会误杀慢速链路下的大包下载
# （同日实证：779MB @ <900KB/s 两次尝试全超时）。
STAT_LS_TIMEOUT = 120
DOWNLOAD_TIMEOUT = 1800
DOWNLOAD_ATTEMPTS = 2
DOWNLOAD_RETRY_INTERVAL = 5


class ObsDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadedObject:
    url: str
    dest: Path
    size: int


class ObsutilDownloader:
    """通过 obsutil CLI 下载对象（obs://bucket/path → 本地路径）。"""

    def __init__(self, bin_path: str = "obsutil", *, regional_proxy_endpoint=None, bucket=None):
        # A bare name uses PATH; configured relative paths use the AI_LOOP root.
        binary = Path(bin_path)
        self.bin_path = str(ROOT / binary) if not binary.is_absolute() and '/' in bin_path else bin_path
        self.regional_proxy_endpoint = regional_proxy_endpoint
        self.bucket = bucket

    def _run(self, args: list[str], timeout: float) -> subprocess.CompletedProcess:
        try:
            if self.regional_proxy_endpoint:
                from obs_transport import ObsRegionalProxy
                route = ObsRegionalProxy(self.regional_proxy_endpoint, self.bucket)
            else:
                route = nullcontext(None)
            with route as proxy:
                options = ['-proxy-url=' + proxy] if proxy else []
                return subprocess.run(
                    [self.bin_path, *args, *options], capture_output=True, text=True,
                    timeout=timeout, check=False,
                )
        except FileNotFoundError as e:
            raise ObsDownloadError(
                f"找不到下载工具 {self.bin_path!r}；OBS 下载方式未就绪，不自动安装"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ObsDownloadError(f"obsutil 调用超时({timeout}s): {args}") from e

    def stat(self, url: str) -> dict | None:
        """查询对象元数据；明确不存在返回 None，其他查询失败抛错。

        返回 {last_modified, etag, size}——ETag 作为远端对象版本标识
        （同名覆盖上传会变化，满足“对象更新必须重新核实”），
        LastModified 作为远端时间（不用本地发现时间冒充）。
        """
        proc = query(self._run, ["stat", url], timeout=STAT_LS_TIMEOUT,
                     error_type=ObsDownloadError, allow_missing=True)
        if proc.returncode != 0:
            return None
        out = proc.stdout or ""
        import re

        def _field(name: str) -> str | None:
            m = re.search(rf"{name}\s*[:：]\s*(\S+)", out)
            return m.group(1) if m else None

        lm = _field("Last Modified")
        etag = _field("ETag")
        if etag:
            etag = etag.strip('"')
        size = _field(r"Size \(KB\)")
        return {
            "last_modified": lm,
            "etag": etag,
            "size_kb": size,
        }

    def list(self, prefix_url: str) -> dict[str, dict]:
        """列出一个前缀下的全部对象，返回 {url: {last_modified, etag, size}}。

        用于 cycle 的一次性桶快照（避免对每个 commit 逐个 stat）。
        解析 `obsutil ls` 的“对象名行 + 元数据行”两行式表格输出。
        """
        proc = query(self._run, ["ls", prefix_url, "-limit=10000"], timeout=STAT_LS_TIMEOUT,
                     error_type=ObsDownloadError)
        if proc.returncode != 0:
            raise ObsDownloadError(
                f"obsutil ls 失败 (exit={proc.returncode}): {prefix_url}\n"
                f"{(proc.stderr or proc.stdout)[-1500:]}"
            )
        import re

        out = proc.stdout or ""
        entries = re.findall(
            r"^(obs://\S+)\s*\n\s+(\d{4}-\d{2}-\d{2}T\S+Z)\s+(\S+)\s+\S+\s+(\"[^\"]*\"|\S+)",
            out, re.MULTILINE,
        )
        result: dict[str, dict] = {}
        for url, mtime, size, etag in entries:
            result[url] = {
                "last_modified": mtime,
                "etag": etag.strip('"'),
                "size": size,
            }
        if len(result) >= 10000:
            raise ObsDownloadError('OBS listing reached the 10000-object limit; refusing a potentially truncated snapshot')
        return result

    def download(self, url: str, dest: Path) -> DownloadedObject:
        if not isinstance(url, str) or not url.strip():
            raise ObsDownloadError("下载 URL 不能为空")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 快速失败 + 一次重试：链路劣化时挂死等价于任务烧满超时（实证 1h/个）。
        last_error: ObsDownloadError | None = None
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                proc = self._run(["cp", "-f", url, str(dest)], timeout=DOWNLOAD_TIMEOUT)
            except ObsDownloadError as e:  # 超时（工具缺失不重试，直接抛）
                if "找不到下载工具" in str(e):
                    raise
                last_error = e
            else:
                if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
                    return DownloadedObject(url=url, dest=dest, size=dest.stat().st_size)
                last_error = ObsDownloadError(
                    f"obsutil 下载失败 (exit={proc.returncode}): {url}\n"
                    f"{(proc.stderr or proc.stdout)[-2000:]}"
                )
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(DOWNLOAD_RETRY_INTERVAL)
        assert last_error is not None
        raise last_error


def make_downloader(obs_cfg: dict | None):
    """按配置构造下载器；未配置返回 None（由执行端记 blocked）。"""
    cfg = (obs_cfg or {}).get("downloader")
    if not cfg:
        return None
    kind = cfg.get("type")
    if kind == "obsutil":
        return ObsutilDownloader(cfg.get("bin", "obsutil"),
            regional_proxy_endpoint=cfg.get('regional_proxy_endpoint'),
            bucket=(obs_cfg or {}).get('bucket', 'pytorch-ci-cache'))
    raise ObsDownloadError(
        f"未知 OBS 下载器类型 {kind!r}（当前支持 obsutil）；"
        "下载方式必须由部署配置显式给出"
    )
