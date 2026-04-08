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
FLAVORS_FILE  = Path(__file__).resolve().parent / "flavors.yaml"
STACKS_FILE   = Path(__file__).resolve().parent / "stacks.yaml"
LEARNERS_FILE = Path(__file__).resolve().parent / "learners.yaml"
CATALOG_FILE  = Path(__file__).resolve().parent / "hardware-catalog.yaml"

# ── MCP server ────────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="cluster-advisor",
    instructions="""
K3s homelab cluster advisor — hardware planning, learning roadmaps, stack analysis.

Knowledge graph:
  survey/*.json        → live hardware facts per node
  stacks.yaml          → modular behavioral stacks (what to deploy)
  learners.yaml        → learning profiles and curricula (what to learn)
  hardware-catalog.yaml→ boards to buy with prices and K8s readiness
  skills/**/SKILL.md   → deep technical docs per component

Tools:
  list_nodes()              — table of all surveyed nodes
  node_profile(hostname)    — detailed hardware + K8s readiness per node
  analyze_cluster()         — flavor recommendation + node assignments
  cluster_stacks()          — RAM budget per behavioral stack + combinations
  cluster_roadmap()         — phased deployment plan from survey data
  cluster_power_score()     — 5-dimension hardware score (S/A/B/C/D)
  learning_roadmap(profile) — personalized curriculum for a learning goal
  hardware_catalog()        — browse boards to buy + K8s readiness
  what_to_buy(goal)         — targeted buy recommendation for a specific goal
  get_skill(name)           — read technical skill doc for any component
  list_skills()             — list all available skills

Typical flow:
  1. analyze_cluster() or cluster_stacks()  → understand current hardware
  2. learning_roadmap('devops')             → get a personalized curriculum
  3. what_to_buy('local inference')         → know what to buy next
  4. get_skill('ai')                        → deep-dive any component
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


def _load_stacks() -> dict:
    """Load modular stack definitions from stacks.yaml. Returns {} if missing."""
    if not STACKS_FILE.exists():
        return {}
    try:
        return yaml.safe_load(STACKS_FILE.read_text()).get("stacks", {})
    except Exception:
        return {}


def _load_learners() -> dict:
    """Load learning profiles from learners.yaml. Returns {} if missing."""
    if not LEARNERS_FILE.exists():
        return {}
    try:
        return yaml.safe_load(LEARNERS_FILE.read_text()).get("profiles", {})
    except Exception:
        return {}


def _load_catalog() -> dict:
    """Load hardware catalog from hardware-catalog.yaml. Returns {} if missing."""
    if not CATALOG_FILE.exists():
        return {}
    try:
        return yaml.safe_load(CATALOG_FILE.read_text()).get("boards", {})
    except Exception:
        return {}


def _stack_ram_mb(sdef: dict) -> int:
    """Get RAM estimate from a stack definition. Returns 0 if missing."""
    return int(sdef.get("ram_mb", 0))


def _board_unlocks(board: dict, stacks: dict) -> list[str]:
    """Return list of stack names this board unlocks (soft refs — unknown keys skipped)."""
    return [s for s in board.get("unlocks_stacks", []) if s in stacks]


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
        roles.append("  ✓ ai-worker (≥16GB RAM — suitable for LLM/agent workloads)")
    elif caps["has_npu"]:
        roles.append(f"  ~ ai-worker (NPU present but only {caps['ram_gb']:.0f}GB RAM — needs ≥16GB)")
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
                assignment[h] = "K3s agent (ai-worker — high-RAM)"
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
        f"AI workers     : {len(ai_nodes)} nodes (≥16GB RAM, suitable for LLM workloads)",
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
        f"  NAS (SMB)    : {'detected' if has_nas else 'not auto-detected (run make storage if you have a NAS)'}",
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
        (storage_score >= 40, "Persistent storage (SMB/NAS PVCs — run make storage)"),
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
        bottlenecks.append("  ⚠ NAS not auto-detected via survey (port 445 scan) — if you have a NAS, run 'make storage'")
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
            "make": "make core && make networking",
            "deploys": ["K3s + kubeconfig", "Cilium CNI + LB-IPAM + Gateway API CRDs"],
            "learns": ["kubectl basics, pod scheduling, namespaces", "ClusterIP services, CNI networking, LB-IPAM"],
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
            "make": "make services",
            "deploys": ["K3s embedded etcd across 3 server nodes", "All Tier 2–5 components across HA cluster"],
            "learns": ["etcd consensus, split-brain prevention", "Rolling upgrades without downtime", "Leader election, node failure scenarios"],
            "note": "Requires 3 server nodes configured in inventory/hosts.ini — assign the 3 fastest-disk nodes.",
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
def cluster_stacks() -> str:
    """
    Analyze which modular behavioral stacks fit the cluster's hardware budget.

    Stacks are self-contained purpose units: observability, ai, security, etc.
    core + networking are always required (K3s + Cilium — no alternatives).

    For each stack combination, shows:
    - RAM budget consumed vs available
    - Whether it fits on the current hardware
    - Which nodes are best suited for each stack
    - Make commands to deploy each combination

    Use this instead of cluster_roadmap() when you want to think in terms of
    "what can I run together" rather than "what phases to deploy in order".
    """
    nodes = _load_surveys()
    if not nodes:
        return "No survey data found.\nRun: make survey"

    raw = yaml.safe_load(STACKS_FILE.read_text())
    all_stacks = raw.get("stacks", {})

    caps_map = {h: _node_capabilities(d) for h, d in nodes.items() if "_error" not in d}
    all_caps = list(caps_map.values())

    # ── Cluster metrics ────────────────────────────────────────────────────────
    node_count = len(all_caps)
    total_ram_gb = sum(c["ram_gb"] for c in all_caps)
    total_ram_mb = total_ram_gb * 1024
    max_node_ram_gb = max((c["ram_gb"] for c in all_caps), default=0)
    best_server_ram_gb = max(
        (c["ram_gb"] for c in all_caps if c["control_plane_capable"]), default=0
    )
    has_cgroups_v2 = any(c["cgroups_v2"] for c in all_caps)
    has_ebpf = any(c["ebpf"] for c in all_caps)
    has_npu = any(c["has_npu"] for c in all_caps)

    # ── Per-stack feasibility ──────────────────────────────────────────────────
    def _stack_feasible(sdef: dict) -> tuple[bool, list[str]]:
        """Returns (feasible, list_of_gaps)."""
        gaps = []
        req = sdef
        if not has_cgroups_v2:
            gaps.append("cgroups v2 required")
        if req.get("kind") in ("foundation", "additive", "behavioral"):
            if "min_ram_gb_server" in req and best_server_ram_gb < req["min_ram_gb_server"]:
                gaps.append(f"server node needs {req['min_ram_gb_server']}GB RAM (have {best_server_ram_gb:.0f}GB)")
            if "min_cluster_ram_gb" in req and total_ram_gb < req["min_cluster_ram_gb"]:
                delta = req["min_cluster_ram_gb"] - total_ram_gb
                gaps.append(f"need {req['min_cluster_ram_gb']}GB total RAM (have {total_ram_gb:.0f}GB, need +{delta:.0f}GB)")
            if "min_nodes" in req and node_count < req["min_nodes"]:
                gaps.append(f"need {req['min_nodes']} nodes (have {node_count})")
            # Networking-specific: needs eBPF
            if sdef.get("always_required") and "cilium" in str(sdef.get("roles", [])).lower():
                if not has_ebpf:
                    gaps.append("eBPF required for Cilium (kernel ≥5.4)")
        return (len(gaps) == 0, gaps)

    # ── Behavioral stacks (the interesting ones) ───────────────────────────────
    behavioral_stacks = {k: v for k, v in all_stacks.items()
                         if v.get("kind") == "behavioral" and not k.startswith("_")}
    foundation_ram_mb = sum(
        all_stacks[k].get("ram_mb", 0)
        for k in ["core", "networking", "ingress", "dns", "gitops"]
        if k in all_stacks
    )

    # ── Meta-stacks (predefined combinations) ─────────────────────────────────
    meta_stacks = {k: v for k, v in all_stacks.items() if v.get("kind") == "meta"}

    # ── Format output ──────────────────────────────────────────────────────────
    out = ["═══ Cluster Stack Analysis ═══", ""]

    out += [
        f"  Hardware: {node_count} nodes | {total_ram_gb:.0f}GB total RAM | {max_node_ram_gb:.0f}GB max/node",
        f"  K3s (base): always-on | Cilium (networking): always-on",
        "",
    ]

    # ── Foundation always-on budget ────────────────────────────────────────────
    out.append("━━━ Always-On Base (required for everything) ━━━")
    out.append("")
    foundation_gb = foundation_ram_mb / 1024
    out.append(f"  core + networking + ingress + dns + gitops")
    out.append(f"  RAM: ~{foundation_gb:.1f}GB  |  Make: make core && make networking && make ingress && make dns && make gitops")
    out.append(f"  Budget remaining after foundation: {total_ram_gb - foundation_gb:.0f}GB / {total_ram_mb - foundation_ram_mb:.0f}MB")
    out.append("")

    # ── Individual behavioral stacks ───────────────────────────────────────────
    out.append("━━━ Behavioral Stacks ━━━")
    out.append("")
    remaining_after_foundation = total_ram_mb - foundation_ram_mb

    for sname, sdef in behavioral_stacks.items():
        feasible, gaps = _stack_feasible(sdef)
        ram_mb = sdef.get("ram_mb", 0)
        ram_gb = ram_mb / 1024
        fits_budget = remaining_after_foundation >= ram_mb
        icon = "✓" if (feasible and fits_budget) else "✗"
        pct = int(ram_mb / total_ram_mb * 100) if total_ram_mb else 0

        out.append(f"  {icon} {sname.upper():<16} ~{ram_gb:.0f}GB  ({pct}% of cluster RAM)")
        out.append(f"     {sdef['behavior']}")
        out.append(f"     Run: {sdef['make']}")

        if sdef.get("what_you_get"):
            out.append("     Includes:")
            for item in sdef["what_you_get"][:3]:
                out.append(f"       + {item}")

        if not feasible:
            out.append(f"     ✗ Blocked: {'; '.join(gaps)}")
        elif not fits_budget:
            needed_extra = ram_mb - remaining_after_foundation
            out.append(f"     ✗ RAM tight: needs +{needed_extra/1024:.0f}GB more cluster RAM")

        out.append("")

    # ── Combinations that fit simultaneously ──────────────────────────────────
    out.append("━━━ What Fits Simultaneously ━━━")
    out.append("")

    feasible_behavioral = [
        (k, v) for k, v in behavioral_stacks.items()
        if _stack_feasible(v)[0] and remaining_after_foundation >= v.get("ram_mb", 0)
    ]

    if feasible_behavioral:
        total_behavioral_ram = sum(v.get("ram_mb", 0) for _, v in feasible_behavioral)
        total_with_foundation = foundation_ram_mb + total_behavioral_ram
        total_gb = total_with_foundation / 1024
        pct_used = int(total_with_foundation / total_ram_mb * 100) if total_ram_mb else 0

        out.append(f"  All behavioral stacks can run simultaneously:")
        for k, v in feasible_behavioral:
            out.append(f"    + {k:<20} ~{v['ram_mb']/1024:.0f}GB")
        out.append(f"  ─────────────────────────────")
        out.append(f"  Total (incl. foundation): ~{total_gb:.0f}GB / {total_ram_gb:.0f}GB available ({pct_used}% used)")
        spare = total_ram_gb - total_gb
        out.append(f"  Headroom: ~{spare:.0f}GB spare — {'comfortable' if spare > 20 else 'tight' if spare > 5 else 'very tight'}")
        out.append("")
    else:
        out.append("  ✗ No behavioral stacks fit — check hardware requirements.")
        out.append("")

    # ── Predefined combination recipes ────────────────────────────────────────
    out.append("━━━ Combination Recipes ━━━")
    out.append("")

    for mname, mdef in sorted(meta_stacks.items(), key=lambda x: x[1].get("ram_mb_total", 0)):
        if mname.startswith("_"):
            label = mdef.get("label", mname)
        else:
            label = mname
        ram_total_gb = mdef.get("ram_mb_total", 0) / 1024
        fits = total_ram_gb >= ram_total_gb
        icon = "✓" if fits else "✗"
        pct = int(mdef.get("ram_mb_total", 0) / total_ram_mb * 100) if total_ram_mb else 0

        out.append(f"  {icon} {label}")
        out.append(f"     {mdef['description']}")
        out.append(f"     RAM: ~{ram_total_gb:.0f}GB ({pct}% of cluster) — {'fits' if fits else f'needs +{ram_total_gb - total_ram_gb:.0f}GB'}")
        out.append("     Deploy sequence:")
        for cmd in mdef.get("make_sequence", []):
            out.append(f"       {cmd}")
        out.append("")

    # ── Node placement recommendations ────────────────────────────────────────
    out.append("━━━ Node Placement Recommendations ━━━")
    out.append("")

    # Best control-plane nodes (fastest disk, 4+ GB)
    cp_sorted = sorted(
        [(h, c) for h, c in caps_map.items() if c["control_plane_capable"]],
        key=lambda x: _etcd_score(x[1]), reverse=True
    )
    ai_sorted = sorted(
        [(h, c) for h, c in caps_map.items() if c["ai_worker_capable"]],
        key=lambda x: x[1]["ram_gb"], reverse=True
    )

    if cp_sorted:
        out.append("  Control-plane (K3s server) — pick fastest disk:")
        for h, c in cp_sorted[:3]:
            lat = f"{c['write_latency_ms']:.2f}ms" if c["write_latency_ms"] else "?"
            out.append(f"    {h:<36} {c['ram_gb']:.0f}GB | {'NVMe' if c['has_nvme'] else 'eMMC'} | {lat}")

    if ai_sorted:
        out.append("")
        out.append("  AI stack workers — pick highest-RAM (memory-bound workloads):")
        for h, c in ai_sorted[:4]:
            npu = " [NPU]" if c["has_npu"] else ""
            out.append(f"    {h:<36} {c['ram_gb']:.0f}GB{npu}")

    # Observability placement
    obs_nodes = sorted(
        [(h, c) for h, c in caps_map.items() if c["worker_capable"]],
        key=lambda x: x[1]["ram_gb"], reverse=True
    )
    if obs_nodes:
        out.append("")
        out.append("  Observability stack — any node with ≥4GB RAM:")
        for h, c in obs_nodes[:2]:
            out.append(f"    {h:<36} {c['ram_gb']:.0f}GB")

    out.append("")
    out.append("  Tip: K3s node labels let you pin stacks to specific nodes via nodeSelector.")
    out.append("       e.g. kubectl label node srv-rk1-nvme-01 role=ai-worker")
    out.append("")

    return "\n".join(out)


@mcp.tool()
def learning_roadmap(profile: str = "") -> str:
    """
    Generate a personalized learning roadmap for a given profile.

    Profiles: beginner, devops, ai-builder, security-engineer, full-stack
    Leave profile empty to list available profiles.

    Each profile shows:
    - What you'll learn at each stage
    - Which stacks to deploy in order
    - Milestones you can demo after each stage
    - Minimum hardware required
    - What to deploy next after completing this profile
    """
    learners = _load_learners()
    stacks = _load_stacks()

    if not learners:
        return "learners.yaml not found.\nExpected at: " + str(LEARNERS_FILE)

    # List profiles if none specified or unknown
    if not profile or profile not in learners:
        out = ["Available learning profiles:", ""]
        for pname, pdef in sorted(learners.items(), key=lambda x: x[1].get("level", 99)):
            emoji = pdef.get("emoji", "")
            label = pdef.get("label", pname)
            level = pdef.get("level", "?")
            goal = pdef.get("goal", "")
            out.append(f"  {emoji} {pname:<22} [L{level}]  {label}")
            out.append(f"     {goal[:80]}")
            out.append("")
        out.append("Usage: learning_roadmap('beginner')  or  learning_roadmap('devops')")
        if profile and profile not in learners:
            out.insert(0, f"Profile '{profile}' not found.\n")
        return "\n".join(out)

    pdef = learners[profile]
    emoji = pdef.get("emoji", "")
    label = pdef.get("label", profile)
    level = pdef.get("level", "?")

    # Check hardware feasibility against profile requirements
    nodes = _load_surveys()
    caps_map = {h: _node_capabilities(d) for h, d in nodes.items() if "_error" not in d}
    all_caps = list(caps_map.values())
    total_ram_gb = sum(c["ram_gb"] for c in all_caps)
    node_count = len(all_caps)

    hw_req = pdef.get("min_hardware", {})
    hw_gaps = []
    if node_count < hw_req.get("nodes", 1):
        hw_gaps.append(f"need {hw_req['nodes']} nodes (have {node_count})")
    if total_ram_gb < hw_req.get("ram_gb", 0):
        hw_gaps.append(f"need {hw_req['ram_gb']}GB total RAM (have {total_ram_gb:.0f}GB)")

    out = [f"═══ {emoji} {label} Roadmap (Level {level}) ═══", ""]
    out.append(f"  Goal: {pdef.get('goal', '')}")
    out.append(f"  For:  {pdef.get('archetype', '')}")
    out.append("")

    if nodes:
        if hw_gaps:
            out.append(f"  ⚠ Hardware gaps: {'; '.join(hw_gaps)}")
        else:
            out.append(f"  ✓ Hardware ready ({node_count} nodes, {total_ram_gb:.0f}GB RAM)")
        out.append("")

    # Curriculum — each stack as a stage
    curriculum = pdef.get("stacks_ordered", [])
    for i, stage in enumerate(curriculum, 1):
        sname = stage.get("name", "") if isinstance(stage, dict) else str(stage)
        sdef_global = stacks.get(sname, {})

        why = stage.get("why", sdef_global.get("behavior", "")) if isinstance(stage, dict) else ""
        teaches = stage.get("teaches", []) if isinstance(stage, dict) else []
        milestone = stage.get("milestone", "") if isinstance(stage, dict) else ""
        make_cmd = sdef_global.get("make", f"make {sname}")

        status = "✓" if sdef_global else "?"
        out.append(f"  Stage {i}: {sname.upper()}")
        out.append(f"  {'─' * 50}")
        if why:
            out.append(f"  Why: {why}")
        out.append(f"  Run: {make_cmd}")

        if teaches:
            out.append("  You'll learn:")
            for t in teaches:
                out.append(f"    • {t}")

        if milestone:
            out.append(f"  Milestone: ✅ {milestone}")

        # Cross-ref with stacks.yaml for what_you_get
        if sdef_global.get("what_you_get"):
            out.append("  Deploys:")
            for item in sdef_global["what_you_get"][:3]:
                out.append(f"    + {item}")

        out.append("")

    # Hardware requirements summary
    out.append("  ─── Minimum Hardware for this Profile ───")
    for k, v in hw_req.items():
        out.append(f"    {k}: {v}")
    out.append("")

    # Notes
    for note in pdef.get("notes", []):
        out.append(f"  💡 {note}")
    if pdef.get("notes"):
        out.append("")

    # Next profile
    next_p = pdef.get("next_profile", "")
    if next_p and next_p in learners:
        nd = learners[next_p]
        out.append(f"  After completing this: → {nd.get('emoji','')} {nd.get('label', next_p)} profile")
        out.append(f"    learning_roadmap('{next_p}')")

    return "\n".join(out)


@mcp.tool()
def hardware_catalog() -> str:
    """
    Browse the catalog of boards you can buy to build or expand your K8s homelab.

    Shows boards organized by tier (nano → large) with:
    - Price, vendor URLs, K8s readiness (cgroups_v2, eBPF, NVMe)
    - Which stacks each board can run
    - Notes on gotchas and K8s compatibility
    """
    catalog = _load_catalog()
    stacks = _load_stacks()

    if not catalog:
        return "hardware-catalog.yaml not found.\nExpected at: " + str(CATALOG_FILE)

    # Group by k8s_tier
    tier_order = ["nano", "micro", "medium", "large", "depends on modules"]
    by_tier: dict[str, list] = {}
    for bname, bdef in catalog.items():
        tier = bdef.get("k8s_tier", "unknown")
        by_tier.setdefault(tier, []).append((bname, bdef))

    out = ["═══ Hardware Catalog ═══", ""]
    out.append("  Boards listed by K8s capability tier (nano → large)")
    out.append("  Prices are approximate — check vendor URL for current price")
    out.append("")

    for tier in tier_order + [t for t in by_tier if t not in tier_order]:
        boards = by_tier.get(tier, [])
        if not boards:
            continue

        out.append(f"━━━ Tier: {tier.upper()} ━━━")
        out.append("")

        for bname, bdef in sorted(boards, key=lambda x: x[1].get("price_usd", 999)):
            name = bdef.get("name", bname)
            variant = bdef.get("variant", "")
            price = bdef.get("price_usd", "?")
            arch = bdef.get("arch", "?")
            ram = bdef.get("ram_gb", "?")
            npu = "NPU" if bdef.get("has_npu") else ""
            gpu = "GPU" if bdef.get("has_gpu") and not bdef.get("has_npu") else ""
            special = " | ".join(filter(None, [npu, gpu]))
            cg2 = "✓" if bdef.get("cgroups_v2") else "✗"
            ebpf_ok = "✓" if bdef.get("ebpf") else "✗"
            nvme_ok = "NVMe" if "nvme" in bdef.get("storage_type", "").lower() else "eMMC/SD"
            carrier = " [needs carrier]" if bdef.get("requires_carrier") else ""

            unlocks = _board_unlocks(bdef, stacks)
            unlocks_str = ", ".join(unlocks[:4]) + ("..." if len(unlocks) > 4 else "")

            out.append(f"  {name} {variant}{carrier}")
            out.append(f"    ${price:<6} | {arch:<7} | {ram}GB RAM | {nvme_ok} | cgroups_v2:{cg2} eBPF:{ebpf_ok}" +
                       (f" | {special}" if special else ""))
            if unlocks_str:
                out.append(f"    Unlocks: {unlocks_str}")

            # Vendors
            vendors = bdef.get("vendors", [])
            if vendors:
                v = vendors[0]
                out.append(f"    Buy: {v['name']} — {v['url']}")
            if len(vendors) > 1:
                out.append(f"    Also: " + " | ".join(v["name"] for v in vendors[1:3]))

            # First note
            notes = bdef.get("notes", [])
            if notes:
                out.append(f"    Note: {notes[0]}")

            # Carrier boards
            carriers = bdef.get("carrier_boards", [])
            if carriers:
                c = carriers[0]
                out.append(f"    Carrier: {c['name']} ~${c['price_usd']} — {c['url']}")

            out.append("")

    out.append("━━━ Tip ━━━")
    out.append("")
    out.append("  Use what_to_buy(goal) for targeted recommendations.")
    out.append("  Examples:")
    out.append("    what_to_buy('ha control-plane')   → 3 fast-disk nodes")
    out.append("    what_to_buy('local ai inference') → NPU board")
    out.append("    what_to_buy('more workers')       → budget SBCs")

    return "\n".join(out)


@mcp.tool()
def what_to_buy(goal: str) -> str:
    """
    Get targeted hardware recommendations for a specific goal.

    Examples:
      what_to_buy('ha control-plane')    — need 3 etcd nodes with fast NVMe
      what_to_buy('local ai inference')  — need NPU or GPU board
      what_to_buy('more workers')        — budget worker nodes
      what_to_buy('ai stack')            — high-RAM nodes for LLM agents
      what_to_buy('full cluster')        — complete cluster from scratch
    """
    catalog = _load_catalog()
    stacks = _load_stacks()
    learners = _load_learners()

    if not catalog:
        return "hardware-catalog.yaml not found.\nExpected at: " + str(CATALOG_FILE)

    # Load current cluster state if available
    nodes = _load_surveys()
    caps_map = {h: _node_capabilities(d) for h, d in nodes.items() if "_error" not in d}
    all_caps = list(caps_map.values())
    total_ram_gb = sum(c["ram_gb"] for c in all_caps)
    node_count = len(all_caps)
    etcd_count = sum(1 for c in all_caps if c["etcd_capable"])

    goal_lower = goal.lower()
    all_boards = list(catalog.items())

    def _boards_with(filter_fn) -> list:
        return [(k, v) for k, v in all_boards if filter_fn(v)]

    def _fmt_board(bname: str, bdef: dict, reason: str = "") -> list[str]:
        name = bdef.get("name", bname)
        variant = bdef.get("variant", "")
        price = bdef.get("price_usd", "?")
        ram = bdef.get("ram_gb", "?")
        npu = " + NPU" if bdef.get("has_npu") else ""
        carrier_note = ""
        carriers = bdef.get("carrier_boards", [])
        if bdef.get("requires_carrier") and carriers:
            c = carriers[0]
            carrier_note = f" (+ {c['name']} ~${c['price_usd']})"
        vendors = bdef.get("vendors", [])
        buy_url = vendors[0]["url"] if vendors else "—"
        lines = [
            f"  → {name} {variant}",
            f"     ${price}{carrier_note} | {ram}GB RAM{npu}",
        ]
        if reason:
            lines.append(f"     Why: {reason}")
        lines.append(f"     Buy: {buy_url}")
        for note in bdef.get("notes", [])[:2]:
            lines.append(f"     Note: {note}")
        return lines

    out = [f"═══ What to Buy: {goal} ═══", ""]

    # Current state context
    if nodes:
        out.append(f"  Current cluster: {node_count} nodes | {total_ram_gb:.0f}GB RAM | {etcd_count} etcd-capable")
        out.append("")

    # ── Goal matching ──────────────────────────────────────────────────────────
    matched = False

    if any(k in goal_lower for k in ["ha", "etcd", "control", "3 node"]):
        matched = True
        need_more = max(0, 3 - etcd_count)
        out.append("  Goal: HA control-plane (3 etcd nodes with NVMe + <10ms write latency)")
        out.append("")
        if etcd_count >= 3:
            out.append("  ✓ You already have 3+ etcd-capable nodes — HA is achievable now!")
            out.append("    Run: make services  (with 3 server nodes in inventory)")
        else:
            out.append(f"  Need {need_more} more etcd-capable node(s):")
            out.append("")
            candidates = _boards_with(lambda b: (
                b.get("ebpf") and b.get("cgroups_v2") and
                b.get("ram_gb", 0) >= 4 and
                "nvme" in b.get("storage_type", "").lower() and
                not b.get("requires_carrier")
            ))
            candidates.sort(key=lambda x: x[1].get("price_usd", 999))
            for bname, bdef in candidates[:3]:
                out += _fmt_board(bname, bdef, "NVMe + cgroups v2 + eBPF — etcd-capable")
                out.append("")
        out.append("")

    if any(k in goal_lower for k in ["npu", "inference", "local ai", "ollama", "rknn", "gpu"]):
        matched = True
        out.append("  Goal: Local AI inference (NPU or GPU for on-device LLM)")
        out.append("")
        npu_boards = _boards_with(lambda b: b.get("has_npu") or b.get("has_gpu"))
        npu_boards.sort(key=lambda x: x[1].get("price_usd", 999))
        for bname, bdef in npu_boards[:4]:
            tops = bdef.get("npu_tops", "")
            reason = f"{tops} TOPS NPU — local LLM inference" if tops else "GPU — ROCm/CUDA inference"
            out += _fmt_board(bname, bdef, reason)
            out.append("")
        out.append("")

    if any(k in goal_lower for k in ["ai stack", "ai worker", "hermes", "llm", "kagent", "ram"]):
        matched = True
        out.append("  Goal: AI stack workers (high-RAM nodes for LLM agent workloads)")
        out.append("  AI stack is memory-bound — more GB = bigger models + more agents")
        out.append("")
        ai_boards = _boards_with(lambda b: b.get("ram_gb", 0) >= 16)
        ai_boards.sort(key=lambda x: (-x[1].get("ram_gb", 0), x[1].get("price_usd", 999)))
        for bname, bdef in ai_boards[:4]:
            reason = f"{bdef.get('ram_gb')}GB RAM — fits full AI stack (needs 24GB cluster)"
            out += _fmt_board(bname, bdef, reason)
            out.append("")
        out.append("")

    if any(k in goal_lower for k in ["worker", "budget", "cheap", "scale", "more node"]):
        matched = True
        out.append("  Goal: Budget worker nodes (scale the cluster cheaply)")
        out.append("")
        budget = _boards_with(lambda b: (
            b.get("price_usd", 999) < 100 and
            b.get("ram_gb", 0) >= 4 and
            b.get("cgroups_v2") and
            b.get("ebpf")
        ))
        budget.sort(key=lambda x: x[1].get("price_usd", 999))
        for bname, bdef in budget[:4]:
            out += _fmt_board(bname, bdef, "Budget K8s worker — cgroups v2 + eBPF")
            out.append("")
        out.append("")

    if any(k in goal_lower for k in ["full", "scratch", "start", "build", "cluster from"]):
        matched = True
        out.append("  Goal: Build a complete cluster from scratch")
        out.append("")
        out.append("  Recommended starter kit (3-node HA cluster, ~$600):")
        out.append("")
        # TuringPi 2 + 2× CM4 + 1× RK1 or 3× RPi5
        rpi5 = catalog.get("rpi5-8gb")
        tp2 = catalog.get("turingpi2-board")
        rk1 = catalog.get("turing-rk1-32gb")
        if rpi5:
            out += _fmt_board("rpi5-8gb", rpi5, "3× as HA control-plane (fast NVMe via M.2 HAT)")
            out.append("")
        if tp2:
            out += _fmt_board("turingpi2-board", tp2, "Backplane — fits 4 modules in 1U")
            out.append("")
        if rk1:
            out += _fmt_board("turing-rk1-32gb", rk1, "1× as AI worker (31GB RAM + NPU)")
            out.append("")
        out.append("  Budget breakdown:")
        total = 0
        for bname, qty, role in [
            ("rpi5-8gb", 3, "control-plane"),
            ("turingpi2-board", 1, "backplane"),
            ("turing-rk1-32gb", 1, "AI worker"),
        ]:
            b = catalog.get(bname, {})
            p = b.get("price_usd", 0) * qty
            total += p
            out.append(f"    {qty}× {b.get('name', bname)}: ~${p}")
        out.append(f"    ─────────────────")
        out.append(f"    Total: ~${total}")
        out.append("")

    if not matched:
        # Generic: show all affordable boards
        out.append(f"  No specific match for '{goal}' — showing all boards by price:")
        out.append("")
        sorted_boards = sorted(all_boards, key=lambda x: x[1].get("price_usd", 999))
        for bname, bdef in sorted_boards[:6]:
            out += _fmt_board(bname, bdef)
            out.append("")

    out.append("━━━ Other goals you can ask about ━━━")
    out.append("  what_to_buy('ha control-plane')  |  what_to_buy('local ai inference')")
    out.append("  what_to_buy('more workers')       |  what_to_buy('full cluster from scratch')")

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
