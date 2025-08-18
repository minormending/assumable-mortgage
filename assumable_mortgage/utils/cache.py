import hashlib
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class FileCache:
    """Simple file-backed JSON cache with hashed keys.

    Stores files under `.cache/` by default. Keys are md5 hashes derived from
    provided inputs to keep filenames short and safe.
    """

    def __init__(self, base_dir: str = ".cache") -> None:
        self.base = Path(base_dir)
        self.base.mkdir(exist_ok=True)

    @staticmethod
    def make_key(obj: Any) -> str:
        try:
            payload = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
        except Exception:
            payload = str(obj).encode("utf-8")
        return hashlib.md5(payload).hexdigest()

    def path_for(self, prefix: str, key: str, ext: str = "json") -> Path:
        return self.base / f"{prefix}_{key}.{ext}"

    def read_json(self, path: Path) -> Any | None:
        if not path.exists():
            log.debug("cache.miss", extra={"path": str(path)})
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Try to count items if this is a common envelope
            items = None
            try:
                items = len((data or {}).get("response", {}).get("items", []))
            except Exception:
                pass
            log.debug("cache.hit", extra={"path": str(path), "items": items})
            return data

    def write_json(self, path: Path, data: Any) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
        size = path.stat().st_size if path.exists() else None
        log.debug("cache.write", extra={"path": str(path), "bytes": size})
