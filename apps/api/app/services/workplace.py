import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings

EXCEL_EXTS = {".xlsx", ".xlsm"}
MAX_EXCEL_ROWS = 500
MAX_EXCEL_COLS = 50


def _benchmark_workplace_root() -> Path | None:
    raw = (os.environ.get("REACT_BENCH_WORKSPACE_ROOT") or "").strip()
    if not raw:
        return None
    return Path(raw).resolve()


def _wp_root(sandbox_id: str) -> Path:
    bench_root = _benchmark_workplace_root()
    if bench_root is not None:
        return bench_root
    return Path(get_settings().workplace_dir) / sandbox_id / "workplace"


def _resolved_wp_root(sandbox_id: str) -> Path:
    return _wp_root(sandbox_id).resolve()


def _safe_filename(filename: str) -> str:
    if not filename:
        return ""
    name = filename.replace("\\", "/").split("/")[-1].strip()
    if not name or name in (".", ".."):
        return ""
    return name


def _path_under_root(root: Path, rel: str = "") -> Path | None:
    rel = (rel or "").strip().strip("/")
    try:
        target = root if not rel else (root / rel).resolve()
    except (OSError, ValueError):
        return None
    root_s = str(root)
    target_s = str(target)
    if target_s == root_s or target_s.startswith(root_s + os.sep):
        return target
    return None


def ensure_workplace(sandbox_id: str) -> Path:
    """Ensure workplace root and task scratch directory exist."""
    root = _wp_root(sandbox_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "task").mkdir(exist_ok=True)
    return root


def find_export_build_scripts(sandbox_id: str) -> list[Path]:
    """Locate LLM-authored build_report*.py under workplace/tmp (newest first)."""
    root = ensure_workplace(sandbox_id)
    found: list[Path] = []
    tmp = root / "tmp"
    if tmp.is_dir():
        found.extend(p for p in tmp.glob("build_report*.py") if p.is_file())
    found.extend(p for p in root.glob("build_report*.py") if p.is_file())
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    # de-dupe by resolve
    seen: set[Path] = set()
    out: list[Path] = []
    for p in found:
        try:
            key = p.resolve()
        except OSError:
            key = p
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _move_file_to_target(root: Path, src: Path, target_dir: str) -> str:
    dest_rel = f"{target_dir.strip('/')}/{src.name}" if target_dir else src.name
    dest = root / dest_rel
    if dest.resolve() == src.resolve():
        return dest_rel
    if dest.exists():
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest_rel


# Extensions that may be final deliverables (only promoted when explicitly named).
_DELIVERABLE_EXTS = {
    ".xlsx", ".xls", ".xlsm", ".csv", ".pdf", ".zip", ".md", ".json",
    ".docx", ".pptx", ".html", ".htm",
}


def _is_task_checkpoint(rel: str) -> bool:
    return rel.startswith("task/_engine_checkpoints/")


def promote_named_files(
    sandbox_id: str,
    rel_paths: list[str],
    target_dir: str = "",
) -> list[str]:
    """Promote only explicitly named task/ files to current dir (root or target_dir)."""
    root = ensure_workplace(sandbox_id)
    target = target_dir.strip().strip("/")
    out: list[str] = []
    seen: set[str] = set()
    for rel in rel_paths or []:
        rel = (rel or "").strip().lstrip("/")
        if not rel.startswith("task/") or _is_task_checkpoint(rel):
            continue
        src = (root / rel).resolve()
        if not src.is_file() or not str(src).startswith(str(root.resolve())):
            continue
        dest_rel = _move_file_to_target(root, src, target)
        if dest_rel not in seen:
            out.append(dest_rel)
            seen.add(dest_rel)
    return out


def cleanup_workplace_temp_dirs(sandbox_id: str, paths: list[str], target_dir: str = "") -> list[str]:
    """Keep task/ intact; only flatten nested workplace/ mistake; never batch-promote task deliverables."""
    root = ensure_workplace(sandbox_id)
    target = target_dir.strip().strip("/")
    all_paths = list(paths)

    nested_wp = root / "workplace"
    if nested_wp.exists() and nested_wp.is_dir():
        for f in nested_wp.rglob("*"):
            if f.is_file():
                rel = str(f.relative_to(root)).replace("\\", "/")
                if rel not in all_paths:
                    all_paths.append(rel)

    final_paths: list[str] = []
    seen: set[str] = set()

    for rel in all_paths:
        if not rel:
            continue
        src = (root / rel.lstrip("/")).resolve()
        if not src.is_file() or not str(src).startswith(str(root.resolve())):
            continue
        if rel.startswith("workplace/"):
            dest_rel = _move_file_to_target(root, src, target)
            if dest_rel not in seen:
                final_paths.append(dest_rel)
                seen.add(dest_rel)
        elif rel not in seen:
            # Keep task/ paths in place — process artifacts stay under task/
            final_paths.append(rel)
            seen.add(rel)

    if nested_wp.exists():
        shutil.rmtree(nested_wp, ignore_errors=True)
    return final_paths


# backward-compatible alias
finalize_workplace_outputs = cleanup_workplace_temp_dirs


def list_dir(sandbox_id: str, rel: str = "") -> list[dict]:
    root = ensure_workplace(sandbox_id)
    clean = (rel or "").strip().strip("/")
    # Never auto-create paths from shell noise like "nodes/ 2>&1 | head -50"
    if any(ch in clean for ch in "|;&<>`$()"):
        return []
    target = root / clean if clean else root
    if not target.exists() or not target.is_dir():
        return []
    entries = []
    for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        st = item.stat()
        entries.append({
            "name": item.name,
            "is_dir": item.is_dir(),
            "size": st.st_size if item.is_file() else 0,
            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "path": str((Path(clean) / item.name).as_posix()).lstrip("/") if clean else item.name,
        })
    return entries


def list_dirs(sandbox_id: str) -> list[str]:
    root = _wp_root(sandbox_id)
    dirs = []
    for p in root.rglob("*"):
        if p.is_dir():
            dirs.append(str(p.relative_to(root)))
    return dirs


def format_dir_listing(sandbox_id: str, rel: str = "") -> str:
    """Human-readable workplace listing (same data source as UI sidebar)."""
    entries = list_dir(sandbox_id, rel)
    if not entries:
        prefix = f"/workplace/{rel}".rstrip("/") if rel else "/workplace"
        return f"{prefix}\n(空目录)"
    lines = []
    prefix = f"/workplace/{rel}".rstrip("/") if rel else "/workplace"
    lines.append(prefix + ":")
    for item in entries:
        kind = "d" if item["is_dir"] else "-"
        size = item["size"] if not item["is_dir"] else 0
        lines.append(f"{kind} {item['name']}\t{size}\t{item.get('modified', '')}")
    return "\n".join(lines)


def is_valid_deliverable_file(path: Path) -> bool:
    """Non-empty data file; xlsx/xlsm must look like a real zip workbook."""
    if not path or not path.is_file():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= 0:
        return False
    suf = path.suffix.lower()
    if suf in {".xlsx", ".xlsm"}:
        try:
            with path.open("rb") as fh:
                magic = fh.read(2)
        except OSError:
            return False
        if magic != b"PK":
            return False
        try:
            return zipfile.is_zipfile(path)
        except OSError:
            return False
    if suf == ".xls":
        return size >= 512
    if suf == ".csv":
        return True
    return True


