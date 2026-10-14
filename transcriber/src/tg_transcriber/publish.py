import datetime as dt
import re
from pathlib import Path

import httpx
from pycrdt import Doc, Text


def _sanitize_filename(name: str) -> str:
    name = name.strip().replace("/", "-").replace("\\", "-")
    name = re.sub(r"[^\w\s\.\-áéíóúÁÉÍÓÚñÑ]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name or "sin-titulo"


def write_to_obsidian(
    vault_path: str | Path,
    markdown: str,
    title: str,
    folder: str | None = None,
    overwrite: bool = True,
    timestamp: bool = True,
) -> Path:
    vault = Path(vault_path).expanduser()

    if not vault.exists() or not vault.is_dir():
        raise FileNotFoundError(f"Vault de Obsidian no encontrado: {vault}")

    dest_folder = vault
    if folder and folder.strip():
        dest_folder = vault / folder.strip()
    dest_folder.mkdir(parents=True, exist_ok=True)

    safe_title = _sanitize_filename(title)
    if timestamp:
        ts = dt.datetime.now().strftime("%Y-%m-%d_%H-%M")
        base = f"{ts} - {safe_title}"
    else:
        base = safe_title

    candidate = dest_folder / f"{base}.md"
    if not overwrite and candidate.exists():
        idx = 2
        while (dest_folder / f"{base}_{idx}.md").exists():
            idx += 1
        candidate = dest_folder / f"{base}_{idx}.md"

    candidate.write_text(markdown, encoding="utf-8")
    return candidate


def render_markdown(
    session_name: str,
    started_at_epoch: float,
    segments: list,
    audio_attachment_uris: dict[str, str] | None = None,
) -> str:
    date = dt.datetime.fromtimestamp(started_at_epoch).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {session_name}",
        "",
        f"- Fecha: {date}",
        "- Resumen: _pendiente (goblin-agent)_",
        "",
    ]
    if audio_attachment_uris:
        lines.append("## Audio")
        lines.append("")
        for label, uri in audio_attachment_uris.items():
            lines.append(f"- [{label}]({uri})")
        lines.append("")
    lines.append("## Transcripción")
    lines.append("")
    if not segments:
        lines.append("_Sin voz detectada._")
    for seg in segments:
        mm = int(seg.start // 60)
        ss = int(seg.start % 60)
        lines.append(f"**[{mm:02d}:{ss:02d}] {seg.source}:** {seg.text}")
    return "\n".join(lines) + "\n"


def upload_attachment(api_url: str, token: str, path, filename: str) -> str:
    with open(path, "rb") as f:
        response = httpx.post(
            f"{api_url.rstrip('/')}/attachments",
            files={"file": (filename, f, "audio/wav")},
            headers={"Authorization": f"Bearer {token}"},
            timeout=300,
        )
    response.raise_for_status()
    return response.json()["uri"]


def publish_note(api_url: str, token: str, title: str, markdown: str) -> str:
    base = api_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    doc = httpx.post(f"{base}/docs", json={"title": title}, headers=headers, timeout=30)
    doc.raise_for_status()
    doc_id = doc.json()["id"]

    ct = httpx.post(f"{base}/docs/{doc_id}/token", headers=headers, timeout=30)
    ct.raise_for_status()
    client_token = ct.json()
    ws_base = (
        client_token["baseUrl"]
        .replace("wss://", "https://")
        .replace("ws://", "http://")
    )
    auth_headers = {}
    if client_token.get("token"):
        auth_headers["Authorization"] = f"Bearer {client_token['token']}"

    ydoc = Doc()
    text = ydoc.get("content", type=Text)
    text += markdown
    update_response = httpx.post(
        f"{ws_base}/update",
        content=ydoc.get_update(),
        headers=auth_headers,
        timeout=60,
    )
    update_response.raise_for_status()
    return doc_id
