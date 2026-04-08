#!/usr/bin/env python3
"""
cluster-advisor MCP server

Reads node survey data (survey/*.json) and skill files (skills/*)
to answer cluster planning questions:
  - What hardware do I have?
  - What K3s cluster can I build?
  - Which nodes should be control-plane vs workers?
  - What make targets should I run?

Tools:
  list_nodes()              — table of all surveyed nodes with key metrics
  node_profile(hostname)    — detailed profile + capability classification
  analyze_cluster()         — full recommendation: flavors, assignments, warnings, command
  get_skill(name)           — read a skill file (cilium, ai, survey, onboarding, etc.)
  list_skills()             — list available skills
"""

import json
import glob
import sys
from pathlib import Path
from typing import Optional

import yaml
from fastmcp import FastMCP

# ── Paths ─────────────────────────────────────────────────────────────────────
# This file lives at mcp/cluster-advisor/server.py → repo root is 2 levels up
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SURVEY_DIR = REPO_ROOT / "survey"
SKILLS_DIR = REPO_ROOT / "skills"
FLAVORS_FILE = Path(__file__).resolve().parent / "flavors.yaml"

# ── MCP server ────────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="cluster-advisor",
    instructions="""
K3s cluster planning assistant for the infra-ai/infra repo.

Reads hardware survey data and recommends K3s cluster configurations.

Primary workflow:
  1. analyze_cluster()  — get a full recommendation based on surveyed hardware
  2. list_nodes()       — see all nodes and their key specs
  3. node_profile(name) — deep-dive on a specific node
  4. get_skill(name)    — read technical docs for any component

Survey data lives in: survey/<hostname>.json
Run the survey with: make survey
""",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_surveys() -> dict[str, dict]:
    """Load all survey JSON files. Returns {hostname: data}."""
    results = {}
    for path in sorted(SURVEY_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            results[data.get("hostname", path.stem)] = data
        except Exception as e:
            results[path.stem] = {"_error": str(e)}
    return results


def _load_flavors() -> dict:
    """Load cluster flavor definitions from flavors.yaml."""
    return yaml.safe_load(FLAVORS_FILE.read_text()).get("flavors", {})


def _node_capabilities(node: dict) -> dict:
    """Derive capability flags from survey data."""
    ram_gb = float(node.get("ram", {}).get("total_gb", 0) or 0)
    cgroups = node.get("k8s_readiness", {}).get("cgroups_version", "")
    ebpf = node.get("k8s_readiness", {}).get("ebpf_capable", False)
    latency_str = node.get("storage", {}).get("write_latency", "")
    is_nat = node.get("network", {}).get("is_nat", True)
    gpu_npu = node.get("gpu_npu", [])
    netstore = node.get("net_storage", [])
    warnings = node.get("warnings", [])

    # Parse write latency — e.g. "2.44 ms/op" or "0.12 ms/op"
    write_latency_ms = None
    if latency_str:
        try:
            write_latency_ms = float(latency_str.split()[0])
        except (ValueError, IndexError):
            pass

    has_nvme = any(
        "nvme" in str(d).lower()
        for d in node.get("storage", {}).get("devices", [])
    )

    # Distinguish real NPU/GPU from integrated display-only GPU
    gpu_str = " ".join(str(g) for g in gpu_npu).lower()
    has_npu = "rknpu" in gpu_str or "npu" in gpu_str
    has_gpu_npu = bool(gpu_npu)  # any GPU/NPU (includes VideoCore, DRM)

    # SMB/NAS reachable from this node
    has_smb_nas = any("SMB" in str(s) or "smb" in str(s).lower() or "445" in str(s)
                      for s in netstore)

    return {
        "ram_gb": ram_gb,
        "cgroups_v2": cgroups == "v2",
        "ebpf": bool(ebpf),
        "write_latency_ms": write_latency_ms,
        "has_nvme": has_nvme,
        "has_gpu_npu": has_gpu_npu,
        "has_npu": has_npu,
        "has_smb_nas": has_smb_nas,
        "is_nat": is_nat,
        "has_warnings": bool(warnings),
        "warnings": warnings,
        # Role suitability
        "etcd_capable": (
            cgroups == "v2"
            and bool(ebpf)
            and ram_gb >= 4
            and (write_latency_ms is not None and write_latency_ms < 10)
        ),
        "control_plane_capable": (
            cgroups == "v2"
            and bool(ebpf)
            and ram_gb >= 4
        ),
        "worker_capable": (
            cgroups == "v2"
            and ram_gb >= 2
        ),
        "ai_worker_capable": (
            ram_gb >= 16
            and cgroups == "v2"
        ),
    }


def _etcd_score(caps: dict) -> float:
    """Score a node for etcd suitability (higher = better)."""
    score = 0.0
    if caps["cgroups_v2"]:
        score += 10
    if caps["ebpf"]:
        score += 5
    if caps["has_nvme"]:
        score += 20
    if caps["write_latency_ms"] is not None:
        # Lower is better — invert and scale
        score += max(0, 20 - caps["write_latency_ms"] * 2)
    score += min(caps["ram_gb"] / 4, 5)  # cap at 5 points for RAM
    return score


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_nodes() -> str:
    """
    List all surveyed nodes with key hardware metrics.
    Shows RAM, storage type, write latency, GPU/NPU, and K8s readiness.
    Run 'make survey' first if no nodes appear.
    """
    nodes = _load_surveys()
    if not nodes:
        return (
            "No survey data found in survey/\n"
            "Run: make survey"
        )

    lines = [
        f"{'Node':<30} {'RAM':>6} {'Storage':<10} {'Latency':>10} {'GPU/NPU':<8} {'cgroupsv2':>10} {'Warnings':>8}",
        "-" * 90,
    ]

    for hostname, data in sorted(nodes.items()):
        if "_error" in data:
            lines.append(f"{hostname:<30}  ERROR: {data['_error']}")
            continue

        caps = _node_capabilities(data)
        ram = f"{caps['ram_gb']:.0f}GB"
        storage = "NVMe" if caps["has_nvme"] else "eMMC/SD"
        latency = (
            f"{caps['write_latency_ms']:.1f}ms"
            if caps["write_latency_ms"] is not None
            else "?"
        )
        gpu = "yes" if caps["has_gpu_npu"] else "-"
        cg = "yes" if caps["cgroups_v2"] else "NO"
        warn = str(len(caps["warnings"])) if caps["warnings"] else "-"

        lines.append(
            f"{hostname:<30} {ram:>6} {storage:<10} {latency:>10} {gpu:<8} {cg:>10} {warn:>8}"
        )

    lines.append("")
    lines.append(f"Total: {len(nodes)} nodes surveyed")
    lines.append("Use node_profile(hostname) for details on any node.")
    lines.append("Use analyze_cluster() for a full recommendation.")
    return "\n".join(lines)


@mcp.tool()
def node_profile(hostname: str) -> str:
    """
    Get a detailed profile and capability classification for a specific node.
    Shows hardware specs, K8s readiness, warnings, and recommended role.
    """
    nodes = _load_surveys()
    if hostname not in nodes:
        available = ", ".join(sorted(nodes.keys())) or "none (run make survey)"
        return f"Node '{hostname}' not found.\nAvailable: {available}"

    data = nodes[hostname]
    caps = _node_capabilities(data)

    lines = [f"═══ {hostname} ═══", ""]

    # Hardware
    lines += [
        "Hardware:",
        f"  Board      : {data.get('board', '?')}",
        f"  OS         : {data.get('os', '?')} | kernel {data.get('kernel', '?')}",
        f"  Arch       : {data.get('arch', '?')}",
        f"  Uptime     : {data.get('uptime', '?')}",
        "",
        f"  CPU        : {data.get('cpu', {}).get('model', '?')} | "
        f"{data.get('cpu', {}).get('cores', '?')} cores",
    ]
    for mhz in data.get("cpu", {}).get("mhz_per_cluster", []):
        lines.append(f"               {mhz}")

    ram = data.get("ram", {})
    swap = data.get("swap", {})
    lines += [
        "",
        f"  RAM        : {ram.get('stats', '?')} | type: {ram.get('type', 'unknown')}",
        f"  Swap       : {'ENABLED ⚠' if swap.get('enabled') else 'disabled ✓'}",
        "",
        "  Storage    :",
    ]
    for dev in data.get("storage", {}).get("devices", []):
        lines.append(f"    {dev}")
    lines += [
        f"    write latency: {data.get('storage', {}).get('write_latency', '?')}",
        f"    root:          {data.get('storage', {}).get('root_df', '?')}",
        f"    /var/lib:      {data.get('storage', {}).get('varlib_df', '?')}",
    ]

    # GPU/NPU
    gpu_list = data.get("gpu_npu", [])
    lines += [
        "",
        f"  GPU/NPU    : {', '.join(gpu_list) if gpu_list else 'none'}",
    ]

    # Network
    net = data.get("network", {})
    lines += [
        "",
        "  Network    :",
    ]
    for iface in net.get("interfaces", []):
        lines.append(f"    {iface}")
    lines += [
        f"    local IPs: {', '.join(net.get('local_ips', []))}",
        f"    gateway:   {net.get('gateway', '?')}",
        f"    public:    {net.get('public_ip', '?')} | {net.get('nat_status', '?')}",
    ]

    # K8s readiness
    k8s = data.get("k8s_readiness", {})
    lines += [
        "",
        "K8s Readiness:",
        f"  cgroups    : {k8s.get('cgroups_version', '?')} {'✓' if caps['cgroups_v2'] else '⚠ needs v2'}",
        f"  eBPF       : {'✓' if caps['ebpf'] else '⚠ not supported'}",
        f"  ip_forward : {k8s.get('ip_forward', '?')}",
        f"  runtime    : {k8s.get('container_runtime', 'none') or 'none'}",
        f"  k3s        : {k8s.get('k3s_installed', 'not installed') or 'not installed'}",
        "  modules    :",
    ]
    for mod in k8s.get("modules", []):
        lines.append(f"    {mod}")

    # Role classification
    lines += ["", "Role Classification:"]
    roles = []
    if caps["etcd_capable"]:
        score = _etcd_score(caps)
        roles.append(f"  ✓ control-plane (etcd score: {score:.0f}/50)")
    elif caps["control_plane_capable"]:
        lat = caps["write_latency_ms"]
        note = f" — write latency {lat:.1f}ms may be slow for etcd" if lat and lat >= 5 else ""
        roles.append(f"  ✓ control-plane{note}")
    if caps["ai_worker_capable"]:
        roles.append("  ✓ ai-worker (GPU/NPU detected, ≥16GB RAM)")
    elif caps["has_gpu_npu"]:
        roles.append(f"  ~ ai-worker (GPU/NPU present but only {caps['ram_gb']:.0f}GB RAM — needs ≥16GB)")
    if caps["worker_capable"]:
        roles.append("  ✓ general worker")
    if not caps["cgroups_v2"]:
        roles.append("  ✗ NOT K8s-ready: cgroups v2 required")
    lines += roles if roles else ["  ? not yet classified"]

    # Warnings
    warnings = caps["warnings"]
    if warnings:
        lines += ["", "⚠ Warnings (fix before bootstrap):"]
        for w in warnings:
            lines.append(f"  - {w}")
    else:
        lines += ["", "✓ No warnings"]

    return "\n".join(lines)


@mcp.tool()
def analyze_cluster() -> str:
    """
    Analyze all surveyed nodes and recommend K3s cluster configuration.

    Returns:
    - Summary of available hardware
    - Which cluster flavors are achievable (with reasons)
    - Recommended configuration with node role assignments
    - Pre-flight warnings that must be fixed before bootstrap
    - Exact make command to run
    """
    nodes = _load_surveys()
    if not nodes:
        return (
            "No survey data found.\n"
            "Run: make survey\n"
            "Then re-run this analysis."
        )

    flavors = _load_flavors()

    # ── Compute capabilities per node ─────────────────────────────────────────
    caps_map = {h: _node_capabilities(d) for h, d in nodes.items() if "_error" not in d}

    # ── Cluster-level aggregates ───────────────────────────────────────────────
    total_nodes = len(caps_map)
    total_ram = sum(c["ram_gb"] for c in caps_map.values())
    etcd_nodes = {h: c for h, c in caps_map.items() if c["etcd_capable"]}
    cp_nodes = {h: c for h, c in caps_map.items() if c["control_plane_capable"]}
    worker_nodes = {h: c for h, c in caps_map.items() if c["worker_capable"]}
    ai_nodes = {h: c for h, c in caps_map.items() if c["ai_worker_capable"]}
    direct_ip_nodes = {h: c for h, c in caps_map.items() if not c["is_nat"]}
    all_warnings = [(h, w) for h, c in caps_map.items() for w in c["warnings"]]

    # ── Flavor evaluation ─────────────────────────────────────────────────────
    achievable = []
    not_achievable = []

    for fname, fdef in flavors.items():
        req = fdef.get("requires", {})
        reasons_ok = []
        reasons_fail = []

        if "min_server_nodes" in req:
            n = req["min_server_nodes"]
            if len(cp_nodes) >= n:
                reasons_ok.append(f"{len(cp_nodes)} control-plane capable nodes (need {n})")
            else:
                reasons_fail.append(f"need {n} control-plane capable nodes, have {len(cp_nodes)}")

        if "min_nodes" in req:
            n = req["min_nodes"]
            if total_nodes >= n:
                reasons_ok.append(f"{total_nodes} nodes total (need {n})")
            else:
                reasons_fail.append(f"need {n} nodes, have {total_nodes}")

        if "min_server_ram_gb" in req:
            r = req["min_server_ram_gb"]
            best = max((c["ram_gb"] for c in cp_nodes.values()), default=0)
            if best >= r:
                reasons_ok.append(f"best server node has {best:.0f}GB RAM (need {r}GB)")
            else:
                reasons_fail.append(f"need {r}GB RAM on server node, best is {best:.0f}GB")

        if req.get("cgroups_v2"):
            ok = [h for h, c in caps_map.items() if c["cgroups_v2"]]
            if ok:
                reasons_ok.append(f"cgroups v2: {len(ok)}/{total_nodes} nodes")
            else:
                reasons_fail.append("no nodes have cgroups v2 (required)")

        if req.get("ebpf"):
            ok = [h for h, c in caps_map.items() if c["ebpf"]]
            if ok:
                reasons_ok.append(f"eBPF capable: {len(ok)}/{total_nodes} nodes")
            else:
                reasons_fail.append("no nodes have eBPF support (required for Cilium)")

        if "max_server_write_latency_ms" in req:
            limit = req["max_server_write_latency_ms"]
            fast = {h: c for h, c in cp_nodes.items()
                    if c["write_latency_ms"] is not None and c["write_latency_ms"] < limit}
            needed = req.get("min_server_nodes", 1)
            if len(fast) >= needed:
                fast_summary = ", ".join(
                    f"{h}: {c['write_latency_ms']:.1f}ms" for h, c in list(fast.items())[:3]
                )
                reasons_ok.append(
                    f"{len(fast)} nodes with write latency <{limit}ms ({fast_summary})"
                )
            else:
                slow = {h: c for h, c in cp_nodes.items()
                        if c["write_latency_ms"] is None or c["write_latency_ms"] >= limit}
                reasons_fail.append(
                    f"need {needed} nodes with write latency <{limit}ms, "
                    f"only {len(fast)} qualify. Slow nodes: "
                    + ", ".join(f"{h}: {c['write_latency_ms']}ms" for h, c in list(slow.items())[:3])
                )

        if "min_cluster_ram_gb" in req:
            r = req["min_cluster_ram_gb"]
            if total_ram >= r:
                reasons_ok.append(f"total cluster RAM: {total_ram:.0f}GB (need {r}GB)")
            else:
                reasons_fail.append(f"need {r}GB total cluster RAM, have {total_ram:.0f}GB")

        if req.get("has_gpu_npu"):
            if ai_nodes:
                reasons_ok.append(
                    f"GPU/NPU nodes: {', '.join(ai_nodes.keys())}"
                )
            else:
                reasons_fail.append("no nodes with GPU/NPU and ≥16GB RAM")

        if "min_ai_node_ram_gb" in req:
            r = req["min_ai_node_ram_gb"]
            gpu_nodes = {h: c for h, c in caps_map.items() if c["has_gpu_npu"]}
            enough = {h: c for h, c in gpu_nodes.items() if c["ram_gb"] >= r}
            if enough:
                reasons_ok.append(
                    f"AI-capable nodes: "
                    + ", ".join(f"{h} ({c['ram_gb']:.0f}GB)" for h, c in enough.items())
                )
            elif gpu_nodes:
                reasons_fail.append(
                    f"GPU/NPU nodes exist but need {r}GB RAM: "
                    + ", ".join(f"{h} has {c['ram_gb']:.0f}GB" for h, c in gpu_nodes.items())
                )

        if req.get("requires_direct_ip"):
            if direct_ip_nodes:
                reasons_ok.append(f"direct IP nodes: {', '.join(direct_ip_nodes.keys())}")
            else:
                reasons_fail.append("all nodes are behind NAT (no direct public IP)")

        if req.get("has_smb_nas"):
            smb_nodes = {h: c for h, c in caps_map.items() if c["has_smb_nas"]}
            if smb_nodes:
                reasons_ok.append(f"SMB/NAS reachable from {len(smb_nodes)} nodes")
            else:
                reasons_fail.append("no nodes can reach SMB/NAS (port 445 not accessible)")

        if req.get("has_npu"):
            npu_nodes = {h: c for h, c in caps_map.items() if c["has_npu"]}
            if npu_nodes:
                reasons_ok.append(f"NPU nodes: {', '.join(npu_nodes.keys())}")
            else:
                reasons_fail.append("no nodes with NPU (/dev/rknpu0) detected")

        if reasons_fail:
            not_achievable.append((fname, fdef, reasons_ok, reasons_fail))
        else:
            achievable.append((fname, fdef, reasons_ok))

    # ── Build recommended configuration ───────────────────────────────────────
    # Pick the best achievable non-additive flavor
    base_priority = ["full-ha", "standard", "minimal", "edge"]
    chosen_base = None
    for name in base_priority:
        if any(f[0] == name for f in achievable):
            chosen_base = name
            break

    # Pick additive flavors (observability, ai) if achievable
    additive_achievable = [
        f for f in achievable
        if f[0] in ("observability", "ai")
    ]

    # ── Node assignment for recommended config ─────────────────────────────────
    assignment = {}
    if chosen_base:
        # Sort cp candidates by etcd score descending
        cp_sorted = sorted(cp_nodes.items(), key=lambda x: _etcd_score(x[1]), reverse=True)

        if chosen_base == "full-ha":
            server_count = 3
        else:
            server_count = 1

        server_nodes_assigned = [h for h, _ in cp_sorted[:server_count]]
        remaining = [h for h in caps_map if h not in server_nodes_assigned]

        for h in server_nodes_assigned:
            assignment[h] = "K3s server (control-plane)"

        # AI workers first
        for h in list(remaining):
            if caps_map[h]["ai_worker_capable"] and h not in assignment:
                assignment[h] = "K3s agent (ai-worker — GPU/NPU)"
                remaining.remove(h)

        # General workers
        for h in remaining:
            if caps_map[h]["worker_capable"]:
                assignment[h] = "K3s agent (worker)"
            else:
                assignment[h] = "standalone (not K8s ready)"

    # ── Build make command ────────────────────────────────────────────────────
    make_parts = []
    if chosen_base:
        chosen_def = next(f for f in achievable if f[0] == chosen_base)
        make_parts.append(chosen_def[1].get("make_command", f"make {chosen_base}"))
    for name, fdef, _ in additive_achievable:
        make_parts.append(fdef.get("make_command", f"make {name}"))

    # ── Format output ─────────────────────────────────────────────────────────
    out = ["═══ Cluster Analysis ═══", ""]

    # Hardware summary
    out += [
        f"Nodes surveyed : {total_nodes}",
        f"Total RAM      : {total_ram:.0f}GB",
        f"Control-plane  : {len(cp_nodes)} capable nodes",
        f"Etcd (fast)    : {len(etcd_nodes)} nodes (write latency <10ms + cgroups v2 + eBPF)",
        f"AI workers     : {len(ai_nodes)} nodes (GPU/NPU + ≥16GB RAM)",
        f"Direct IP      : {len(direct_ip_nodes)} nodes (no NAT)",
        "",
    ]

    # Achievable flavors
    out.append("Achievable Flavors:")
    if achievable:
        for fname, fdef, reasons in achievable:
            note = f" [{fdef.get('note', '')}]" if fdef.get("note") else ""
            out.append(f"  ✓ {fname}{note} — {fdef['description']}")
    else:
        out.append("  ✗ None — check warnings below")
    out.append("")

    # Not achievable
    if not_achievable:
        out.append("Not Achievable (missing requirements):")
        for fname, fdef, _, reasons_fail in not_achievable:
            out.append(f"  ✗ {fname}: {'; '.join(reasons_fail)}")
        out.append("")

    # Recommendation
    if chosen_base:
        out.append("═══ Recommended Configuration ═══")
        out.append("")
        additive_names = [f[0] for f in additive_achievable]
        full_label = chosen_base
        if additive_names:
            full_label += " + " + " + ".join(additive_names)
        out.append(f"Flavor: {full_label}")
        out.append("")

        out.append("Node Assignments:")
        for h, role in sorted(assignment.items()):
            c = caps_map[h]
            lat = f"{c['write_latency_ms']:.1f}ms" if c["write_latency_ms"] else "?"
            out.append(
                f"  {h:<32} → {role}"
                + (f"  [{c['ram_gb']:.0f}GB | {'NVMe' if c['has_nvme'] else 'eMMC'} | {lat}]")
            )
        out.append("")

        if make_parts:
            out.append("Commands to run (in order):")
            for cmd in make_parts:
                out.append(f"  {cmd}")
        out.append("")
    else:
        out.append("Cannot recommend a cluster — see warnings below.")
        out.append("")

    # Pre-flight warnings
    if all_warnings:
        out.append(f"⚠ Pre-flight Warnings ({len(all_warnings)} issues):")
        for hostname, warning in all_warnings:
            out.append(f"  [{hostname}] {warning}")
        out.append("")
        out.append("Fix warnings before running bootstrap.")
        out.append("Re-run: make survey  →  re-run this analysis")
    else:
        out.append("✓ No pre-flight warnings — ready to bootstrap")

    return "\n".join(out)


@mcp.tool()
def cluster_power_score() -> str:
    """
    Score the cluster across 5 dimensions and produce an overall power rating.

    Dimensions:
      Compute   — total CPU cores, RAM, NVMe nodes
      Memory    — total cluster RAM, largest single-node RAM
      Storage   — NVMe count, write latency, NAS availability
      Network   — inter-node latency, bandwidth, direct IP
      AI/ML     — NPU nodes, AI-capable RAM, GPU presence

    Returns a score card with tier (S/A/B/C/D) and what's holding the cluster back.
    """
    nodes = _load_surveys()
    if not nodes:
        return "No survey data found.\nRun: make survey"

    caps_map = {h: _node_capabilities(d) for h, d in nodes.items() if "_error" not in d}
    all_nodes = list(caps_map.values())
    node_count = len(all_nodes)

    # ── Compute ────────────────────────────────────────────────────────────────
    total_cores = sum(int(d.get("cpu", {}).get("cores", 0) or 0)
                      for d in nodes.values() if "_error" not in d)
    total_ram = sum(c["ram_gb"] for c in all_nodes)
    nvme_count = sum(1 for c in all_nodes if c["has_nvme"])
    etcd_count = sum(1 for c in all_nodes if c["etcd_capable"])
    cp_count = sum(1 for c in all_nodes if c["control_plane_capable"])

    compute_score = min(100, int(
        (min(node_count / 10, 1) * 30)           # up to 30pts: node count (10 = full)
        + (min(total_cores / 80, 1) * 30)         # up to 30pts: cores (80 = full)
        + (min(cp_count / 5, 1) * 20)             # up to 20pts: CP-capable nodes
        + (min(etcd_count / 3, 1) * 20)           # up to 20pts: etcd-capable nodes
    ))

    # ── Memory ────────────────────────────────────────────────────────────────
    max_node_ram = max((c["ram_gb"] for c in all_nodes), default=0)
    memory_score = min(100, int(
        (min(total_ram / 200, 1) * 50)            # up to 50pts: total cluster RAM (200GB = full)
        + (min(max_node_ram / 32, 1) * 30)        # up to 30pts: largest node (32GB = full)
        + (min(node_count / 8, 1) * 20)           # up to 20pts: nodes with RAM
    ))

    # ── Storage ────────────────────────────────────────────────────────────────
    latencies = [c["write_latency_ms"] for c in all_nodes if c["write_latency_ms"] is not None]
    avg_latency = sum(latencies) / len(latencies) if latencies else 999
    best_latency = min(latencies) if latencies else 999
    has_nas = any(c["has_smb_nas"] for c in all_nodes)

    storage_score = min(100, int(
        (min(nvme_count / node_count, 1) * 40)     # up to 40pts: NVMe ratio
        + (max(0, 1 - avg_latency / 10) * 30)      # up to 30pts: avg latency (<1ms = full)
        + (20 if has_nas else 0)                    # 20pts: NAS available
        + (10 if best_latency < 1 else 5 if best_latency < 5 else 0)  # bonus: fast nodes
    ))

    # ── Network ────────────────────────────────────────────────────────────────
    all_latencies = []
    for d in nodes.values():
        if "_error" in d:
            continue
        for entry in d.get("network", {}).get("inter_node_latency", []):
            # "192.168.178.85: 0.446/0.523/0.595/0.067 ms" — extract avg (2nd field)
            try:
                avg_ms = float(entry.split(": ")[1].split("/")[1])
                all_latencies.append(avg_ms)
            except (IndexError, ValueError):
                pass

    avg_inter_node = sum(all_latencies) / len(all_latencies) if all_latencies else 999
    gbe_count = sum(
        1 for d in nodes.values() if "_error" not in d
        for iface in d.get("network", {}).get("interfaces", [])
        if "1000Mbps" in str(iface) and "veth" not in str(iface)
    )
    direct_ip_count = sum(1 for c in all_nodes if not c["is_nat"])

    network_score = min(100, int(
        (max(0, 1 - avg_inter_node / 5) * 40)      # up to 40pts: inter-node latency (<0.5ms = full)
        + (min(gbe_count / node_count, 1) * 30)    # up to 30pts: GbE ratio
        + (20 if avg_inter_node < 2 else 10)        # 20/10pts: latency tier
        + (10 if direct_ip_count > 0 else 0)        # 10pts: direct IP node
    ))

    # ── AI/ML ──────────────────────────────────────────────────────────────────
    npu_count = sum(1 for c in all_nodes if c["has_npu"])
    ai_capable = sum(1 for c in all_nodes if c["ai_worker_capable"])
    ai_ram = sum(c["ram_gb"] for c in all_nodes if c["ai_worker_capable"])

    ai_score = min(100, int(
        (min(ai_ram / 128, 1) * 40)                # up to 40pts: AI-capable RAM (128GB = full)
        + (min(npu_count / 4, 1) * 30)             # up to 30pts: NPU nodes
        + (min(ai_capable / node_count, 1) * 20)   # up to 20pts: AI-capable ratio
        + (10 if ai_capable >= 1 else 0)            # 10pts: at least 1 AI node
    ))

    # ── Overall ────────────────────────────────────────────────────────────────
    weights = {"Compute": 0.25, "Memory": 0.25, "Storage": 0.20, "Network": 0.15, "AI/ML": 0.15}
    scores = {
        "Compute": compute_score,
        "Memory": memory_score,
        "Storage": storage_score,
        "Network": network_score,
        "AI/ML": ai_score,
    }
    overall = int(sum(scores[k] * weights[k] for k in scores))

    def tier(s: int) -> str:
        if s >= 90: return "S"
        if s >= 75: return "A"
        if s >= 55: return "B"
        if s >= 35: return "C"
        return "D"

    # ── Format ─────────────────────────────────────────────────────────────────
    out = ["═══ Cluster Power Score ═══", ""]
    out.append(f"  Overall:  {overall:3d}/100  [{tier(overall)}]")
    out.append("")
    out.append("  Dimension     Score  Tier  Weight")
    out.append("  " + "─" * 40)
    for dim, score in scores.items():
        w = int(weights[dim] * 100)
        out.append(f"  {dim:<14} {score:3d}/100  [{tier(score)}]  {w}%")

    out += ["", "─" * 50, ""]

    # Hardware summary
    out += [
        f"  Nodes        : {node_count} (cores: {total_cores})",
        f"  Total RAM    : {total_ram:.0f} GB  (max node: {max_node_ram:.0f} GB)",
        f"  NVMe         : {nvme_count}/{node_count} nodes",
        f"  Avg latency  : {avg_latency:.1f} ms/op  (best: {best_latency:.2f} ms/op)",
        f"  NAS (SMB)    : {'yes' if has_nas else 'no — run make storage to configure'}",
        f"  Etcd-capable : {etcd_count}/{node_count} nodes",
        f"  NPU nodes    : {npu_count} (Rockchip RK3588S — /dev/rknpu0)",
        f"  AI RAM       : {ai_ram:.0f} GB across {ai_capable} nodes",
        f"  Inter-node   : avg {avg_inter_node:.2f} ms",
    ]

    out += ["", "─" * 50, ""]

    # What roles this cluster can run
    out.append("  Deployable Stack:")
    role_checks = [
        (overall >= 40,  "K3s cluster (core + networking)"),
        (compute_score >= 40, "Standard services (ingress + DNS + GitOps)"),
        (storage_score >= 50 or has_nas, "Persistent storage (SMB/NAS PVCs)"),
        (memory_score >= 50, "Observability (Prometheus + Grafana + Loki + Tempo)"),
        (memory_score >= 40, "Security (NeuVector runtime protection)"),
        (memory_score >= 60, "AI stack (LiteLLM + Hermes + HolmesGPT + kagent)"),
        (npu_count >= 1, "Local AI inference (NPU — future: Ollama/RKNN)"),
        (etcd_count >= 3, "HA control-plane (3 etcd nodes)"),
    ]
    for ok, label in role_checks:
        out.append(f"  {'  ✓' if ok else '  ✗'} {label}")

    out += ["", "─" * 50, ""]

    # Bottlenecks
    bottlenecks = []
    slow_nodes = [(h, c["write_latency_ms"]) for h, c in caps_map.items()
                  if c["write_latency_ms"] is not None and c["write_latency_ms"] > 50]
    if slow_nodes:
        for h, lat in slow_nodes:
            bottlenecks.append(f"  ⚠ {h}: write latency {lat:.0f}ms — too slow for etcd")
    if not has_nas:
        bottlenecks.append("  ⚠ NAS not detected — run 'make storage' to enable PVC-backed workloads")
    if npu_count == 0:
        bottlenecks.append("  ⚠ No NPU detected — local AI inference not available")
    all_warnings = [(h, w) for h, c in caps_map.items() for w in c["warnings"]]
    if all_warnings:
        bottlenecks.append(f"  ⚠ {len(all_warnings)} pre-flight warnings — run node_profile(hostname) for details")

    if bottlenecks:
        out.append("  Bottlenecks:")
        out += bottlenecks
    else:
        out.append("  ✓ No significant bottlenecks")

    out += [""]
    return "\n".join(out)


@mcp.tool()
def cluster_roadmap() -> str:
    """
    Generate a phased deployment roadmap based on surveyed hardware.

    Classifies the cluster into a hardware profile (nano/micro/small/medium/large/gpu),
    then produces an ordered list of deployment phases:
      - Phases achievable NOW (with exact make commands)
      - Phases blocked (with what's missing)
      - "To unlock next tier" recommendations (exact hardware/config to add)

    Works for any cluster — from a single Pi to a 20-node datacenter.
    The goal is to maximize what you can learn/run at each stage.
    """
    nodes = _load_surveys()
    if not nodes:
        return "No survey data found.\nRun: make survey"

    caps_map = {h: _node_capabilities(d) for h, d in nodes.items() if "_error" not in d}
    all_caps = list(caps_map.values())

    # ── Cluster-level metrics ─────────────────────────────────────────────────
    node_count = len(all_caps)
    total_ram = sum(c["ram_gb"] for c in all_caps)
    max_node_ram = max((c["ram_gb"] for c in all_caps), default=0)
    best_server_ram = max(
        (c["ram_gb"] for c in all_caps if c["control_plane_capable"]), default=0
    )
    etcd_capable_count = sum(1 for c in all_caps if c["etcd_capable"])
    cp_capable_count = sum(1 for c in all_caps if c["control_plane_capable"])
    has_cgroups_v2 = any(c["cgroups_v2"] for c in all_caps)
    has_ebpf = any(c["ebpf"] for c in all_caps)
    has_npu = any(c["has_npu"] for c in all_caps)
    has_smb_nas = any(c["has_smb_nas"] for c in all_caps)
    npu_count = sum(1 for c in all_caps if c["has_npu"])
    max_ai_node_ram = max(
        (c["ram_gb"] for c in all_caps if c["has_npu"]), default=0
    )

    # ── Hardware profile classification ──────────────────────────────────────
    # Assign based on most capable profile that fits
    if node_count >= 8 and max_node_ram >= 16:
        hw_profile = "large"
        hw_desc = f"{node_count} nodes, {max_node_ram:.0f}GB max/node — full stack possible"
    elif node_count >= 5 and max_node_ram >= 8:
        hw_profile = "medium"
        hw_desc = f"{node_count} nodes, {max_node_ram:.0f}GB max/node — full stack with AI"
    elif node_count >= 3 and max_node_ram >= 4:
        hw_profile = "small"
        hw_desc = f"{node_count} nodes, {max_node_ram:.0f}GB max/node — HA + observability"
    elif max_node_ram >= 4:
        hw_profile = "micro"
        hw_desc = f"{node_count} node(s), {max_node_ram:.0f}GB — single-node full stack"
    elif max_node_ram >= 2:
        hw_profile = "nano"
        hw_desc = f"{node_count} node(s), {max_node_ram:.0f}GB — K8s basics only"
    else:
        hw_profile = "nano"
        hw_desc = f"{node_count} node(s), {max_node_ram:.0f}GB — very limited"

    if has_npu:
        hw_profile = f"{hw_profile}+gpu"
        hw_desc += f" | {npu_count} NPU node(s) — local inference possible"

    # ── Tier definitions ─────────────────────────────────────────────────────
    # Each tier: (name, description, check_fn → (ok, gap), make_cmd, what_you_learn)
    def check_tier1():
        if not has_cgroups_v2:
            return False, "cgroups v2 required — enable in /boot/cmdline.txt or kernel params"
        if node_count < 1:
            return False, "need at least 1 node"
        if max_node_ram < 2:
            return False, f"need 2GB RAM minimum (have {max_node_ram:.0f}GB)"
        return True, None

    def check_tier2():
        ok1, gap1 = check_tier1()
        if not ok1:
            return False, f"Tier 1 blocked: {gap1}"
        if not has_ebpf:
            return False, "eBPF support required (kernel ≥5.4) — needed for Cilium"
        if best_server_ram < 4:
            return False, f"need 4GB RAM on server node (have {best_server_ram:.0f}GB)"
        return True, None

    def check_tier3():
        ok2, gap2 = check_tier2()
        if not ok2:
            return False, f"Tier 2 blocked: {gap2}"
        if total_ram < 12:
            delta = 12 - total_ram
            return False, f"need 12GB total cluster RAM (have {total_ram:.0f}GB, need +{delta:.0f}GB)"
        return True, None

    def check_tier4():
        ok2, gap2 = check_tier2()
        if not ok2:
            return False, f"Tier 2 blocked: {gap2}"
        if total_ram < 24:
            delta = 24 - total_ram
            return False, f"need 24GB total cluster RAM (have {total_ram:.0f}GB, need +{delta:.0f}GB)"
        if best_server_ram < 4:
            return False, f"need 4GB RAM on server node (have {best_server_ram:.0f}GB)"
        return True, None

    def check_tier5():
        ok2, gap2 = check_tier2()
        if not ok2:
            return False, f"Tier 2 blocked: {gap2}"
        if total_ram < 8:
            return False, f"need 8GB total cluster RAM (have {total_ram:.0f}GB)"
        if best_server_ram < 4:
            return False, f"need 4GB RAM on server node (have {best_server_ram:.0f}GB)"
        return True, None

    def check_tier6():
        ok2, gap2 = check_tier2()
        if not ok2:
            return False, f"Tier 2 blocked: {gap2}"
        if etcd_capable_count < 3:
            delta = 3 - etcd_capable_count
            return False, (
                f"need 3 etcd-capable nodes (have {etcd_capable_count}). "
                f"Need {delta} more node(s) with: cgroups v2, eBPF, 4GB RAM, <10ms write latency"
            )
        return True, None

    def check_tier7():
        if not has_npu:
            return False, "no NPU detected (/dev/rknpu0 required for Rockchip, or GPU for others)"
        if max_ai_node_ram < 16:
            return False, f"need 16GB RAM on NPU/GPU node (have {max_ai_node_ram:.0f}GB)"
        return True, None

    tiers = [
        {
            "num": 1,
            "name": "K8s Foundations",
            "check": check_tier1,
            "make": "make core",
            "deploys": ["K3s + kubeconfig", "Cilium CNI + LB-IPAM"],
            "learns": ["kubectl basics, pod scheduling, namespaces", "ClusterIP services, CNI networking"],
        },
        {
            "num": 2,
            "name": "Real Services",
            "check": check_tier2,
            "make": "make services",
            "deploys": ["cert-manager (TLS)", "Gateway API (ingress)", "Pi-hole (DNS)", "ArgoCD (GitOps)"],
            "learns": ["HTTPRoute pattern (ClusterIP + HTTPRoute)", "TLS termination, cert rotation", "GitOps: push → sync"],
        },
        {
            "num": 3,
            "name": "Observability",
            "check": check_tier3,
            "make": "make observability",
            "deploys": ["Prometheus + Grafana + AlertManager", "Grafana Tempo (tracing)", "Loki (logs) + Alloy (collector)"],
            "learns": ["PromQL, dashboards, alerts", "Log correlation with Loki", "Trace-based debugging with Tempo"],
        },
        {
            "num": 4,
            "name": "AI Stack",
            "check": check_tier4,
            "make": "make ai && make ai-holmes && make kagent",
            "deploys": ["Docker registry (ARM64)", "LiteLLM proxy (OpenRouter router)", "Hermes Agent (AI assistant)", "HolmesGPT + UI (SRE assistant)", "kagent (multi-tenant AI platform)"],
            "learns": ["LLM routing and fallbacks", "MCP (Model Context Protocol)", "AI agent patterns (tool-use loops)", "K8s-native AI workloads"],
            "note": "No GPU needed — uses OpenRouter API. Cost: ~$0–$5/month.",
        },
        {
            "num": 5,
            "name": "Security",
            "check": check_tier5,
            "make": "make security",
            "deploys": ["NeuVector (container runtime security, vuln scanning)"],
            "learns": ["Runtime security policies", "Network policy enforcement", "Zero-trust networking"],
        },
        {
            "num": 6,
            "name": "HA Control-Plane",
            "check": check_tier6,
            "make": "make services  # with 3 server nodes in inventory",
            "deploys": ["K3s embedded etcd across 3 server nodes", "All Tier 2–5 components across HA cluster"],
            "learns": ["etcd consensus, split-brain prevention", "Rolling upgrades without downtime", "Leader election, node failure scenarios"],
        },
        {
            "num": 7,
            "name": "Local AI Inference",
            "check": check_tier7,
            "make": "# Future: make ai-local",
            "deploys": ["Ollama or local inference engine", "RKNN runtime (Rockchip NPU)", "Local model serving"],
            "learns": ["Local LLM inference, model quantization", "NPU/GPU scheduling in K8s", "Air-gapped AI operations"],
            "note": "Not yet implemented — placeholder for future.",
        },
    ]

    # ── Evaluate all tiers ────────────────────────────────────────────────────
    ready = []
    blocked = []
    for t in tiers:
        ok, gap = t["check"]()
        if ok:
            ready.append(t)
        else:
            blocked.append((t, gap))

    # ── Best node candidates for control-plane ────────────────────────────────
    cp_sorted = sorted(
        [(h, c) for h, c in caps_map.items() if c["control_plane_capable"]],
        key=lambda x: _etcd_score(x[1]),
        reverse=True,
    )
    ai_nodes_sorted = sorted(
        [(h, c) for h, c in caps_map.items() if c["ai_worker_capable"]],
        key=lambda x: x[1]["ram_gb"],
        reverse=True,
    )

    # ── Format output ─────────────────────────────────────────────────────────
    out = ["═══ Cluster Deployment Roadmap ═══", ""]

    out += [
        f"  Hardware profile : {hw_profile}",
        f"  {hw_desc}",
        f"  Nodes: {node_count}  |  Total RAM: {total_ram:.0f}GB  |  Max node: {max_node_ram:.0f}GB",
        f"  Etcd-capable: {etcd_capable_count}/3 needed for HA",
        "",
    ]

    # ── Phases you can run NOW ────────────────────────────────────────────────
    out.append(f"━━━ Deploy NOW ({len(ready)} phases ready) ━━━")
    out.append("")
    for t in ready:
        out.append(f"  Phase {t['num']}: {t['name']}")
        out.append(f"  {'─' * 45}")
        out.append(f"  Run: {t['make']}")
        out.append("  Deploys:")
        for d in t["deploys"]:
            out.append(f"    + {d}")
        out.append("  You'll learn:")
        for l in t["learns"]:
            out.append(f"    • {l}")
        if t.get("note"):
            out.append(f"  Note: {t['note']}")
        out.append("")

    # ── Blocked phases ────────────────────────────────────────────────────────
    if blocked:
        out.append(f"━━━ Blocked ({len(blocked)} phases need more hardware/config) ━━━")
        out.append("")
        for t, gap in blocked:
            out.append(f"  Phase {t['num']}: {t['name']}  ✗")
            out.append(f"  Missing: {gap}")
            out.append("")

    # ── Node assignments ──────────────────────────────────────────────────────
    out.append("━━━ Recommended Node Assignments ━━━")
    out.append("")

    if cp_sorted:
        # Determine how many servers based on achievability
        tier6_ok = any(t["num"] == 6 for t in ready)
        server_count = 3 if tier6_ok else 1
        servers = [h for h, _ in cp_sorted[:server_count]]
        remaining = [h for h in caps_map if h not in servers]

        out.append(f"  Control-plane ({server_count} server{'s' if server_count > 1 else ''}):")
        for h, c in cp_sorted[:server_count]:
            lat = f"{c['write_latency_ms']:.1f}ms" if c["write_latency_ms"] else "?"
            npu_tag = " [NPU]" if c["has_npu"] else ""
            out.append(f"    {h:<34} {c['ram_gb']:.0f}GB | {'NVMe' if c['has_nvme'] else 'eMMC'} | {lat}{npu_tag}")

        if ai_nodes_sorted:
            out.append("")
            out.append("  AI workers (prefer high-RAM nodes for LLM agent workloads):")
            for h, c in ai_nodes_sorted:
                if h not in servers:
                    npu_tag = " [NPU]" if c["has_npu"] else ""
                    out.append(f"    {h:<34} {c['ram_gb']:.0f}GB{npu_tag}")

        general_workers = [
            h for h in remaining
            if h not in [x[0] for x in ai_nodes_sorted]
            and caps_map[h]["worker_capable"]
        ]
        if general_workers:
            out.append("")
            out.append("  General workers:")
            for h in general_workers:
                c = caps_map[h]
                out.append(f"    {h:<34} {c['ram_gb']:.0f}GB | {'NVMe' if c['has_nvme'] else 'eMMC'}")
    else:
        out.append("  No control-plane capable nodes found — check warnings.")

    out.append("")

    # ── Unlock next tier ─────────────────────────────────────────────────────
    if blocked:
        out.append("━━━ To Unlock Blocked Phases ━━━")
        out.append("")

        for t, gap in blocked:
            out.append(f"  Unlock Phase {t['num']} ({t['name']}):")
            # Translate gap into concrete buy/config recommendation
            if "etcd-capable nodes" in gap:
                missing = 3 - etcd_capable_count
                out.append(f"    → Add {missing} node(s) with NVMe + 4GB RAM + cgroups v2 + eBPF")
                out.append(f"       e.g. Raspberry Pi 5 (8GB) ~$80 each, or Turing RK1 ~$150")
            elif "total cluster RAM" in gap:
                need = 24 if t["num"] == 4 else 12 if t["num"] == 3 else 8
                delta = need - total_ram
                out.append(f"    → Add {delta:.0f}GB more cluster RAM")
                out.append(f"       e.g. Add 1–2 nodes with ≥8GB RAM (Turing RK1 32GB ~$150)")
            elif "eBPF" in gap:
                out.append(f"    → Upgrade kernel to ≥5.4 (Ubuntu 22.04+ has this by default)")
                out.append(f"       On Raspberry Pi: use 64-bit Ubuntu 22.04 or 24.04 image")
            elif "cgroups v2" in gap:
                out.append(f"    → Enable cgroups v2 in /boot/cmdline.txt:")
                out.append(f"       Add: systemd.unified_cgroup_hierarchy=1 cgroup_no_v1=all")
                out.append(f"       Reboot required")
            elif "NPU" in gap or "rknpu0" in gap:
                out.append(f"    → Add a node with Rockchip RK3588S NPU (/dev/rknpu0)")
                out.append(f"       e.g. Turing RK1 (32GB) ~$150 — 4 TOPS NPU + Mali GPU")
                out.append(f"       Or any board with dedicated GPU (RTX 3060 Ti ~$300)")
            elif "server node" in gap:
                ram_need = 4
                out.append(f"    → Ensure server node has ≥{ram_need}GB RAM")
                out.append(f"       Current best: {best_server_ram:.0f}GB")
            else:
                out.append(f"    → {gap}")
            out.append("")

    # ── Quick reference ───────────────────────────────────────────────────────
    out.append("━━━ Quick Reference ━━━")
    out.append("")
    out.append("  Run phases in order. Each builds on the previous.")
    if ready:
        first = ready[0]
        out.append(f"  Start here: {first['make']}")
    if len(ready) >= 2:
        all_cmds = []
        for t in ready:
            if t["make"] and not t["make"].startswith("#"):
                all_cmds.append(t["make"])
        if len(all_cmds) > 1:
            out.append("")
            out.append("  Full deploy sequence (all ready phases):")
            for cmd in all_cmds:
                out.append(f"    {cmd}")
    out.append("")
    out.append("  Use analyze_cluster() for flavor details and per-node assignments.")
    out.append("  Use cluster_power_score() for a hardware capability breakdown.")
    out.append("")

    return "\n".join(out)


@mcp.tool()
def get_skill(name: str) -> str:
    """
    Read a skill file to get technical documentation for a component.

    Available skills: onboarding, survey, infra-ops, k3s, cilium, gateway,
    cert-manager, argocd, pihole, monitoring, ai, kagent, k8s-debug, storage
    """
    skill_path = SKILLS_DIR / name / "SKILL.md"
    if not skill_path.exists():
        available = [p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md")]
        return (
            f"Skill '{name}' not found.\n"
            f"Available: {', '.join(sorted(available))}"
        )
    return skill_path.read_text()


@mcp.tool()
def list_skills() -> str:
    """
    List all available skill files with their descriptions.
    Use get_skill(name) to read any of them.
    """
    skill_paths = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if not skill_paths:
        return "No skills found in skills/ directory."

    lines = ["Available Skills:", ""]
    for path in skill_paths:
        name = path.parent.name
        # Try to extract description from frontmatter
        content = path.read_text()
        desc = ""
        if "description:" in content:
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("description:") and not stripped.endswith(">"):
                    desc = stripped.replace("description:", "").strip().strip('"')
                    break
                elif stripped.startswith("description: >"):
                    # Multi-line — grab next non-empty, non-indented line
                    pass
        lines.append(f"  {name:<20} {desc[:70]}")

    lines += [
        "",
        "Usage: get_skill('k3s')  or  get_skill('survey')  etc.",
    ]
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run()