def find_best_task_data_file(sandbox_id: str) -> str | None:
    """Pick one best deliverable under task/ (exclude checkpoints). Prefer xlsx, else largest csv."""
    root = ensure_workplace(sandbox_id)
    task = root / "task"
    if not task.is_dir():
        return None
    candidates: list[tuple[int, float, int, str]] = []
    # rank: xlsx=2, csv=1, other=0; then size; then mtime
    for f in task.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        if "_engine_checkpoints" in rel.split("/"):
            continue
        if any(part.startswith("_stale_") for part in rel.split("/")):
            continue
        suf = f.suffix.lower()
        if suf not in {".xlsx", ".xlsm", ".xls", ".csv"}:
            continue
        if not is_valid_deliverable_file(f):
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        rank = 2 if suf in {".xlsx", ".xlsm", ".xls"} else 1
        candidates.append((rank, st.st_size, st.st_mtime, rel))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return candidates[0][3]


def _rows_from_json_obj(obj) -> list[list] | None:
    """Normalize common JSON payloads into sheet rows (header + data)."""
    if isinstance(obj, list):
        if not obj:
            return [["(empty)"]]
        if all(isinstance(x, dict) for x in obj):
            keys: list[str] = []
            seen: set[str] = set()
            for row in obj:
                for k in row.keys():
                    sk = str(k)
                    if sk not in seen:
                        seen.add(sk)
                        keys.append(sk)
            return [keys] + [[_cell_value(r.get(k)) for k in keys] for r in obj]
        if all(isinstance(x, (list, tuple)) for x in obj):
            return [list(x) for x in obj]
        return [["value"]] + [[_cell_value(x)] for x in obj]
    if isinstance(obj, dict):
        for key in ("rows", "data", "records", "result", "items"):
            val = obj.get(key)
            if isinstance(val, list):
                return _rows_from_json_obj(val)
        nested = obj.get("content")
        if nested is not None:
            return _rows_from_json_obj(nested)
    return None


def _find_best_task_json(sandbox_id: str) -> str | None:
    """Pick one best task/*.json path for naming hints (not used alone for materialize)."""
    files = _list_task_json_files(sandbox_id)
    if not files:
        return None
    best: tuple[int, float, str] | None = None
    for mt, rel, p in files:
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        name_boost = 2 if any(t in Path(rel).name.lower() for t in ("final", "export", "all", "merge")) else 0
        rank = name_boost * 10_000_000 + sz
        if best is None or rank > best[0] or (rank == best[0] and mt > best[1]):
            best = (rank, mt, rel)
    return best[2] if best else None


def _list_task_json_files(sandbox_id: str) -> list[tuple[float, str, Path]]:
    root = ensure_workplace(sandbox_id)
    task = root / "task"
    if not task.is_dir():
        return []
    out: list[tuple[float, str, Path]] = []
    for f in task.rglob("*.json"):
        if not f.is_file():
            continue
        # Skip sidecar metas and run state
        name = f.name.lower()
        if name.endswith(".meta.json") or name in ("_run_state.json",):
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        if "_engine_checkpoints" in rel.split("/"):
            continue
        if any(part.startswith("_stale_") for part in rel.split("/")):
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        if st.st_size <= 0:
            continue
        out.append((st.st_mtime, rel, f))
    out.sort(key=lambda x: x[0])  # oldest first for stable merge order
    return out


