"""Fetch pinned CTranslate2 weights and the matching Marian source/target vocabularies."""

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WEIGHTS = "ooeoeo/opus-mt-tc-big-en-ko-ct2-float16/resolve/575dfd05aac17dd9a3983b22cd7483a151ee11b1"
VOCABS = "eskara1398/opus-mt-tc-big-en-ko-ct2/resolve/c4722b722b477acb3635059af5e7aa0fc1769084"
FILES = {
    "model.bin": (
        WEIGHTS,
        "2a85e7bb574cd9905386d1a4b48d29b3881f897ec14013221d91dc92217e9b4d",
    ),
    "config.json": (
        WEIGHTS,
        "8f6496adfc930cbfecbe8281112197705c488fab47d34b4829b06d7f478909af",
    ),
    "source.spm": (
        WEIGHTS,
        "3d0591e65c49541d82f48df33d7b322c3d4ee7aa0ee8747f9a7f9355dbf22c95",
    ),
    "target.spm": (
        WEIGHTS,
        "3d2aa641a0890d8966ab8703b109895a4e522713ce99b4a0192bfacb495bc97c",
    ),
    "source_vocabulary.json": (
        VOCABS,
        "a87e56c72f921d1c1fffe747098228bdb7a5cb7b16b86372a34419b1bb275028",
    ),
    "target_vocabulary.json": (
        VOCABS,
        "e9d0300fce8fa7a734a7cede1b0faf9dba3328432652e607d2c8eebdb026e8b1",
    ),
}


def install(target):
    if all((target / name).is_file() for name in FILES):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o755)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        folder = Path(tmp) / "model"
        folder.mkdir(mode=0o755)
        for name, (repo, digest) in FILES.items():
            path = folder / name
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--location",
                    "--retry",
                    "3",
                    "--max-time",
                    "900",
                    "--output",
                    str(path),
                    f"https://huggingface.co/{repo}/{name}",
                ],
                check=True,
            )
            with path.open("rb") as src:
                if hashlib.file_digest(src, "sha256").hexdigest() != digest:
                    raise ValueError("invalid model file: " + name)
            path.chmod(0o644)
        folder.chmod(0o755)
        os.rename(folder, target)


if __name__ == "__main__":
    install(Path(sys.argv[1]))
