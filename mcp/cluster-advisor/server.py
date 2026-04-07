#!/usr/bin/env python3
"""
cluster-advisor MCP server

Reads node survey data (playbooks/survey-output/*.json) and skill files (skills/*)
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
SURVEY_DIR = REPO_ROOT / "playbooks" / "survey-output"
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

Survey data lives in: playbooks/survey-output/<hostname>.json
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
    has_gpu_npu = bool(gpu_npu)

    return {
        "ram_gb": ram_gb,
        "cgroups_v2": cgroups == "v2",
        "ebpf": bool(ebpf),
        "write_latency_ms": write_latency_ms,
        "has_nvme": has_nvme,
        "has_gpu_npu": has_gpu_npu,
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
            has_gpu_npu
            and ram_gb >= 16
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
            "No survey data found in playbooks/survey-output/\n"
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
                reasons_ok.append(
                    f"{len(fast)} nodes with write latency <{limit}ms "
                    f"({', '.join(f'{h}: {c[\"write_latency_ms\"]:.1f}ms' for h, c in list(fast.items())[:3])})"
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
