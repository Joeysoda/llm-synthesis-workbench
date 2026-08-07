from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    system_root: Path
    workspace_root: Path
    runtime_root: Path
    database_path: Path
    research_root: Path
    synthetic_repo: Path
    synthetic_cli: Path
    synlogic_repo: Path
    easy_dataset_repo: Path
    kaqg_repo: Path
    kaqg_commit: str
    easy_dataset_base_url: str
    kaqg_worker_base_url: str
    llm_base_url: str
    llm_model: str
    llm_judge_model: str
    vision_base_url: str
    vision_model: str
    gateway_host: str
    gateway_advertise_host: str
    gateway_public_host: str
    gateway_port: int
    max_upload_bytes: int = 25 * 1024 * 1024

    @property
    def llm_credentials_rotated(self) -> bool:
        return os.environ.get("LLM_CREDENTIAL_ROTATED", "").lower() in {
            "1",
            "true",
            "yes",
        }

    @property
    def deepseek_key_present(self) -> bool:
        return bool(os.environ.get("DEEPSEEK_API_KEY"))

    @property
    def llm_ready(self) -> bool:
        return self.deepseek_key_present and self.llm_credentials_rotated

    @property
    def gateway_base_url(self) -> str:
        return f"http://{self.gateway_advertise_host}:{self.gateway_port}"

    @property
    def gateway_public_base_url(self) -> str:
        return f"http://{self.gateway_public_host}:{self.gateway_port}"

    @property
    def deepseek_api_key(self) -> str:
        return os.environ.get("DEEPSEEK_API_KEY", "")

    @property
    def vision_key_present(self) -> bool:
        return bool(os.environ.get("VISION_API_KEY"))

    @property
    def vision_ready(self) -> bool:
        return bool(self.vision_base_url and self.vision_model and self.vision_key_present)

    @property
    def vision_api_key(self) -> str:
        return os.environ.get("VISION_API_KEY", "")


def load_settings() -> Settings:
    system_root = Path(__file__).resolve().parents[2]
    workspace_root = system_root.parent
    bundled_upstream = system_root / "upstream"
    default_research_root = (
        bundled_upstream
        if bundled_upstream.exists()
        else workspace_root / "research" / "llm_synthesis"
    )
    research_root = Path(
        os.environ.get("RESEARCH_ROOT", str(default_research_root))
    )
    runtime_root = system_root / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    for directory in ("uploads", "runs", "logs", "exports"):
        (runtime_root / directory).mkdir(parents=True, exist_ok=True)

    return Settings(
        system_root=system_root,
        workspace_root=workspace_root,
        runtime_root=runtime_root,
        database_path=runtime_root / "workbench.sqlite3",
        research_root=research_root,
        synthetic_repo=Path(
            os.environ.get(
                "SYNTHETIC_REPO",
                str(
                    research_root / "synthetic-data-kit"
                    if research_root == bundled_upstream
                    else research_root / "repos" / "synthetic-data-kit"
                ),
            )
        ),
        synthetic_cli=Path(
            os.environ.get(
                "SYNTHETIC_CLI",
                str(
                    system_root / ".venv" / "bin" / "synthetic-data-kit"
                    if research_root == bundled_upstream
                    else research_root
                    / "envs"
                    / "synthetic-data-kit"
                    / "bin"
                    / "synthetic-data-kit"
                ),
            )
        ),
        synlogic_repo=Path(
            os.environ.get(
                "SYNLOGIC_REPO",
                str(
                    research_root / "synlogic"
                    if research_root == bundled_upstream
                    else research_root / "repos" / "synlogic"
                ),
            )
        ),
        easy_dataset_repo=Path(
            os.environ.get(
                "EASY_DATASET_REPO",
                str(
                    research_root / "easy-dataset"
                    if research_root == bundled_upstream
                    else research_root / "repos" / "easy-dataset"
                ),
            )
        ),
        kaqg_repo=Path(
            os.environ.get(
                "KAQG_REPO",
                str(
                    research_root / "kaqg"
                    if research_root == bundled_upstream
                    else research_root / "repos" / "kaqg"
                ),
            )
        ),
        kaqg_commit=os.environ.get(
            "KAQG_COMMIT", "aa80de0082d5c217bbcb887ba386c2a85518c7d6"
        ),
        easy_dataset_base_url=os.environ.get(
            "EASY_DATASET_BASE_URL", "http://127.0.0.1:1717"
        ).rstrip("/"),
        kaqg_worker_base_url=os.environ.get(
            "KAQG_WORKER_BASE_URL", "http://127.0.0.1:18100"
        ).rstrip("/"),
        llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        llm_model=os.environ.get("LLM_MODEL", "deepseek-v4-pro"),
        llm_judge_model=os.environ.get("LLM_JUDGE_MODEL", "deepseek-v4-pro"),
        vision_base_url=os.environ.get("VISION_BASE_URL", "").rstrip("/"),
        vision_model=os.environ.get("VISION_MODEL", ""),
        gateway_host=os.environ.get("GATEWAY_HOST", "127.0.0.1"),
        gateway_advertise_host=os.environ.get(
            "GATEWAY_ADVERTISE_HOST", os.environ.get("GATEWAY_HOST", "127.0.0.1")
        ),
        gateway_public_host=os.environ.get("GATEWAY_PUBLIC_HOST", "127.0.0.1"),
        gateway_port=int(os.environ.get("GATEWAY_PORT", "18000")),
    )


settings = load_settings()
