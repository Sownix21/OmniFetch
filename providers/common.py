"""Provider contracts and limits; no Telegram dependency."""
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    max_file_bytes: int
    max_job_bytes: int
    max_items: int
    reserve_bytes: int
    timeout: int = 30
    cobalt_url: str = ""
    cobalt_key: str = ""
    instagram_cookies: str = ""


@dataclass
class Result:
    files: list[Path]
    provider: str
    warnings: list[str] = field(default_factory=list)


def host_is(url, domain):
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def check_files(files, root, settings):
    root = root.resolve()
    found, total = [], 0
    for file in files:
        path = Path(file).resolve()
        if not path.is_relative_to(root) or not path.is_file() or Path(file).is_symlink():
            raise ProviderError("Provider returned an invalid output path")
        if path in found:
            continue
        size = path.stat().st_size
        if not size or size > settings.max_file_bytes:
            raise ProviderError("Provider output is empty or exceeds the file-size limit")
        total += size
        if total > settings.max_job_bytes:
            raise ProviderError("Download exceeds the total job-size limit")
        found.append(path)
    if not found:
        raise ProviderError("Provider returned no completed files")
    return found
