"""Install a verified model into a versioned directory, without replacing active files."""

import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

URL = "https://argos-net.com/v1/translate-en_ko-1_1.argosmodel"
SHA = "e03d8e65e6d44525ec5808c3409fcf8728c76c2c76925372b6d3dc3278de17fc"
FILES = (
    "sentencepiece.model",
    "model/model.bin",
    "model/shared_vocabulary.txt",
    "README.md",
    "metadata.json",
)


def install(target):
    if target.is_dir() and all((target / p).is_file() for p in FILES):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o755)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        archive = Path(tmp) / "archive"
        with urllib.request.urlopen(URL, timeout=90) as src, archive.open("wb") as out:
            shutil.copyfileobj(src, out)
        with archive.open("rb") as src:
            if hashlib.file_digest(src, "sha256").hexdigest() != SHA:
                raise ValueError("model checksum mismatch")
        with zipfile.ZipFile(archive) as z:
            for name in FILES:
                z.extract("en_ko/" + name, tmp)
        folder = Path(tmp) / "en_ko"
        for p in [folder, *folder.rglob("*")]:
            p.chmod(0o755 if p.is_dir() else 0o644)
        os.rename(folder, target)


if __name__ == "__main__":
    install(Path(sys.argv[1]))
