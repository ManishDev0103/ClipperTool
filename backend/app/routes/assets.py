from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from app.config import DATA_DIR

router = APIRouter(prefix="/api/assets", tags=["assets"])
MAX_LOGO_BYTES = 20 * 1024 * 1024
MAX_MUSIC_BYTES = 512 * 1024 * 1024
LOGO_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


def _safe_name(filename: str, extensions: set[str]) -> str:
    safe_name = Path(filename.replace("\\", "/")).name
    if safe_name != filename or Path(safe_name).suffix.lower() not in extensions:
        raise HTTPException(status_code=415, detail=f"Unsupported asset filename: {filename}")
    return safe_name


def _list_files(folder: str, extensions: set[str]) -> list[dict[str, str | int]]:
    root = DATA_DIR / folder
    root.mkdir(parents=True, exist_ok=True)
    return [
        {"filename": path.name, "size_bytes": path.stat().st_size}
        for path in sorted(root.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and path.suffix.lower() in extensions
    ]


@router.get("")
def list_assets() -> dict[str, list[dict[str, str | int]]]:
    return {
        "logos": _list_files("logos", {".png", ".jpg", ".jpeg", ".webp"}),
        "music": _list_files("music", MUSIC_EXTENSIONS),
    }


async def _save_asset(
    request: Request,
    filename: str,
    folder: str,
    extensions: set[str],
    maximum_bytes: int,
    validate_logo: bool = False,
) -> dict[str, str | int]:
    safe_name = _safe_name(filename, extensions)
    asset_dir = DATA_DIR / folder
    asset_dir.mkdir(parents=True, exist_ok=True)
    final_path = asset_dir / safe_name
    temp_path = asset_dir / f".{uuid4().hex}.part"
    size = 0
    try:
        with temp_path.open("wb") as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > maximum_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Asset exceeds the {maximum_bytes // (1024 * 1024)} MB limit.",
                    )
                target.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="The uploaded asset is empty.")
        if validate_logo:
            with temp_path.open("rb") as source:
                signature = source.read(12)
            valid = signature.startswith(LOGO_SIGNATURES) or (
                signature.startswith(b"RIFF") and signature[8:12] == b"WEBP"
            )
            if not valid:
                raise HTTPException(status_code=422, detail="Logo must be a valid PNG, JPEG, or WebP image.")
        temp_path.replace(final_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return {"filename": final_path.name, "size_bytes": final_path.stat().st_size}


@router.post("/logos", status_code=201)
async def upload_logo(
    request: Request,
    filename: str = Query(min_length=5, max_length=255),
) -> dict[str, str | int]:
    return await _save_asset(
        request,
        filename,
        "logos",
        {".png", ".jpg", ".jpeg", ".webp"},
        MAX_LOGO_BYTES,
        validate_logo=True,
    )


@router.post("/music", status_code=201)
async def upload_music(
    request: Request,
    filename: str = Query(min_length=5, max_length=255),
) -> dict[str, str | int]:
    return await _save_asset(
        request,
        filename,
        "music",
        MUSIC_EXTENSIONS,
        MAX_MUSIC_BYTES,
    )