def _merge_task_json_rows(sandbox_id: str) -> list[list] | None:
    """Merge user-grain task/*.json payloads into one sheet (header + rows).

    Only pages with uid+register_time (and not order/game-shaped) are merged so
    fallback materialize is not a heterogeneous wide dump.
    """
    files = _list_task_json_files(sandbox_id)
    if not files:
        return None
    # Prefer a single large final-like *user-grain* file if it alone is enough
    best_rel = None
    best_size = 0
    for _mt, rel, p in files:
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        boost = 2 if any(t in Path(rel).name.lower() for t in ("final", "export", "all", "merge")) else 1
        score = boost * sz
        if score > best_size:
            best_size = score
            best_rel = (rel, p)

    # If one "final*" file dominates (>60% of total bytes), use it alone when user-grain
    total_bytes = sum(p.stat().st_size for *_rest, p in files)
    if best_rel and best_rel[1].stat().st_size >= max(total_bytes * 0.6, 1):
        try:
            obj = json.loads(best_rel[1].read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            obj = None
        if isinstance(obj, list) and obj and isinstance(obj[0], dict) and is_user_grain_payload(obj):
            rows = _rows_from_json_obj(obj)
            if rows and len(rows) > 1:
                return rows

    keys: list[str] = []
    seen: set[str] = set()
    data_rows: list[list] = []
    seen_uids: set[str] = set()
    for _mt, _rel, p in files:
        try:
            obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(obj, list) or not obj or not isinstance(obj[0], dict):
            continue
        if not is_user_grain_payload(obj):
            continue
        part = _rows_from_json_obj(obj)
        if not part or len(part) < 2:
            continue
        header = [str(c) for c in part[0]]
        hset = {h.lower() for h in header}
        if hset <= {"name", "description", "tags"} or hset <= {"cnt", "count", "total"}:
            continue
        for h in header:
            if h not in seen:
                seen.add(h)
                keys.append(h)
        idx = {h: i for i, h in enumerate(header)}
        uid_col = next((h for h in header if h.lower() in ("uid", "user_id")), "")
        for row in part[1:]:
            if not isinstance(row, (list, tuple)):
                continue
            if uid_col and uid_col in idx and idx[uid_col] < len(row):
                uid = _cell_value(row[idx[uid_col]]).strip()
                if uid:
                    if uid in seen_uids:
                        continue
                    seen_uids.add(uid)
            data_rows.append([
                _cell_value(row[idx[k]]) if k in idx and idx[k] < len(row) else ""
                for k in keys
            ])
    if not keys or not data_rows:
        # fallback: largest single user-grain file
        candidates: list[tuple[int, tuple]] = []
        for _mt, rel, p in files:
            try:
                obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(obj, list) and obj and isinstance(obj[0], dict) and is_user_grain_payload(obj):
                try:
                    candidates.append((p.stat().st_size, (rel, p, obj)))
                except OSError:
                    continue
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return _rows_from_json_obj(candidates[0][1][2])
        return None
    return [keys] + data_rows


def task_has_exportable_data(sandbox_id: str) -> bool:
    """True if task/ has a valid csv/xlsx or mergeable JSON."""
    if find_best_task_data_file(sandbox_id):
        return True
    return bool(_merge_task_json_rows(sandbox_id))


def _sample_keys(rows: list) -> set[str]:
    sample = [r for r in rows[:8] if isinstance(r, dict)]
    keys: set[str] = set()
    for r in sample:
        keys.update(str(k) for k in r.keys())
    return keys


def is_detail_row_payload(rows: list) -> bool:
    """True if rows are worth persisting for SHELL analysis (user/order/dim, not views/cnt)."""
    if not isinstance(rows, list) or not rows:
        return False
    keys = _sample_keys(rows)
    if not keys:
        return False
    if keys <= {"name", "description", "tags"}:
        return False
    if keys <= {"cnt"} or keys <= {"count"} or keys <= {"total"}:
        return False
    lower = {k.lower() for k in keys}
    if "uid" in lower or "user_id" in lower or "register_time" in lower:
        return len(rows) >= 1
    name_keys = {"channel_name", "channelname", "game_name", "gamename", "name", "title"}
    if (
        ("channel_id" in lower or "channelid" in lower or "game_id" in lower or "gameid" in lower)
        and (lower & name_keys)
    ):
        return True
    # dim tables (e.g. game catalog) — keep for analyze joins, not for merge
    return len(rows) >= 2 and len(keys) >= 3


def is_user_grain_payload(rows: list) -> bool:
    """True if rows look like per-user detail suitable for merge/materialize."""
    if not isinstance(rows, list) or not rows:
        return False
    keys = _sample_keys(rows)
    if not keys:
        return False
    lower = {k.lower() for k in keys}
    has_uid = "uid" in lower or "user_id" in lower
    if not has_uid:
        return False
    # Exclude order / pay / bet shaped facts even if they carry register_time
    orderish = {
        "order_id", "third_order_id", "product_id", "product_type",
        "bet_amount", "win_amount", "sc_bet", "effective_bet",
    }
    if lower & orderish:
        return False
    paycash_fact = {
        "price", "amount", "pay_sum", "cash_sum", "refund_sum",
        "card_cnt", "finish_time", "update_time",
    }
    if lower & paycash_fact:
        return False
    bet_fact = {
        "bet_sc", "win_sc", "bet_value", "result_value", "bet_nums",
        "goods_type", "stat_date", "game_id", "create_time",
    }
    if lower & bet_fact and "register_time" not in lower and not (lower & {"ban_type", "limit_type"}):
        return False
    # Exclude game dimension catalogs mistakenly tagged with sparse uids
    if "game_id" in lower and "game_name" in lower and "channel_id" not in lower:
        return False
    return len(rows) >= 1


def _infer_page_category(rows: list, view: str = "") -> str:
    v = (view or "").lower()
    if "user_info" in v or "user_register" in v:
        return "user"
    if "pay" in v or "cash" in v or "order" in v:
        return "order"
    if "bet" in v:
        return "bet"
    # Channel dim before game (avoid "user_channel*" mis-tags handled above)
    if "channel" in v and "user" not in v:
        return "dim_channel"
    keys = {k.lower() for k in _sample_keys(rows)}
    channel_name_keys = {"channel_name", "channelname", "name", "title"}
    if (
        ("channel_id" in keys or "channelid" in keys)
        and (keys & channel_name_keys)
        and "uid" not in keys
        and "user_id" not in keys
    ):
        return "dim_channel"
    # Game config dim — exclude bet logs and gameuser daily stats
    if "game" in v and "bet" not in v and "gameuser" not in v and "stat" not in v:
        return "dim_game"
    if is_user_grain_payload(rows):
        return "user"
    if keys & {"order_id", "third_order_id", "product_id"}:
        return "order"
    if "game_id" in keys and "game_name" in keys and "uid" not in keys:
        return "dim_game"
    if "uid" in keys or "user_id" in keys:
        return "fact"
    return "dim"


def write_task_json_page(
    sandbox_id: str,
    rows: list[dict],
    *,
    run_id: str,
    page: int,
    view: str = "",
) -> str | None:
    """Auto-persist one MCP page as task/<run_id>/page_N.json (+ page_N.meta.json)."""
    if not rows or not isinstance(rows, list):
        return None
    if not is_detail_row_payload(rows):
        return None
    safe_run = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(run_id or "export"))[:48]
    if not safe_run.strip("_"):
        safe_run = "export"
    page_n = max(1, int(page))
    rel = f"task/{safe_run}/page_{page_n}.json"
    meta_rel = f"task/{safe_run}/page_{page_n}.meta.json"
    root = ensure_workplace(sandbox_id)
    path = root / rel
    meta_path = root / meta_rel
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        if path.stat().st_size <= 0:
            return None
        view_s = str(view or "").strip()[:120]
        meta = {
            "page": page_n,
            "view": view_s,
            "role": _infer_page_category(rows, view_s),
            "rows": len(rows),
            "keys": sorted(_sample_keys(rows))[:40],
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return None
    return rel.replace("\\", "/")


def collect_export_uids_from_task(
    sandbox_id: str,
    run_id: str,
    *,
    view_needles: tuple[str, ...] = ("pay_order",),
    roles: tuple[str, ...] = ("pay",),
    max_uids: int = 50_000,
) -> list[str]:
    """Distinct uids from landed pay (or other) task pages, stable order."""
    root = ensure_workplace(sandbox_id)
    safe_run = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in str(run_id or "")
    )[:48]
    if not safe_run:
        return []
    task_dir = root / "task" / safe_run
    if not task_dir.is_dir():
        return []
    by_view, by_role = _load_task_pages_indexed(task_dir)
    rows = _pick_all_rows(
        by_view,
        by_role,
        view_needles=view_needles,
        roles=roles,
    )
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("uid") or row.get("user_id") or "").strip()
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
        if len(out) >= max(1, int(max_uids)):
            break
    return out


