import os
import secrets as _secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

app = FastAPI(title="NAS Admin")
templates = Jinja2Templates(directory="templates")
_basic = HTTPBasic(auto_error=False)

AUTH_MODE = os.environ.get("AUTH_MODE", "none")
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
NAS_MOUNT  = os.environ.get("NAS_MOUNT", "/mnt/nas")
NAS_SOURCE = os.environ.get("NAS_SOURCE", "")   # e.g. //192.168.178.102/service


def _load_k8s():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


_load_k8s()


def check_auth(creds: Optional[HTTPBasicCredentials] = Depends(_basic)):
    if AUTH_MODE != "basic":
        return None
    if creds is None:
        raise HTTPException(
            status_code=401,
            headers={"WWW-Authenticate": "Basic"},
            detail="Authentication required",
        )
    ok_u = _secrets.compare_digest(creds.username.encode(), AUTH_USERNAME.encode())
    ok_p = _secrets.compare_digest(creds.password.encode(), AUTH_PASSWORD.encode())
    if not (ok_u and ok_p):
        raise HTTPException(
            status_code=401,
            headers={"WWW-Authenticate": "Basic"},
            detail="Invalid credentials",
        )
    return creds


def _age(ts) -> str:
    if ts is None:
        return "—"
    delta = datetime.now(timezone.utc) - ts
    d, s = delta.days, delta.seconds
    if d > 0:
        return f"{d}d"
    if s >= 3600:
        return f"{s // 3600}h"
    return f"{s // 60}m"


def _smb_pvs():
    v1 = client.CoreV1Api()
    result = []
    for pv in v1.list_persistent_volume().items:
        spec = pv.spec
        is_smb_csi = spec.csi and "smb" in (spec.csi.driver or "")
        is_smb_sc = (
            "smb" in (spec.storage_class_name or "").lower()
            or "nas" in (spec.storage_class_name or "").lower()
        )
        if not (is_smb_csi or is_smb_sc):
            continue
        source = ""
        if spec.csi and spec.csi.volume_attributes:
            source = spec.csi.volume_attributes.get("source", "")
        claim_ref = None
        if spec.claim_ref:
            claim_ref = {
                "namespace": spec.claim_ref.namespace,
                "name": spec.claim_ref.name,
            }
        phase = (pv.status.phase if pv.status else None) or "Unknown"
        ann = pv.metadata.annotations or {}
        result.append({
            "name": pv.metadata.name,
            "capacity": (pv.spec.capacity or {}).get("storage", "?"),
            "access_modes": pv.spec.access_modes or [],
            "storage_class": pv.spec.storage_class_name or "—",
            "status": phase,
            "nas_path": source,
            "bound_claim": claim_ref,
            "age": _age(pv.metadata.creation_timestamp),
            "orphaned": phase in ("Released", "Available", "Failed"),
            "deleted_at":           ann.get("nas-admin/deleted-at"),
            "deleted_pvc":          ann.get("nas-admin/deleted-pvc"),
            "deleted_sc":           ann.get("nas-admin/deleted-sc"),
            "deleted_pods":         ann.get("nas-admin/deleted-pods"),
            "deleted_nas_path":  ann.get("nas-admin/deleted-nas-path"),
            "deleted_nas_marked": ann.get("nas-admin/nas-dir-marked", ""),
        })
    return result


def _pvcs_with_owners():
    v1 = client.CoreV1Api()
    pvcs = v1.list_persistent_volume_claim_for_all_namespaces().items
    pods = v1.list_pod_for_all_namespaces().items

    pvc_pods: dict = {}
    for pod in pods:
        ns = pod.metadata.namespace
        for vol in pod.spec.volumes or []:
            if vol.persistent_volume_claim:
                key = (ns, vol.persistent_volume_claim.claim_name)
                pvc_pods.setdefault(key, []).append(pod.metadata.name)

    result = []
    for pvc in pvcs:
        sc = pvc.spec.storage_class_name or ""
        ns, name = pvc.metadata.namespace, pvc.metadata.name
        owners = pvc_pods.get((ns, name), [])
        phase = (pvc.status.phase if pvc.status else None) or "Unknown"
        cap = None
        if pvc.status and pvc.status.capacity:
            cap = pvc.status.capacity.get("storage")
        if not cap and pvc.spec.resources and pvc.spec.resources.requests:
            cap = pvc.spec.resources.requests.get("storage")
        is_smb = "smb" in sc.lower() or "nas" in sc.lower()
        result.append({
            "name": name,
            "namespace": ns,
            "status": phase,
            "storage_class": sc or "—",
            "capacity": cap or "?",
            "volume_name": pvc.spec.volume_name or "—",
            "access_modes": pvc.spec.access_modes or [],
            "age": _age(pvc.metadata.creation_timestamp),
            "pods": owners,
            "unused": phase == "Bound" and len(owners) == 0,
            "is_smb": is_smb,
        })
    return result


