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
NAS_MOUNT = os.environ.get("NAS_MOUNT", "/mnt/nas")


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
        v1.delete_namespaced_persistent_volume_claim(name=name, namespace=namespace)
    except ApiException as e:
        raise HTTPException(status_code=e.status, detail=str(e.reason))
    return HTMLResponse(content="", status_code=200)