def list_task_page_view_map(sandbox_id: str, run_id: str = "") -> str:
    """Human-readable page→view/role lines for analyze hints."""
    root = ensure_workplace(sandbox_id)
    task = root / "task"
    if not task.is_dir():
        return ""
    safe_run = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(run_id or ""))[:48]
    scan_dirs: list[Path] = []
    if safe_run and (task / safe_run).is_dir():
        scan_dirs.append(task / safe_run)
    else:
        scan_dirs.append(task)
    lines: list[str] = []
    for d in scan_dirs:
        metas = sorted(d.glob("page_*.meta.json"))
        if not metas:
            # Fallback: infer from page json keys only
            for p in sorted(d.glob("page_*.json")):
                if p.name.endswith(".meta.json"):
                    continue
                try:
                    obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(obj, list) or not obj:
                    continue
                role = _infer_page_category(obj, "")
                lines.append(f"{p.name} → role={role} (no meta)")
            continue
        for mpath in metas:
            try:
                meta = json.loads(mpath.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(meta, dict):
                continue
            page = meta.get("page")
            view = meta.get("view") or "?"
            role = meta.get("role") or "?"
            nrows = meta.get("rows")
            base = mpath.name.replace(".meta.json", ".json")
            lines.append(f"{base} → view={view} role={role} rows={nrows}")
    return "\n".join(lines[:24])


def read_deliverable_headers(path: Path) -> list[str]:
    """Return first-row headers for csv/xlsx; empty on failure."""
    if not path or not path.is_file():
        return []
    suf = path.suffix.lower()
    try:
        if suf == ".csv":
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                line = fh.readline()
            if not line:
                return []
            return [c.strip().strip('"') for c in line.strip().split(",") if c.strip()]
        if suf in {".xlsx", ".xlsm", ".xls"}:
            import openpyxl

            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                ws = wb.active
                row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
            finally:
                wb.close()
            if not row:
                return []
            return [str(c).strip() for c in row if c is not None and str(c).strip()]
    except Exception:
        return []
    return []


def quarantine_root_deliverables(
    sandbox_id: str,
    run_id: str,
    *,
    target_dir: str = "",
) -> list[str]:
    """Move root/current-dir xlsx/csv into task/_stale_{run_id}/.

    Do NOT call on export start when multiple tasks may share one workplace
    concurrently — it steals sibling-run deliverables. Prefer run_id / mtime
    ownership at finish instead. Kept for rare manual/admin use.
    """
    root = ensure_workplace(sandbox_id)
    target = (target_dir or "").strip().strip("/")
    dest_dir = root / target if target else root
    if not dest_dir.is_dir():
        return []
    safe_run = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(run_id or "export"))[:48]
    if not safe_run.strip("_"):
        safe_run = "export"
    stale_dir = root / "task" / f"_stale_{safe_run}"
    moved: list[str] = []
    try:
        stale_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return []
    for f in list(dest_dir.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in {".xlsx", ".xlsm", ".xls", ".csv"}:
            continue
        if not is_valid_deliverable_file(f):
            continue
        dest = stale_dir / f.name
        try:
            if dest.exists():
                dest = stale_dir / f"{f.stem}_{int(time.time())}{f.suffix}"
            shutil.move(str(f), str(dest))
            moved.append(str(dest.relative_to(root)).replace("\\", "/"))
        except OSError:
            continue
    return moved


def _write_rows_xlsx(target: Path, rows: list[list]) -> bool:
    import openpyxl

    if not rows:
        return False
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for r_idx, row in enumerate(rows, 1):
        if not isinstance(row, (list, tuple)):
            continue
        for c_idx, value in enumerate(list(row)[:MAX_EXCEL_COLS * 4], 1):
            ws.cell(row=r_idx, column=c_idx, value="" if value is None else value)
    target.parent.mkdir(parents=True, exist_ok=True)
    wb.save(target)
    return is_valid_deliverable_file(target)


def count_data_rows(path: Path) -> int | None:
    """Row count excluding header when possible."""
    if not path or not path.is_file():
        return None
    suf = path.suffix.lower()
    try:
        if suf == ".csv":
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                n = sum(1 for _ in fh)
            return max(0, n - 1) if n else 0
        if suf in {".xlsx", ".xlsm"}:
            import openpyxl

            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                ws = wb.active
                total = ws.max_row or 0
                return max(0, total - 1)
            finally:
                wb.close()
    except Exception:
        return None
    return None


def materialize_export_deliverable(
    sandbox_id: str,
    *,
    preferred_name: str = "",
    target_dir: str = "",
    ignore_existing: bool = False,
) -> str | None:
    """Ensure a valid current-dir xlsx/csv exists; build from task/ JSON if needed."""
    root = ensure_workplace(sandbox_id)
    target = (target_dir or "").strip().strip("/")
    dest_dir = root / target if target else root
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 1) Prefer existing valid file in current dir (unless ignored for fresh fallback)
    if not ignore_existing:
        for f in sorted(dest_dir.iterdir(), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True):
            if not f.is_file():
                continue
            if f.suffix.lower() not in {".xlsx", ".xlsm", ".xls", ".csv"}:
                continue
            if is_valid_deliverable_file(f):
                rel = str(f.relative_to(root)).replace("\\", "/")
                return rel

    # Remove empty/fake deliverables that block promotion
    for f in list(dest_dir.iterdir()) if dest_dir.is_dir() else []:
        if not f.is_file():
            continue
        if f.suffix.lower() not in {".xlsx", ".xlsm", ".xls", ".csv"}:
            continue
        if not is_valid_deliverable_file(f):
            try:
                f.unlink()
            except OSError:
                pass

    # 2) Promote best task csv/xlsx (skip stale quarantine dirs)
    best = find_best_task_data_file(sandbox_id)
    if best and "_stale_" not in best:
        promoted = promote_named_files(sandbox_id, [best], target_dir=target)
        if promoted:
            p = download_path(sandbox_id, promoted[0])
            if p and is_valid_deliverable_file(p):
                return promoted[0]

    # 3) Merge task JSON shards → xlsx
    rows = _merge_task_json_rows(sandbox_id)
    if not rows:
        return None

    name = _safe_filename(preferred_name) or ""
    if name and Path(name).suffix.lower() not in {".xlsx", ".xlsm"}:
        name = f"{Path(name).stem}.xlsx"
    if not name:
        name = "export.xlsx"
    dest_rel = f"{target}/{name}" if target else name
    dest = root / dest_rel
    if not _write_rows_xlsx(dest, rows):
        return None
    return dest_rel.replace("\\", "/")


# Align with react_engine._EXPORT_COLUMN_CALC_DOCS (14 analytical columns).
_ANALYZED_EXPORT_HEADERS = [
    "注册时间",
    "用户ID",
    "注册渠道",
    "总充值金额",
    "总提现金额",
    "当前余额(SC)",
    "总下注金额(SC)",
    "总返奖金额(SC)",
    "下注次数(SC投注次数)",
    "流水倍数(总下注金额/总充值金额)",
    "连续充值次数",
    "SC投注金额最多的游戏",
    "是否被封禁",
    "是否有退款",
]


_BAN_LABELS = {
    1: "登录",
    2: "提现",
    3: "充值",
    4: "充值",
    5: "提现",
}


def _analyzed_tz_offset_hours(time_window: dict | None) -> int:
    """Parse timezone like UTC-4 / America/New_York → hours offset from UTC."""
    if not isinstance(time_window, dict):
        return -4
    raw_off = time_window.get("utc_offset_hours")
    if raw_off is not None:
        try:
            return int(raw_off)
        except (TypeError, ValueError):
            pass
    raw = str(time_window.get("timezone") or time_window.get("tz_label") or "").strip()
    if not raw:
        return -4
    m = re.search(r"UTC\s*([+-]?\d+)", raw, flags=re.I)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return -4
    if "new_york" in raw.lower() or "america/new_york" in raw.lower():
        return -4
    return -4


