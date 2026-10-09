"""Fetch an immutable public ASR model; verify LFS SHA and git blob IDs before use."""

import argparse
import hashlib
import json
from pathlib import Path

ASR_REPO = "Systran/faster-whisper-tiny"
ASR_REVISION = "d90ca5fe260221311c53c58e660288d3deb8d356"


def download_docling(destination):
    import fnmatch

    from huggingface_hub import HfApi, hf_hub_download

    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    specs = [
        (
            "docling-project/docling-layout-heron",
            "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
            ["config.json", "preprocessor_config.json", "model.safetensors"],
        ),
        (
            "docling-project/docling-models",
            "fc0f2d45e2218ea24bce5045f58a389aed16dc23",
            ["model_artifacts/tableformer/accurate/*"],
        ),
    ]
    manifest = {"repositories": [], "files": {}}
    for repo, revision, patterns in specs:
        info = HfApi().model_info(repo, revision=revision, files_metadata=True)
        folder = root / repo.replace("/", "--")
        for item in info.siblings:
            if not any(fnmatch.fnmatch(item.rfilename, pattern) for pattern in patterns):
                continue
            path = Path(hf_hub_download(repo, item.rfilename, revision=revision, local_dir=folder))
            with path.open("rb") as f:
                sha = hashlib.file_digest(f, "sha256").hexdigest()
            if item.lfs:
                if sha != item.lfs.sha256:
                    raise RuntimeError("Docling model checksum mismatch")
            else:
                data = path.read_bytes()
                if hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest() != item.blob_id:
                    raise RuntimeError("Docling config checksum mismatch")
            manifest["files"][str(path.relative_to(root))] = sha
        manifest["repositories"].append({"repository": repo, "revision": revision})
    if len(manifest["files"]) != 5:
        raise RuntimeError("Incomplete Docling artifacts")
    (root / "wks-model-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def download_asr(destination):
    from huggingface_hub import HfApi, hf_hub_download

    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    info = HfApi().model_info(ASR_REPO, revision=ASR_REVISION, files_metadata=True)
    manifest = {"repository": ASR_REPO, "revision": ASR_REVISION, "files": {}}
    for item in info.siblings:
        if item.rfilename not in {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt"}:
            continue
        path = Path(
            hf_hub_download(ASR_REPO, item.rfilename, revision=ASR_REVISION, local_dir=root)
        )
        with path.open("rb") as f:
            sha = hashlib.file_digest(f, "sha256").hexdigest()
        if item.lfs:
            if sha != item.lfs.sha256:
                raise RuntimeError("Model checksum mismatch")
        else:
            data = path.read_bytes()
            blob_hash = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            if blob_hash != item.blob_id:
                raise RuntimeError("Configuration git-blob checksum mismatch")
        manifest["files"][item.rfilename] = sha
    if len(manifest["files"]) != 4:
        raise RuntimeError("Incomplete ASR model")
    (root / "wks-model-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", default=".local/models/asr-tiny")
    parser.add_argument("--docling", action="store_true")
    args = parser.parse_args()
    result = download_docling(args.destination) if args.docling else download_asr(args.destination)
    print(json.dumps({"verified_files": len(result["files"])}))
