from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
MAX_PUBLIC_FILE_BYTES = 10_000_000
EXPECTED_MEDIA = {
    "docs/media/demo.mp4": 10_000_000,
    "docs/media/demo.gif": 3_000_000,
    "docs/media/demo-poster.png": 3_000_000,
    "docs/media/system-overview.png": 3_000_000,
}

FORBIDDEN_PATH_PARTS = (
    ".ai-bridge/",
    ".demo-profile",
    ".e2e-profile",
    ".venv/",
    "node_modules/",
    "npm-cache/",
    "playwright-report/",
    "test-results/",
    "local-api/runtime/",
)
FORBIDDEN_SUFFIXES = (".log", ".pyc", ".zip")
FORBIDDEN_EXACT = {
    "local-api/config.local.json",
    "local-api/config.json",
}
SENSITIVE_PATTERNS = {
    "Windows absolute path": re.compile(r"[A-Za-z]:\\(?:Users|00_dev|00_doc)\\", re.IGNORECASE),
    "Unix home path": re.compile(r"/(?:Users|home)/[^/\s]+/"),
    "email address": re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "private key": re.compile(r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY"),
    "bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._-]{12,}", re.IGNORECASE),
    "credential assignment": re.compile(
        r"(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*[\"']"
        r"(?!example|placeholder|your-)[^\"']{8,}[\"']",
        re.IGNORECASE,
    ),
}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
GITHUB_VIDEO_ATTACHMENT = re.compile(
    r"(?m)^https://github\.com/user-attachments/assets/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def git_public_candidates() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    )
    return sorted({item for item in output.decode("utf-8").split("\0") if item})


def text_content(path: Path) -> str | None:
    if path.suffix.lower() in {".png", ".gif", ".mp4", ".ico", ".wav", ".webp"}:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def check_markdown_links(relative: str, text: str, errors: list[str]) -> None:
    source = ROOT / relative
    for match in MARKDOWN_LINK.finditer(text):
        target = match.group(1).strip().split()[0].strip("<>")
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not target:
            continue
        resolved = (source.parent / target).resolve()
        try:
            resolved.relative_to(ROOT.resolve())
        except ValueError:
            errors.append(f"{relative}: link escapes repository: {target}")
            continue
        if not resolved.exists():
            errors.append(f"{relative}: missing link target: {target}")


def main() -> int:
    candidates = git_public_candidates()
    errors: list[str] = []

    for relative in candidates:
        normalized = relative.replace("\\", "/")
        lowered = normalized.lower()
        path = ROOT / relative

        if relative in FORBIDDEN_EXACT:
            errors.append(f"forbidden local file: {relative}")
        if any(part in lowered for part in FORBIDDEN_PATH_PARTS):
            errors.append(f"forbidden generated/private path: {relative}")
        if lowered.endswith(FORBIDDEN_SUFFIXES):
            errors.append(f"forbidden generated file: {relative}")
        if not path.is_file():
            continue

        size = path.stat().st_size
        if size > MAX_PUBLIC_FILE_BYTES:
            errors.append(f"file exceeds 10 MB public limit: {relative} ({size} bytes)")

        text = text_content(path)
        if text is None:
            continue
        for label, pattern in SENSITIVE_PATTERNS.items():
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{relative}:{line}: {label}")
        if path.suffix.lower() in {".md", ".markdown"}:
            check_markdown_links(relative, text, errors)

    actual_media = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "docs" / "media").glob("*")
        if path.is_file()
    }
    if actual_media != set(EXPECTED_MEDIA):
        errors.append(
            "docs/media must contain only the approved public media files: "
            + ", ".join(sorted(EXPECTED_MEDIA))
        )
    for relative, limit in EXPECTED_MEDIA.items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"missing public media file: {relative}")
        elif path.stat().st_size > limit:
            errors.append(f"public media file too large: {relative} ({path.stat().st_size} bytes)")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if not GITHUB_VIDEO_ATTACHMENT.search(readme):
        errors.append("README does not contain a GitHub user-attachments video URL on its own line")
    legacy_preview = "[![ChatGPT Local Voice Bridge demo](docs/media/demo.gif)](docs/media/demo.mp4)"
    if legacy_preview in readme:
        errors.append("README still contains the legacy GIF-to-MP4 preview link")

    if errors:
        print("PUBLIC TREE CHECK: FAIL", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"PUBLIC TREE CHECK: PASS ({len(candidates)} candidate files)")
    for relative in sorted(EXPECTED_MEDIA):
        print(f"- {relative}: {(ROOT / relative).stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
