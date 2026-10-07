from __future__ import annotations

from typing import Any


ALLOWED_GENERATION_BACKENDS = {"local", "remote_ssh"}


def normalize_generation_backend(config: dict[str, Any]) -> None:
    backend = str(config.get("generationBackend") or "local").strip().lower()
    if backend not in ALLOWED_GENERATION_BACKENDS:
        raise ValueError("generationBackend must be local or remote_ssh")

    remote = config.get("remoteGeneration") if isinstance(config.get("remoteGeneration"), dict) else {}
    if backend == "remote_ssh":
        ssh_alias = str(remote.get("sshAlias") or "").strip()
        remote_repo_root = str(remote.get("remoteRepoRoot") or "").strip()
        if not ssh_alias or not remote_repo_root:
            raise ValueError("remoteGeneration.sshAlias and remoteGeneration.remoteRepoRoot are required for remote_ssh")
        try:
            connect_timeout = max(1.0, float(remote.get("connectTimeoutSeconds", 7.0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("remoteGeneration.connectTimeoutSeconds must be a number") from exc
        remote = {
            **remote,
            "sshAlias": ssh_alias,
            "remoteRepoRoot": remote_repo_root,
            "connectTimeoutSeconds": connect_timeout,
        }

    config["generationBackend"] = backend
    config["remoteGeneration"] = remote
