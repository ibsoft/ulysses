from __future__ import annotations

import asyncio
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import shutil

import httpx
from rich import print as rprint
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn

from .config import MODEL_FILES
from .resources import get_default_model_root

MODEL_DETAILS: dict[str, dict[str, str]] = {
    path: {"url": model_file.url, "checksum": model_file.checksum} for path, model_file in MODEL_FILES.items()
}


def selected_models(group: str = "all") -> dict[str, dict[str, str]]:
    if group == "all":
        return dict(MODEL_DETAILS)
    prefix = f"{group.upper()}/"
    return {path: info for path, info in MODEL_DETAILS.items() if path.startswith(prefix)}


def copy_metadata(model_root: Path) -> None:
    source_root = Path(str(files("sirina.assets") / "models"))
    for source in source_root.rglob("*"):
        if not source.is_file():
            continue
        destination = model_root / source.relative_to(source_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def file_is_valid(path: Path, expected_checksum: str) -> bool:
    if not path.exists():
        return False
    return sha256(path.read_bytes()).hexdigest() == expected_checksum


def models_valid(model_root: str | Path | None = None, group: str = "all") -> bool:
    root = Path(model_root).expanduser() if model_root else get_default_model_root()
    for relative_path, model_info in selected_models(group).items():
        if not file_is_valid(root / relative_path, model_info["checksum"]):
            return False
    return True


async def download_file(
    client: httpx.AsyncClient,
    url: str,
    file_path: Path,
    expected_checksum: str,
    progress: Progress,
) -> bool:
    task_id = progress.add_task(f"Downloading {file_path.name}", status="")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    hash_sha256 = sha256()

    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("Content-Length", 0))
            if total_size:
                progress.update(task_id, total=total_size)
            with file_path.open(mode="wb") as file:
                async for chunk in response.aiter_bytes(32768):
                    file.write(chunk)
                    hash_sha256.update(chunk)
                    progress.advance(task_id, len(chunk))

        actual_checksum = hash_sha256.hexdigest()
        if actual_checksum != expected_checksum:
            progress.update(task_id, status="[bold red]checksum failed")
            file_path.unlink(missing_ok=True)
            return False
        progress.update(task_id, status="[bold green]ok")
        return True
    except Exception as exc:
        progress.update(task_id, status=f"[bold red]error: {exc}")
        return False


async def download_models(model_root: str | Path | None = None, group: str = "all") -> int:
    root = Path(model_root).expanduser() if model_root else get_default_model_root()
    copy_metadata(root)
    models = selected_models(group)

    with Progress(
        TextColumn("[grey50][progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TextColumn("  {task.fields[status]}"),
    ) as progress:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            tasks = []
            for relative_path, model_info in models.items():
                destination = root / relative_path
                if file_is_valid(destination, model_info["checksum"]):
                    continue
                tasks.append(
                    asyncio.create_task(
                        download_file(client, model_info["url"], destination, model_info["checksum"], progress)
                    )
                )
            results = await asyncio.gather(*tasks) if tasks else [True]

    if not all(results):
        rprint("\n[bold red]Some Sirina model files were not downloaded successfully")
        return 1
    rprint(f"\n[bold green]Sirina models ready at {root}")
    return 0
