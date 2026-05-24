"""
Local unit tests for nas-admin/main.py pure functions and NAS filesystem helpers.

Run with:  pytest apps/nas-admin/test_main.py -v
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

# Stub out kubernetes before main.py is imported (it calls _load_k8s() at module level)
_kube_stub = MagicMock()
_kube_stub.config.ConfigException = Exception
for _mod in (
    "kubernetes",
    "kubernetes.client",
    "kubernetes.config",
    "kubernetes.client.exceptions",
):
    sys.modules.setdefault(_mod, _kube_stub)

import main  # noqa: E402  (must come after the stub above)


# ---------------------------------------------------------------------------
# _safe_name
# ---------------------------------------------------------------------------

def test_safe_name_plain():
    assert main._safe_name("smb-nas") == "smb-nas"

def test_safe_name_slash():
    assert main._safe_name("default/my-pvc") == "default-my-pvc"

def test_safe_name_colon():
    assert main._safe_name("my:pvc") == "my-pvc"

def test_safe_name_space():
    assert main._safe_name("my pvc") == "my-pvc"

def test_safe_name_strips_leading_trailing_dash():
    assert main._safe_name("/leading/") == "leading"

def test_safe_name_combined():
    assert main._safe_name("a/b:c d") == "a-b-c-d"


# ---------------------------------------------------------------------------
# _age
# ---------------------------------------------------------------------------

def test_age_days():
    ts = datetime.now(timezone.utc) - timedelta(days=3, seconds=5)
    assert main._age(ts) == "3d"

def test_age_hours():
    ts = datetime.now(timezone.utc) - timedelta(seconds=7200)
    assert main._age(ts) == "2h"

def test_age_minutes():
    ts = datetime.now(timezone.utc) - timedelta(seconds=300)
    assert main._age(ts) == "5m"

def test_age_none():
    assert main._age(None) == "—"


# ---------------------------------------------------------------------------
# _fmt
# ---------------------------------------------------------------------------

def test_fmt_bytes():
    assert main._fmt(0) == "0 B"
    assert main._fmt(1023) == "1023 B"

def test_fmt_kb():
    assert main._fmt(1024) == "1 KB"

def test_fmt_mb():
    assert main._fmt(1024 ** 2) == "1 MB"

def test_fmt_gb():
    assert main._fmt(1024 ** 3) == "1 GB"

def test_fmt_tb():
    assert main._fmt(1024 ** 4) == "1.0 TB"


# ---------------------------------------------------------------------------
# _crumbs
# ---------------------------------------------------------------------------

def test_crumbs_root():
    c = main._crumbs("/")
    assert c == [{"name": "nas", "path": "/"}]

def test_crumbs_single_level():
    c = main._crumbs("/Torrent")
    assert len(c) == 2
    assert c[1] == {"name": "Torrent", "path": "/Torrent"}

def test_crumbs_nested():
    c = main._crumbs("/Torrent/my-pvc_default_smb-nas")
    assert len(c) == 3
    assert c[1] == {"name": "Torrent", "path": "/Torrent"}
    assert c[2] == {"name": "my-pvc_default_smb-nas", "path": "/Torrent/my-pvc_default_smb-nas"}


# ---------------------------------------------------------------------------
# _nas_local_path
# ---------------------------------------------------------------------------

def test_nas_local_path_happy(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    d = tmp_path / "Torrent" / "pvc-abc"
    d.mkdir(parents=True)
    assert main._nas_local_path("//nas/service/Torrent/pvc-abc") == d

def test_nas_local_path_trailing_slash_in_source(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service/")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    d = tmp_path / "Torrent" / "pvc-abc"
    d.mkdir(parents=True)
    assert main._nas_local_path("//nas/service/Torrent/pvc-abc") == d

def test_nas_local_path_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    assert main._nas_local_path("//nas/service/Torrent/nonexistent") is None

def test_nas_local_path_is_file_not_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    f = tmp_path / "somefile"
    f.write_text("x")
    assert main._nas_local_path("//nas/service/somefile") is None

def test_nas_local_path_wrong_source(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    assert main._nas_local_path("//other/share/Torrent/pvc-abc") is None

def test_nas_local_path_empty_nas_source(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    assert main._nas_local_path("//nas/service/Torrent/pvc-abc") is None

def test_nas_local_path_empty_nas_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", "")
    assert main._nas_local_path("//nas/service/Torrent/pvc-abc") is None

def test_nas_local_path_traversal_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    # Attempt to escape mount via ../..
    result = main._nas_local_path("//nas/service/../../etc/passwd")
    assert result is None


# ---------------------------------------------------------------------------
# _rename_nas_dir
# ---------------------------------------------------------------------------

def test_rename_nas_dir_happy(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    old = tmp_path / "Torrent" / "pvc-abc-uuid"
    old.mkdir(parents=True)
    (old / "data.bin").write_bytes(b"\x00" * 16)

    result = main._rename_nas_dir(
        "//nas/service/Torrent/pvc-abc-uuid",
        "my-pvc_default_smb-nas",
    )

    assert result == "//nas/service/Torrent/my-pvc_default_smb-nas"
    assert not old.exists()
    new = tmp_path / "Torrent" / "my-pvc_default_smb-nas"
    assert new.is_dir()
    assert (new / "data.bin").exists()

def test_rename_nas_dir_target_already_exists(tmp_path, monkeypatch):
    """If target name already exists (duplicate call), return the path without error."""
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    parent = tmp_path / "Torrent"
    parent.mkdir()
    (parent / "pvc-abc-uuid").mkdir()
    (parent / "my-pvc_default_smb-nas").mkdir()

    result = main._rename_nas_dir(
        "//nas/service/Torrent/pvc-abc-uuid",
        "my-pvc_default_smb-nas",
    )
    assert result == "//nas/service/Torrent/my-pvc_default_smb-nas"

def test_rename_nas_dir_source_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    result = main._rename_nas_dir("//nas/service/Torrent/nonexistent", "new-name")
    assert result is None

def test_rename_nas_dir_os_error(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    d = tmp_path / "Torrent" / "pvc-abc"
    d.mkdir(parents=True)

    original_rename = Path.rename

    def _fail_rename(self, target):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "rename", _fail_rename)

    result = main._rename_nas_dir("//nas/service/Torrent/pvc-abc", "new-name")
    assert result is None

    monkeypatch.setattr(Path, "rename", original_rename)


# ---------------------------------------------------------------------------
# _mark_nas_dir_deleted
# ---------------------------------------------------------------------------

def test_mark_nas_dir_deleted_creates_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    d = tmp_path / "kaniko-cache"
    d.mkdir()

    result = main._mark_nas_dir_deleted(
        "//nas/service/kaniko-cache", "_smb-nas_default_my-pvc"
    )

    marker = d / ".nas-admin-deleted_smb-nas_default_my-pvc"
    assert result == str(marker)
    assert marker.exists()
    assert marker.read_text() == "marked by nas-admin\n"

def test_mark_nas_dir_deleted_idempotent(tmp_path, monkeypatch):
    """Existing marker is not overwritten; path is returned."""
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    d = tmp_path / "kaniko-cache"
    d.mkdir()
    marker = d / ".nas-admin-deleted_smb-nas"
    marker.write_text("original content")

    result = main._mark_nas_dir_deleted("//nas/service/kaniko-cache", "_smb-nas")

    assert result == str(marker)
    assert marker.read_text() == "original content"

def test_mark_nas_dir_deleted_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    result = main._mark_nas_dir_deleted("//nas/service/no-such-dir", "_suffix")
    assert result is None

def test_mark_nas_dir_deleted_wrong_source(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))
    result = main._mark_nas_dir_deleted("//other/share/kaniko-cache", "_suffix")
    assert result is None


# ---------------------------------------------------------------------------
# Integration: full delete-PVC NAS side (rename path)
# ---------------------------------------------------------------------------

def test_full_pvc_delete_renames_uuid_dir(tmp_path, monkeypatch):
    """Simulate the NAS side of deleting a dynamic PVC:
    UUID subdir inside share → readable name.
    """
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))

    # Create the UUID directory that the CSI driver would have made
    uuid_dir = tmp_path / "Torrent" / "pvc-25e90319-3c12-4013-b0b4-9ceb1e83edde"
    uuid_dir.mkdir(parents=True)
    (uuid_dir / "appdata").mkdir()

    source = "//nas/service/Torrent"
    subdir = "pvc-25e90319-3c12-4013-b0b4-9ceb1e83edde"
    nas_path = source.rstrip("/") + "/" + subdir

    sc, ns, pvc_name = "smb-nas", "default", "my-app-pvc"
    readable_name = "_".join(
        filter(None, [main._safe_name(pvc_name), main._safe_name(ns), main._safe_name(sc)])
    )
    new_nas_path = main._rename_nas_dir(nas_path, readable_name)

    assert new_nas_path == "//nas/service/Torrent/my-app-pvc_default_smb-nas"
    assert not uuid_dir.exists()
    renamed = tmp_path / "Torrent" / "my-app-pvc_default_smb-nas"
    assert renamed.is_dir()
    assert (renamed / "appdata").is_dir()


def test_full_pvc_delete_marker_fallback_for_root_level(tmp_path, monkeypatch):
    """Root-level directories can't be renamed → marker file fallback."""
    monkeypatch.setattr(main, "NAS_SOURCE", "//nas/service")
    monkeypatch.setattr(main, "NAS_MOUNT", str(tmp_path))

    root_dir = tmp_path / "kaniko-cache"
    root_dir.mkdir()

    nas_path = "//nas/service/kaniko-cache"
    sc, ns, pvc_name = "smb-nas", "kaniko", "kaniko-cache"

    readable_name = "_".join(
        filter(None, [main._safe_name(pvc_name), main._safe_name(ns), main._safe_name(sc)])
    )

    # Simulate rename failure (root-level block) by making rename raise
    original_rename = Path.rename

    def _block(self, target):
        raise OSError("ACCESS_DENIED")

    monkeypatch.setattr(Path, "rename", _block)

    new_nas_path = main._rename_nas_dir(nas_path, readable_name)
    assert new_nas_path is None  # rename failed

    # Fall back to marker file
    suffix = "_" + "_".join(filter(None, [main._safe_name(sc), main._safe_name(ns), main._safe_name(pvc_name)]))
    marker_path = main._mark_nas_dir_deleted(nas_path, suffix)

    monkeypatch.setattr(Path, "rename", original_rename)

    assert marker_path is not None
    assert Path(marker_path).exists()
    assert root_dir.is_dir()  # directory itself is untouched
