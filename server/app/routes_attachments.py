import hashlib
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .auth import get_current_user
from .config import settings
from .db import get_db

router = APIRouter(prefix="/attachments", tags=["attachments"])


@router.post("")
async def upload_attachment(file: UploadFile, user=Depends(get_current_user)):
    sha = hashlib.sha256()
    fd, tmp_name = tempfile.mkstemp(dir=settings.attachments_dir)
    os.close(fd)
    tmp_path = Path(tmp_name)
    size = 0
    try:
        with tmp_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                sha.update(chunk)
                size += len(chunk)
                out.write(chunk)
        digest = sha.hexdigest()
        final = _attachment_path(digest)
        db = get_db()
        if not final.exists():
            shutil.move(tmp_path, final)
        else:
            tmp_path.unlink(missing_ok=True)
        db.execute(
            """
            INSERT INTO attachments (sha256, filename, content_type, size, uploaded_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET uploaded_by = excluded.uploaded_by
            """,
            (digest, file.filename or "unnamed", file.content_type or "application/octet-stream", size, user["id"]),
        )
        db.commit()
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return {"sha256": digest, "size": size, "uri": f"attachment://{digest}"}


@router.get("/{sha256}")
def download_attachment(sha256: str, user=Depends(get_current_user)):
    if not _is_sha256(sha256):
        raise HTTPException(status_code=400, detail="invalid hash")
    path = _attachment_path(sha256)
    if not path.exists():
        raise HTTPException(status_code=404, detail="attachment not found")
    row = get_db().execute(
        "SELECT content_type, filename FROM attachments WHERE sha256 = ?", (sha256,)
    ).fetchone()
    media_type = row["content_type"] if row else "application/octet-stream"
    filename = row["filename"] if row else sha256
    return FileResponse(path, media_type=media_type, filename=filename)


def _attachment_path(digest: str) -> Path:
    folder = settings.attachments_dir / digest[:2]
    folder.mkdir(parents=True, exist_ok=True)
    return folder / digest


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())
