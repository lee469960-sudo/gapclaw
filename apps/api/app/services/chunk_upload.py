import json
from pathlib import Path

from app.config import get_settings


def chunk_dir(upload_id: str) -> Path:
    d = Path(get_settings().data_dir) / "chunks" / upload_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_chunk(upload_id: str, index: int, data: bytes) -> dict:
    path = chunk_dir(upload_id) / f"{index:06d}.part"
    path.write_bytes(data)
    meta = chunk_dir(upload_id) / "meta.json"
    info = json.loads(meta.read_text()) if meta.exists() else {"total": 0, "filename": ""}
    info["received"] = info.get("received", 0) + 1
    meta.write_text(json.dumps(info))
    return {"upload_id": upload_id, "index": index, "received": info["received"]}


def init_upload(upload_id: str, filename: str, total_chunks: int) -> dict:
    d = chunk_dir(upload_id)
    (d / "meta.json").write_text(json.dumps({"filename": filename, "total": total_chunks, "received": 0}))
    return {"upload_id": upload_id}


def merge_chunks(upload_id: str, dest_path: Path) -> dict:
    d = chunk_dir(upload_id)
    meta = json.loads((d / "meta.json").read_text())
    parts = sorted(d.glob("*.part"))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as out:
        for p in parts:
            out.write(p.read_bytes())
    return {"path": str(dest_path), "filename": meta.get("filename", dest_path.name)}