def _load_task_page_rows(data_path: Path) -> list[dict]:
    """Load page_*.json which may be a bare list or {list: [...]}."""
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("list") or payload.get("data") or payload.get("rows") or []
    else:
        return []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _load_task_pages_indexed(task_dir: Path) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Load pages keyed by view and by role from sibling .meta.json."""
    by_view: dict[str, list[dict]] = {}
    by_role: dict[str, list[dict]] = {}
    for meta_path in sorted(task_dir.glob("page_*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        view = str(meta.get("view") or "").strip()
        role = str(meta.get("role") or "").strip()
        data_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".json"))
        if not data_path.is_file():
            continue
        rows = _load_task_page_rows(data_path)
        if not rows:
            continue
        if view:
            by_view.setdefault(view, []).extend(rows)
        if role:
            by_role.setdefault(role, []).extend(rows)
    return by_view, by_role


def _pick_rows(
    by_view: dict[str, list[dict]],
    by_role: dict[str, list[dict]],
    *,
    view_needles: tuple[str, ...] = (),
    roles: tuple[str, ...] = (),
    exclude_view: tuple[str, ...] = (),
) -> list[dict]:
    # Prefer view match so shared roles (e.g. order=pay+cash) do not mix.
    for view, rows in by_view.items():
        low = view.lower()
        if any(ex.lower() in low for ex in exclude_view):
            continue
        if view_needles and any(n.lower() in low for n in view_needles):
            return rows
    for role in roles:
        rows = by_role.get(role) or []
        if rows:
            return rows
    return []


def _pick_all_rows(
    by_view: dict[str, list[dict]],
    by_role: dict[str, list[dict]],
    *,
    view_needles: tuple[str, ...] = (),
    roles: tuple[str, ...] = (),
    exclude_view: tuple[str, ...] = (),
) -> list[dict]:
    rows_out: list[dict] = []
    for view, rows in by_view.items():
        low = view.lower()
        if any(ex.lower() in low for ex in exclude_view):
            continue
        if view_needles and any(n.lower() in low for n in view_needles):
            rows_out.extend(rows)
    if rows_out:
        return rows_out
    for role in roles:
        rows = by_role.get(role) or []
        if rows:
            rows_out.extend(rows)
    return rows_out


def _merge_user_grain_rows(base_users: list[dict], extra_rows: list[dict]) -> list[dict]:
    """Merge arbitrary user-grain pages into base user rows by uid."""
    by_uid: dict[str, dict] = {}
    order: list[str] = []
    for row in base_users or []:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("uid") or row.get("id") or row.get("user_id") or "").strip()
        if not uid:
            continue
        if uid not in by_uid:
            by_uid[uid] = dict(row)
            order.append(uid)
        else:
            by_uid[uid].update(row)
    for row in extra_rows or []:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("uid") or row.get("id") or row.get("user_id") or "").strip()
        if not uid:
            continue
        cur = by_uid.get(uid)
        if cur is None:
            by_uid[uid] = dict(row)
            order.append(uid)
        else:
            cur.update({k: v for k, v in row.items() if v is not None and v != ""})
    return [by_uid[uid] for uid in order if uid in by_uid]


def _max_pay_streak_between_game_events(pay_times: list[int], game_times: list[int]) -> int | str:
    pays = sorted(int(t) for t in pay_times if t is not None)
    games = sorted(int(t) for t in game_times if t is not None)
    if not pays:
        return 0
    if not games:
        return ""
    events = [(t, "game") for t in games] + [(t, "pay") for t in pays]
    events.sort(key=lambda item: (item[0], 0 if item[1] == "game" else 1))
    cur = 0
    best = 0
    seen_game = False
    for _, kind in events:
        if kind == "game":
            if seen_game:
                best = max(best, cur)
            seen_game = True
            cur = 0
        elif seen_game:
            cur += 1
    return max(best, cur)


def materialize_analyzed_export(
    sandbox_id: str,
    run_id: str,
    *,
    time_window: dict | None = None,
    column_headers: list[str] | None = None,
    preferred_name: str = "",
    target_dir: str = "",
    column_plan: list | None = None,
) -> str | None:
    """Join covered task pages into a Chinese analytical xlsx.

    When ``column_plan`` is provided, headers follow the plan (column-driven).
    Otherwise falls back to classic 14-col schema when fewer than 8 headers given.
    """
    root = ensure_workplace(sandbox_id)
    rid = str(run_id or "").strip()
    if not rid:
        return None
    task_dir = root / "task" / rid
    if not task_dir.is_dir():
        return None

    by_view, by_role = _load_task_pages_indexed(task_dir)
    if not by_view and not by_role:
        return None

    users = _pick_rows(
        by_view,
        by_role,
        view_needles=("user_info", "user_list"),
        roles=("user",),
        exclude_view=("gameuser", "user_bet", "user_pay", "user_cash"),
    )
    user_aux_rows: list[dict] = []
    for view, rows in (by_view or {}).items():
        low = str(view or "").lower()
        if any(ex.lower() in low for ex in ("gameuser", "user_bet", "user_pay", "user_cash")):
            continue
        if is_user_grain_payload(rows):
            user_aux_rows.extend(rows)
    if user_aux_rows:
        users = _merge_user_grain_rows(users, user_aux_rows)
    pays = _pick_all_rows(by_view, by_role, view_needles=("pay_order", "pay_order_log"))
    cash_rows = _pick_rows(by_view, by_role, view_needles=("cash_order", "cash_order_log"))
    bets = _pick_all_rows(
        by_view,
        by_role,
        view_needles=(
            "user_bet",
            "bet_log",
            "bet_order",
            "betstat_everyday",
            "everyday_bygame",
            "gameuser_betstat",
        ),
        roles=("bet",),
    )
    # Exhaust / incomplete fetch: synthesize identity from fact uids so join still writes
    if not users:
        syn_uids: list[str] = []
        seen_syn: set[str] = set()
        for src in (pays, cash_rows, bets):
            for row in src or []:
                if not isinstance(row, dict):
                    continue
                uid = str(row.get("uid") or row.get("user_id") or "").strip()
                if uid and uid not in seen_syn:
                    seen_syn.add(uid)
                    syn_uids.append(uid)
        if not syn_uids:
            return None
        users = [{"uid": u} for u in syn_uids]

    # Deduplicate users by uid (multi-page fetch)
    seen_uids: set[str] = set()
    uniq_users: list[dict] = []
    for u in users:
        uid = str(u.get("uid") or u.get("id") or u.get("user_id") or "").strip()
        if not uid or uid in seen_uids:
            continue
        seen_uids.add(uid)
        uniq_users.append(u)
    users = uniq_users

    channels = _pick_rows(
        by_view,
        by_role,
        view_needles=("config_channel", "channel"),
        roles=("dim_channel", "channel"),
        exclude_view=("user_channel",),
    )
    games = _pick_rows(
        by_view,
        by_role,
        view_needles=("config_game", "game_list", "game_info"),
        roles=("dim_game", "game"),
        exclude_view=("gameuser", "user_bet"),
    )

    channel_map: dict[str, str] = {}
    for ch in channels:
        cid = ch.get("channel_id")
        if cid is None:
            cid = ch.get("id")
        if cid is None:
            continue
        name = str(ch.get("channel_name") or ch.get("name") or ch.get("title") or "").strip()
        if name:
            channel_map[str(cid)] = name

    game_map: dict[str, str] = {}
    for g in games:
        gid = g.get("game_id")
        if gid is None:
            gid = g.get("id")
        if gid is None:
            continue
        gname = str(g.get("game_name") or g.get("name") or g.get("title") or "").strip()
        label = f"{gname},{gid}" if gname else str(gid)
        game_map[str(gid)] = label

    # pay: successful recharge uses status=2; refunds use status=4.
    pay_amt: dict[str, float] = {}
    refund_amt: dict[str, float] = {}
    pay_card_cnt: dict[str, int] = {}
    pay_event_times: dict[str, list[int]] = {}
    pay_cohort_uids: list[str] = []
    seen_pay_cohort_uids: set[str] = set()
    refund_uids: set[str] = set()
    for p in pays:
        uid = str(p.get("uid") or p.get("user_id") or "").strip()
        if not uid:
            continue
        if p.get("cohort_uid") is not None:
            if uid not in seen_pay_cohort_uids:
                seen_pay_cohort_uids.add(uid)
                pay_cohort_uids.append(uid)
            continue
        if p.get("card_cnt") is not None:
            try:
                pay_card_cnt[uid] = pay_card_cnt.get(uid, 0) + int(float(p.get("card_cnt") or 0))
            except (TypeError, ValueError):
                pass
        if p.get("refund_sum") is not None:
            refund_uids.add(uid)
            try:
                refund_amt[uid] = refund_amt.get(uid, 0.0) + float(p.get("refund_sum") or 0)
            except (TypeError, ValueError):
                pass
            continue
        if p.get("pay_sum") is not None:
            if uid not in seen_pay_cohort_uids:
                seen_pay_cohort_uids.add(uid)
                pay_cohort_uids.append(uid)
            try:
                pay_amt[uid] = pay_amt.get(uid, 0.0) + float(p.get("pay_sum") or 0)
            except (TypeError, ValueError):
                pass
            continue
        try:
            st = int(p.get("status") if p.get("status") is not None else -1)
        except (TypeError, ValueError):
            st = -1
        if st == 4:
            refund_uids.add(uid)
            raw_refund = p.get("price")
            if raw_refund is None:
                raw_refund = p.get("amount") or 0
            try:
                refund_amt[uid] = refund_amt.get(uid, 0.0) + float(raw_refund) / 100.0
            except (TypeError, ValueError):
                pass
        if st != 2:
            continue
        if uid not in seen_pay_cohort_uids:
            seen_pay_cohort_uids.add(uid)
            pay_cohort_uids.append(uid)
        if p.get("create_time") is not None:
            try:
                pay_event_times.setdefault(uid, []).append(int(p.get("create_time")))
            except (TypeError, ValueError):
                pass
        raw_amt = p.get("price")
        if raw_amt is None:
            raw_amt = p.get("amount") or 0
        try:
            amount = float(raw_amt) / 100.0
        except (TypeError, ValueError):
            amount = 0.0
        pay_amt[uid] = pay_amt.get(uid, 0.0) + amount

    cash_amt: dict[str, float] = {}
    cash_card_cnt: dict[str, int] = {}
    for c in cash_rows:
        uid = str(c.get("uid") or c.get("user_id") or "").strip()
        if not uid:
            continue
        if c.get("card_cnt") is not None:
            try:
                cash_card_cnt[uid] = cash_card_cnt.get(uid, 0) + int(float(c.get("card_cnt") or 0))
            except (TypeError, ValueError):
                pass
        if c.get("cash_sum") is not None:
            try:
                cash_amt[uid] = cash_amt.get(uid, 0.0) + float(c.get("cash_sum") or 0)
            except (TypeError, ValueError):
                pass
            continue
        try:
            st = int(c.get("status") if c.get("status") is not None else -1)
        except (TypeError, ValueError):
            st = -1
        if st != 2:
            continue
        try:
            amount = float(c.get("amount") or 0) / 100.0
        except (TypeError, ValueError):
            amount = 0.0
        cash_amt[uid] = cash_amt.get(uid, 0.0) + amount

    # bet: support raw bet_log OR everyday_bygame / pre-aggregated pages
    bet_sc_sum: dict[str, float] = {}
    win_sc_sum: dict[str, float] = {}
    bet_cnt: dict[str, int] = {}
    bet_game: dict[str, dict[str, float]] = {}
    game_event_times: dict[str, list[int]] = {}
    for b in bets:
        uid = str(b.get("uid") or b.get("user_id") or "").strip()
        if not uid:
            continue
        if b.get("create_time") is not None:
            try:
                game_event_times.setdefault(uid, []).append(int(b.get("create_time")))
            except (TypeError, ValueError):
                pass
        # Pre-aggregated query-graph rows
        if b.get("bet_sum") is not None or b.get("win_sum") is not None or b.get("bet_cnt") is not None:
            try:
                if b.get("bet_sum") is not None:
                    bet_sc_sum[uid] = bet_sc_sum.get(uid, 0.0) + float(b.get("bet_sum") or 0)
                if b.get("win_sum") is not None:
                    win_sc_sum[uid] = win_sc_sum.get(uid, 0.0) + float(b.get("win_sum") or 0)
                if b.get("bet_cnt") is not None:
                    bet_cnt[uid] = bet_cnt.get(uid, 0) + int(float(b.get("bet_cnt") or 0))
            except (TypeError, ValueError):
                pass
            continue
        if b.get("amt") is not None and b.get("game_id") is not None:
            try:
                sc = float(b.get("amt") or 0)
            except (TypeError, ValueError):
                sc = 0.0
            gid = b.get("game_id")
            if str(gid) not in ("", "0", "None"):
                bucket = bet_game.setdefault(uid, {})
                key = str(gid)
                bucket[key] = bucket.get(key, 0.0) + sc
            continue
        # everyday_bygame: goods_type + bet_value / result_value / bet_nums (cents)
        if b.get("bet_value") is not None or b.get("result_value") is not None or b.get("bet_nums") is not None:
            try:
                gt = int(b.get("goods_type") if b.get("goods_type") is not None else 1)
            except (TypeError, ValueError):
                gt = 1
            if gt not in (1, 4):
                continue
            try:
                sc = float(b.get("bet_value") or 0) / 100.0
            except (TypeError, ValueError):
                sc = 0.0
            try:
                win = float(b.get("result_value") or 0) / 100.0
            except (TypeError, ValueError):
                win = 0.0
            try:
                n = int(float(b.get("bet_nums") or 0))
            except (TypeError, ValueError):
                n = 0
            bet_sc_sum[uid] = bet_sc_sum.get(uid, 0.0) + sc
            win_sc_sum[uid] = win_sc_sum.get(uid, 0.0) + win
            bet_cnt[uid] = bet_cnt.get(uid, 0) + n
            gid = b.get("game_id")
            if gid is not None and str(gid) not in ("", "0", "None"):
                bucket = bet_game.setdefault(uid, {})
                key = str(gid)
                bucket[key] = bucket.get(key, 0.0) + sc
            continue
        metric_keys = (
            "type",
            "status",
            "bet_sc",
            "amount",
            "win_sc",
            "win_amount",
            "game_id",
        )
        if not any(b.get(k) is not None for k in metric_keys):
            continue
        try:
            btype = int(b.get("type") if b.get("type") is not None else 1)
        except (TypeError, ValueError):
            btype = 1
        if btype != 1:
            continue
        try:
            st = int(b.get("status") if b.get("status") is not None else 0)
        except (TypeError, ValueError):
            st = 0
        if st == 2:
            continue
        try:
            sc = float(b.get("bet_sc") if b.get("bet_sc") is not None else (b.get("amount") or 0))
        except (TypeError, ValueError):
            sc = 0.0
        try:
            win = float(b.get("win_sc") if b.get("win_sc") is not None else (b.get("win_amount") or 0))
        except (TypeError, ValueError):
            win = 0.0
        bet_sc_sum[uid] = bet_sc_sum.get(uid, 0.0) + sc
        win_sc_sum[uid] = win_sc_sum.get(uid, 0.0) + win
        bet_cnt[uid] = bet_cnt.get(uid, 0) + 1
        gid = b.get("game_id")
        if gid is not None and str(gid) not in ("", "0", "None"):
            bucket = bet_game.setdefault(uid, {})
            key = str(gid)
            bucket[key] = bucket.get(key, 0.0) + sc

    if isinstance(time_window, dict) and time_window.get("cohort") == "pay" and pay_cohort_uids:
        user_by_uid = {
            str(u.get("uid") or u.get("id") or u.get("user_id") or "").strip(): u
            for u in users
            if str(u.get("uid") or u.get("id") or u.get("user_id") or "").strip()
        }
        users = [user_by_uid.get(uid, {"uid": uid}) for uid in pay_cohort_uids]

    # Optional time-window filter on register_time. Pay cohort is already
    # defined by pay rows; user_info is only an attribute lookup by uid.
    win_start = None
    win_end = None
    filter_users_by_register_time = not (
        isinstance(time_window, dict) and time_window.get("cohort") == "pay"
    )
    if filter_users_by_register_time and isinstance(time_window, dict):
        for key, dest in (("start_ms", "start"), ("end_ms", "end"), ("start", "start"), ("end", "end")):
            raw = time_window.get(key)
            if raw is None:
                continue
            try:
                val = int(raw)
            except (TypeError, ValueError):
                continue
            if dest == "start":
                win_start = val
            else:
                win_end = val

    tz_hours = _analyzed_tz_offset_hours(time_window)
    headers = [str(h).strip() for h in (column_headers or []) if str(h).strip()]
    if column_plan:
        plan_headers = [
            str(c.get("header") or "").strip()
            for c in column_plan
            if isinstance(c, dict) and str(c.get("header") or "").strip()
        ]
        if plan_headers:
            headers = plan_headers
    if len(headers) < 8 and not column_plan:
        headers = list(_ANALYZED_EXPORT_HEADERS)

    def _top_game(bucket: dict[str, float] | None) -> str:
        if not bucket:
            return ""
        best = max(bucket.items(), key=lambda kv: kv[1])
        return game_map.get(best[0], f"{best[0]}")

    def _reg_time(row: dict) -> str:
        raw = row.get("register_time") or row.get("reg_time") or row.get("create_time")
        if raw is None or raw == "":
            return ""
        try:
            ts = int(raw)
            if ts > 10_000_000_000:  # ms
                ts_sec = ts / 1000.0
            else:
                ts_sec = float(ts)
            dt = datetime.fromtimestamp(ts_sec, tz=timezone(timedelta(hours=tz_hours)))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(raw)

    def _ban_label(row: dict) -> str:
        for key in ("ban_type", "limit_type"):
            if key not in row or row.get(key) is None:
                continue
            try:
                code = int(row.get(key))
            except (TypeError, ValueError):
                continue
            if code == 0:
                return ""
            return _BAN_LABELS.get(code, f"封禁({code})")
        return ""

    def _lookup_value(stem: str, values: dict) -> object:
        if stem in values:
            return values[stem]
        for k, v in values.items():
            if k and (k in stem or stem in k):
                return v
        return ""

    out_rows: list[list] = [headers]
    for u in users:
        uid = str(u.get("uid") or u.get("id") or u.get("user_id") or "").strip()
        if not uid:
            continue
        if win_start is not None or win_end is not None:
            try:
                rt = int(u.get("register_time") or 0)
            except (TypeError, ValueError):
                rt = 0
            if win_start is not None and rt < win_start:
                continue
            if win_end is not None and rt >= win_end:
                continue

        pay_total = float(pay_amt.get(uid, 0.0))
        refund_total = float(refund_amt.get(uid, 0.0))
        cash_total = float(cash_amt.get(uid, 0.0))
        bet_total = float(bet_sc_sum.get(uid, 0.0))
        win_total = float(win_sc_sum.get(uid, 0.0))
        n_bets = int(bet_cnt.get(uid, 0))
        turnover: float | str = ""
        if pay_total > 1e-12:
            turnover = round(bet_total / pay_total, 2)

        ch_id = u.get("channel_id")
        ch_name = channel_map.get(str(ch_id), "") if ch_id is not None else ""
        if not ch_name and ch_id is not None:
            ch_name = str(ch_id)

        try:
            balance = float(u.get("sc") if u.get("sc") is not None else 0) / 100.0
        except (TypeError, ValueError):
            balance = 0.0

        values = {
            "注册时间": _reg_time(u),
            "用户ID": uid,
            "注册渠道": ch_name,
            "渠道": ch_name,
            "姓": str(u.get("first_name") or u.get("surname") or ""),
            "名": str(u.get("last_name") or u.get("name") or ""),
            "手机": str(u.get("mobile") or u.get("phone") or ""),
            "邮箱": str(u.get("email") or ""),
            "总充值金额": round(pay_total, 2),
            "总提现金额": round(cash_total, 2),
            "退款金额": round(refund_total, 2),
            "总退款金额": round(refund_total, 2),
            "当前余额": int(balance) if abs(balance - int(balance)) < 1e-9 else round(balance, 2),
            "总下注金额": int(bet_total) if abs(bet_total - int(bet_total)) < 1e-9 else round(bet_total, 2),
            "总返奖金额": int(win_total) if abs(win_total - int(win_total)) < 1e-9 else round(win_total, 2),
            "下注次数": n_bets,
            "流水倍数": turnover,
            "连续充值次数": _max_pay_streak_between_game_events(
                pay_event_times.get(uid, []),
                game_event_times.get(uid, []),
            ),
            "SC投注金额最多的游戏": _top_game(bet_game.get(uid)),
            "投注最多的游戏": _top_game(bet_game.get(uid)),
            "是否被封禁": _ban_label(u),
            "封禁状态": _ban_label(u),
            "是否有退款": "有" if uid in refund_uids else "",
            "是否退款": "有" if uid in refund_uids else "",
            "充值银行卡数量": pay_card_cnt.get(uid, 0),
            "充值银行卡": pay_card_cnt.get(uid, 0),
            "提现银行卡数量": cash_card_cnt.get(uid, 0),
            "提现银行卡": cash_card_cnt.get(uid, 0),
            "注册IP": str(u.get("register_ip") or u.get("reg_ip") or ""),
        }

        row_out: list = []
        for h in headers:
            stem = re.split(r"[（(]", h, maxsplit=1)[0].strip()
            row_out.append(_lookup_value(stem, values))
        out_rows.append(row_out)

    if len(out_rows) <= 1:
        return None

    target = (target_dir or "").strip().strip("/\\")
    name = _safe_filename(preferred_name) or ""
    if name and Path(name).suffix.lower() not in {".xlsx", ".xlsm"}:
        name = f"{Path(name).stem}.xlsx"
    if not name:
        name = f"用户分析_{rid}.xlsx"
    dest_rel = f"{target}/{name}" if target else name
    dest = root / dest_rel
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    try:
        import pandas as pd  # type: ignore

        df = pd.DataFrame(out_rows[1:], columns=out_rows[0])
        df.to_excel(dest, index=False, engine="openpyxl")
        if dest.is_file() and dest.stat().st_size > 0:
            return dest_rel.replace("\\", "/")
    except Exception:
        pass

    if not _write_rows_xlsx(dest, out_rows):
        return None
    return dest_rel.replace("\\", "/")


def find_files(sandbox_id: str, name: str) -> list[str]:
    """Find files by basename under workplace (recursive)."""
    root = ensure_workplace(sandbox_id)
    target = name.strip()
    if not target:
        return []
    found: list[str] = []
    for p in root.rglob("*"):
        if p.is_file() and p.name == target:
            found.append(str(p.relative_to(root)).replace("\\", "/"))
    return found


def list_recent_data_files(sandbox_id: str, limit: int = 20) -> list[str]:
    """List .xlsx/.xls/.csv under workplace, newest modified first."""
    root = ensure_workplace(sandbox_id)
    if not root.is_dir():
        return []
    suffixes = {".xlsx", ".xls", ".csv", ".xlsm"}
    files: list[tuple[float, str]] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in suffixes:
            continue
        if not is_valid_deliverable_file(p):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        files.append((mtime, rel))
    files.sort(key=lambda x: x[0], reverse=True)
    return [rel for _, rel in files[: max(1, limit)]]


def _cell_value(value) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_sheet_rows(rows: list[list]) -> list[list[str]]:
    if not rows:
        return [[""]]
    max_cols = max(len(row) for row in rows)
    return [
        [_cell_value(cell) for cell in row] + [""] * (max_cols - len(row))
        for row in rows
    ]


def _read_excel_preview(target: Path) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(target, read_only=True, data_only=True)
    sheets = []
    truncated = False
    try:
        for name in wb.sheetnames:
            ws = wb[name]
            rows = []
            for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
                if row_idx >= MAX_EXCEL_ROWS:
                    truncated = True
                    break
                cells = [_cell_value(cell) for cell in row[:MAX_EXCEL_COLS]]
                if len(row) > MAX_EXCEL_COLS:
                    truncated = True
                rows.append(cells)
            sheets.append({"name": name, "rows": _normalize_sheet_rows(rows)})
    finally:
        wb.close()
    return {"ok": True, "type": "excel", "sheets": sheets, "truncated": truncated}


def _save_excel_workbook(target: Path, sheets_json: str) -> dict:
    import openpyxl

    try:
        sheets = json.loads(sheets_json or "[]")
    except json.JSONDecodeError:
        return {"ok": False, "msg": "无效的表格数据"}
    if not isinstance(sheets, list):
        return {"ok": False, "msg": "无效的表格数据"}

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue
        title = str(sheet.get("name") or "Sheet1")[:31] or "Sheet1"
        ws = wb.create_sheet(title=title)
        for row_idx, row in enumerate(sheet.get("rows") or [], 1):
            if not isinstance(row, list):
                continue
            for col_idx, value in enumerate(row[:MAX_EXCEL_COLS], 1):
                ws.cell(row=row_idx, column=col_idx, value="" if value is None else str(value))
    if not wb.sheetnames:
        wb.create_sheet("Sheet1")
    target.parent.mkdir(parents=True, exist_ok=True)
    wb.save(target)
    return {"ok": True}


def wp_action(sandbox_id: str, action: str, body) -> dict:
    root = _resolved_wp_root(sandbox_id)
    path = body.path or ""
    target = _path_under_root(root, path.lstrip("/"))
    if target is None:
        return {"ok": False, "msg": "非法路径"}

    if action == "mkdir_workplace":
        target.mkdir(parents=True, exist_ok=True)
        return {"ok": True}
    if action == "delete_workplace":
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
        return {"ok": True}
    if action == "rename_workplace":
        new_name = body.new_name or body.name or ""
        if "/" not in new_name.lstrip("/"):
            if target == root:
                new_rel = new_name
            else:
                new_rel = f"{target.parent.relative_to(root)}/{new_name}".replace("\\", "/")
        else:
            new_rel = new_name.lstrip("/")
        new_path = _path_under_root(root, new_rel)
        if new_path is None:
            return {"ok": False, "msg": "非法路径"}
        target.rename(new_path)
        return {"ok": True}
    if action == "move_workplace":
        dest = _path_under_root(root, (body.dest or "").lstrip("/"))
        if dest is None:
            return {"ok": False, "msg": "非法路径"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(dest))
        return {"ok": True}
    if action == "view_workplace":
        if not target.is_file():
            return {"ok": False, "msg": "不是文件"}
        ext = target.suffix.lower()
        if ext in EXCEL_EXTS:
            try:
                return _read_excel_preview(target)
            except Exception as exc:
                return {"ok": False, "msg": f"无法读取 Excel：{exc}"}
        if ext == ".xls":
            return {
                "ok": False,
                "type": "excel_legacy",
                "msg": "旧版 .xls 文件请下载后使用 Excel 打开",
            }
        return {
            "ok": True,
            "type": "text",
            "content": target.read_text(encoding="utf-8", errors="replace")[:50000],
        }
    if action == "save_workplace":
        if target.suffix.lower() in EXCEL_EXTS:
            return {"ok": False, "msg": "Excel 文件请使用表格编辑器保存"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.content or "", encoding="utf-8")
        return {"ok": True}
    if action == "save_excel_workplace":
        if target.suffix.lower() not in EXCEL_EXTS:
            return {"ok": False, "msg": "不是 Excel 文件"}
        return _save_excel_workbook(target, body.content or "[]")
    if action == "zip_workplace":
        zip_path = target.with_suffix(".zip") if target.is_file() else root / f"{target.name}.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            if target.is_dir():
                for f in target.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(target.parent))
            else:
                zf.write(target, target.name)
        return {"ok": True, "zip": str(zip_path.name)}
    if action == "unzip_workplace":
        with zipfile.ZipFile(target, "r") as zf:
            zf.extractall(target.parent)
        return {"ok": True}
    return {"ok": False, "msg": "未知操作"}


def download_path(sandbox_id: str, rel: str) -> Path | None:
    root = _resolved_wp_root(sandbox_id)
    clean = str(rel or "").strip().lstrip("/")
    if clean.startswith("workplace/"):
        clean = clean[len("workplace/") :]
    p = _path_under_root(root, clean)
    if p and p.is_file():
        return p
    return None


def upload_file(sandbox_id: str, rel_dir: str, filename: str, content: bytes) -> dict:
    root = _resolved_wp_root(sandbox_id)
    root.mkdir(parents=True, exist_ok=True)
    target_dir = _path_under_root(root, rel_dir.lstrip("/"))
    if target_dir is None:
        return {"ok": False, "msg": "非法路径"}
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(filename)
    if not safe_name:
        return {"ok": False, "msg": "无效文件名"}
    target = _path_under_root(target_dir, safe_name)
    if target is None:
        return {"ok": False, "msg": "非法路径"}
    target.write_bytes(content)
    rel_path = str(target.relative_to(root)).replace("\\", "/")
    return {"ok": True, "path": rel_path}
