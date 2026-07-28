from __future__ import annotations

import json
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

from packaging.version import Version

EXPECTED_VCS_COMMITS = {
    "irodori-tts": "eaf74d6a19138f743acb5b71a445fd25a57db987",
    "dacvae": "414c20785fc3a28373073ea8ef7a1316eeeaca6e",
    "silentcipher": "d46d7d0893a583d8968ab3a6626e2289faec9152",
}
EXPECTED_EXACT_VERSIONS = {
    "transformers": "5.5.0",
    "huggingface-hub": "1.23.0",
    "hf-xet": "1.5.1",
    "sentencepiece": "0.2.1",
    "PySide6": "6.11.1",
    "torchcodec": "0.14.0",
}
EXPECTED_VERSION_RANGES = {
    "torch": (Version("2.11.0"), Version("2.12.0")),
    "torchaudio": (Version("2.11.0"), Version("2.12.0")),
}
ALLOWED_PIP_CHECK_FRAGMENTS = (
    "descript-audiotools 0.7.2 has requirement protobuf<3.20",
    "irodori-tts 0.1.0 has requirement huggingface-hub<1.0",
    "irodori-tts 0.1.0 has requirement sentencepiece<0.2",
    "irodori-tts 0.1.0 has requirement torchcodec<0.11.0",
    "irodori-tts 0.1.0 has requirement transformers<5",
)


def direct_url_commit(package_name: str) -> str:
    dist = distribution(package_name)
    path = Path(dist._path) / "direct_url.json"  # type: ignore[attr-defined]
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    vcs = payload.get("vcs_info") if isinstance(payload, dict) else None
    return str(vcs.get("commit_id") or "") if isinstance(vcs, dict) else ""


def audit_versions() -> list[str]:
    errors: list[str] = []
    for package_name, expected in EXPECTED_EXACT_VERSIONS.items():
        try:
            installed = version(package_name)
        except PackageNotFoundError:
            errors.append(f"missing package: {package_name}")
            continue
        if installed != expected:
            errors.append(f"{package_name}={installed}; expected {expected}")
    for package_name, (minimum, maximum) in EXPECTED_VERSION_RANGES.items():
        try:
            installed = Version(version(package_name).split("+", 1)[0])
        except PackageNotFoundError:
            errors.append(f"missing package: {package_name}")
            continue
        if installed < minimum or installed >= maximum:
            errors.append(f"{package_name}={installed}; expected >= {minimum}, < {maximum}")
    for package_name, expected in EXPECTED_VCS_COMMITS.items():
        try:
            installed = direct_url_commit(package_name)
        except PackageNotFoundError:
            errors.append(f"missing package: {package_name}")
            continue
        if installed != expected:
            errors.append(f"{package_name} commit={installed or 'unknown'}; expected {expected}")
    return errors


def audit_pip_check() -> list[str]:
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    lines = [line.strip() for line in (completed.stdout + "\n" + completed.stderr).splitlines() if line.strip()]
    return [line for line in lines if not any(fragment in line for fragment in ALLOWED_PIP_CHECK_FRAGMENTS)]


def main() -> int:
    errors = audit_versions() + audit_pip_check()
    if errors:
        for error in errors:
            print(f"[ng] dependency audit: {error}", file=sys.stderr)
        return 1
    print("[ok] dependency audit complete; only documented Irodori metadata overrides remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