def _dir_entries(rel_path: str):
    base = Path(NAS_MOUNT).resolve()
    try:
        target = (base / rel_path.lstrip("/")).resolve()
        target.relative_to(base)  # raises ValueError if path escapes base
    except (ValueError, Exception):
        return [], "/"
    entries = []
    try:
        items = sorted(
            target.iterdir(),
            key=lambda e: (not e.is_dir(), e.name.lower()),
        )
        for entry in items:
            try:
                st = entry.stat()
                rel = "/" + str(entry.relative_to(base))
                entries.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": _fmt(st.st_size) if entry.is_file() else None,
                    "path": rel,
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                })
            except (PermissionError, OSError):
                pass
    except (PermissionError, FileNotFoundError):
        pass
    cur = (
        "/" if target == base
        else "/" + str(target.relative_to(base))
    )
    return entries, cur


def _fmt(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def _safe_name(s: str) -> str:
    """Sanitize a string for use in a directory name component."""
    return s.replace("/", "-").replace(":", "-").replace(" ", "-").strip("-")


def _mark_nas_dir_deleted(nas_path: str, suffix: str) -> str | None:
    """Create a .nas-admin-deleted-{suffix} marker file inside the NAS dir.
    Returns the marker file path on success, None otherwise.
    Uses a marker file (not rename) because the NAS root share disallows renaming its children."""
    if not NAS_SOURCE or not nas_path or not NAS_MOUNT:
        return None
    source = NAS_SOURCE.rstrip("/")
    if not nas_path.startswith(source + "/"):
        return None  # different share — can't reach via our mount
    rel = nas_path[len(source) + 1:]
    if not rel:
        return None  # don't touch the root
    base = Path(NAS_MOUNT).resolve()
    local = (base / rel).resolve()
    try:
        local.relative_to(base)  # path traversal guard
    except ValueError:
        return None
    if not local.exists() or not local.is_dir():
        return None
    marker = local / f".nas-admin-deleted{suffix}"
    if marker.exists():
        return str(marker)  # already marked
    try:
        marker.write_text(f"marked by nas-admin\n")
        return str(marker)
    except OSError:
        return None



def _crumbs(cur_path: str):
    parts = [p for p in cur_path.split("/") if p]
    crumbs = [{"name": "nas", "path": "/"}]
    for i, p in enumerate(parts):
        crumbs.append({"name": p, "path": "/" + "/".join(parts[: i + 1])})
    return crumbs


@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse("/pvs")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/pvs", response_class=HTMLResponse)
async def pvs_page(request: Request, _=Depends(check_auth)):
    pvs_list = _smb_pvs()
    return templates.TemplateResponse(
        "pvs.html",
        {
            "request": request,
            "pvs": pvs_list,
            "orphan_count": sum(1 for p in pvs_list if p["orphaned"]),
        },
    )


@app.get("/pvcs", response_class=HTMLResponse)
async def pvcs_page(request: Request, only_smb: bool = False, _=Depends(check_auth)):
    pvcs_list = _pvcs_with_owners()
    if only_smb:
        pvcs_list = [p for p in pvcs_list if p["is_smb"]]
    return templates.TemplateResponse(
        "pvcs.html",
        {
            "request": request,
            "pvcs": pvcs_list,
            "only_smb": only_smb,
            "unused_count": sum(1 for p in pvcs_list if p["unused"]),
        },
    )


@app.get("/browse", response_class=HTMLResponse)
async def browse_page(request: Request, path: str = "/", _=Depends(check_auth)):
    nas_ok = Path(NAS_MOUNT).exists()
    entries, cur = _dir_entries(path) if nas_ok else ([], "/")
    return templates.TemplateResponse(
        "browse.html",
        {
            "request": request,
            "entries": entries,
            "current_path": cur,
            "crumbs": _crumbs(cur),
            "nas_available": nas_ok,
            "nas_mount": NAS_MOUNT,
        },
    )


@app.get("/api/browse", response_class=HTMLResponse)
async def browse_partial(request: Request, path: str = "/", _=Depends(check_auth)):
    entries, cur = _dir_entries(path)
    return templates.TemplateResponse(
        "_browse_content.html",
        {
            "request": request,
            "entries": entries,
            "current_path": cur,
            "crumbs": _crumbs(cur),
        },
    )


@app.delete("/api/pvcs/{namespace}/{name}", response_class=HTMLResponse)
async def delete_pvc(namespace: str, name: str, _=Depends(check_auth)):
    v1 = client.CoreV1Api()
    try:
        pvc = v1.read_namespaced_persistent_volume_claim(name=name, namespace=namespace)
        pv_name = pvc.spec.volume_name
        sc = pvc.spec.storage_class_name or "—"

        # Find pods using this PVC before deletion
        pods = v1.list_namespaced_pod(namespace=namespace).items
        pod_names = [
            p.metadata.name for p in pods
            if any(
                v.persistent_volume_claim and v.persistent_volume_claim.claim_name == name
                for v in (p.spec.volumes or [])
            )
        ]

        # Annotate the bound PV with deletion context so Released PVs stay identifiable
        if pv_name:
            try:
                pv_obj = v1.read_persistent_volume(pv_name)
                nas_path = ""
                if pv_obj.spec.csi and pv_obj.spec.csi.volume_attributes:
                    nas_path = pv_obj.spec.csi.volume_attributes.get("source", "")
                deleted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                # Rename the NAS directory to mark it as deleted
                marker_suffix = "_" + "_".join([
                    _safe_name(sc),
                    _safe_name(namespace),
                    _safe_name(name),
                ])
                marker_path = _mark_nas_dir_deleted(nas_path, marker_suffix)
                v1.patch_persistent_volume(pv_name, {
                    "metadata": {"annotations": {
                        "nas-admin/deleted-at":       deleted_at,
                        "nas-admin/deleted-pvc":      f"{namespace}/{name}",
                        "nas-admin/deleted-sc":       sc,
                        "nas-admin/deleted-pods":     ",".join(pod_names) if pod_names else "—",
                        "nas-admin/deleted-nas-path": nas_path,
                        "nas-admin/nas-dir-marked":   marker_path or "",
                    }}
                })
            except ApiException:
                pass  # annotation is best-effort — don't block deletion

        v1.delete_namespaced_persistent_volume_claim(name=name, namespace=namespace)
    except ApiException as e:
        raise HTTPException(status_code=e.status, detail=str(e.reason))
    toast = ""
    if pv_name:
        toast = (
            f'<div hx-swap-oob="beforeend:#toast-container">'
            f'<div id="toast-pvc-deleted" class="flex items-start gap-3 bg-blue-50 border border-blue-200 '
            f'rounded-lg px-4 py-3 text-sm text-blue-900 shadow-md max-w-sm" '
            f'style="animation:fadeout 0.5s ease 6s forwards">'
            f'<span>PVC deleted. PV <span class="font-mono font-semibold">{pv_name}</span> is now Released — '
            f'find it in the <a href="/pvs" class="underline font-semibold">Volumes tab</a> to clean up.</span>'
            f'</div></div>'
        )
    return HTMLResponse(content=toast, status_code=200)


@app.delete("/api/pvs/{name}", response_class=HTMLResponse)
async def delete_pv(name: str, _=Depends(check_auth)):
    v1 = client.CoreV1Api()
    try:
        pv = v1.read_persistent_volume(name=name)
        phase = (pv.status.phase if pv.status else None) or "Unknown"
        if phase == "Bound":
            raise HTTPException(status_code=409, detail="Cannot delete a Bound PV — delete the PVC first")

        # Collect NAS path and deletion context from annotations or PV spec
        ann = pv.metadata.annotations or {}
        nas_path = ann.get("nas-admin/deleted-nas-path") or (
            (pv.spec.csi.volume_attributes or {}).get("source", "")
            if pv.spec.csi else ""
        )
        sc = ann.get("nas-admin/deleted-sc") or pv.spec.storage_class_name or ""
        orig_pvc = ann.get("nas-admin/deleted-pvc") or (
            f"{pv.spec.claim_ref.namespace}/{pv.spec.claim_ref.name}"
            if pv.spec.claim_ref else ""
        )

        # Create a visible marker file inside the NAS directory before the K8s object is gone.
        # Note: the LG N2R1 NAS blocks renaming root-level share directories (ACCESS_DENIED),
        # so a marker file is the only automated way to tag orphaned NAS data.
        if nas_path and not ann.get("nas-admin/nas-dir-marked"):
            marker_suffix = "_" + "_".join(p for p in [_safe_name(sc), _safe_name(orig_pvc)] if p)
            _mark_nas_dir_deleted(nas_path, marker_suffix)

        v1.delete_persistent_volume(name=name)
    except ApiException as e:
        raise HTTPException(status_code=e.status, detail=str(e.reason))
    return HTMLResponse(content="", status_code=200)
