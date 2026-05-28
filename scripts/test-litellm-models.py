#!/usr/bin/env python3
"""
LiteLLM model smoke-tester.
Discovers all configured model names and sends each a minimal chat completion,
reporting latency and success/failure for every model.

Usage:
  # Auto port-forward (recommended — no setup needed):
  python scripts/test-litellm-models.py --port-forward

  # If you already have port-forward running (kubectl port-forward svc/litellm-proxy -n ai 4000:4000):
  python scripts/test-litellm-models.py

  # In-cluster URL:
  LITELLM_URL=http://litellm-proxy.ai.svc.cluster.local:4000 python scripts/test-litellm-models.py

  # Filter, adjust timeout, parallelism:
  python scripts/test-litellm-models.py --port-forward --filter npu
  python scripts/test-litellm-models.py --port-forward --timeout 45 --workers 3
  python scripts/test-litellm-models.py --port-forward --list

Environment:
  LITELLM_URL      proxy base URL (default: http://localhost:4000)
  LITELLM_API_KEY  API key       (default: sk-hermes-internal)
"""

import argparse
import concurrent.futures
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ── Config ──────────────────────────────────────────────────────────────────

LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:4000")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "sk-hermes-internal")

TEST_PROMPT = "Reply with exactly the word: OK"
MAX_TOKENS = 15

# ── ANSI colors ──────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def _req(method, url, body=None, timeout=15, api_key=None):
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": f"Bearer {api_key or LITELLM_API_KEY}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get_models(base_url: str) -> list[str]:
    data = _req("GET", f"{base_url}/v1/models")
    return sorted({m["id"] for m in data.get("data", [])})


def test_model(base_url: str, model_name: str, timeout: int, api_key: str) -> dict:
    t0 = time.monotonic()
    try:
        resp = _req(
            "POST",
            f"{base_url}/v1/chat/completions",
            body={
                "model": model_name,
                "messages": [{"role": "user", "content": TEST_PROMPT}],
                "max_tokens": MAX_TOKENS,
            },
            timeout=timeout,
            api_key=api_key,
        )
        elapsed = time.monotonic() - t0
        content = resp["choices"][0]["message"]["content"].strip()
        return {"status": "ok", "elapsed": elapsed, "content": content[:60]}
    except urllib.error.HTTPError as exc:
        elapsed = time.monotonic() - t0
        try:
            body = exc.read().decode(errors="replace")
            detail = json.loads(body).get("error", {})
            msg = detail.get("message", body) if isinstance(detail, dict) else str(detail)
        except Exception:
            msg = exc.reason or str(exc)
        return {"status": "error", "elapsed": elapsed, "error": f"HTTP {exc.code}: {msg[:150]}"}
    except TimeoutError:
        return {"status": "timeout", "elapsed": float(timeout), "error": "timed out"}
    except OSError as exc:
        elapsed = time.monotonic() - t0
        return {"status": "error", "elapsed": elapsed, "error": str(exc)[:150]}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return {"status": "error", "elapsed": elapsed, "error": str(exc)[:150]}


def start_port_forward(namespace: str = "ai", service: str = "litellm-proxy",
                       local_port: int = 4000, remote_port: int = 4000) -> subprocess.Popen:
    proc = subprocess.Popen(
        ["kubectl", "port-forward", f"svc/{service}", f"{local_port}:{remote_port}", "-n", namespace],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    # Wait until the proxy is listening (up to 10s)
    deadline = time.monotonic() + 10
    url = f"http://localhost:{local_port}/health"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            err = proc.stderr.read().decode(errors="replace")
            raise RuntimeError(f"kubectl port-forward exited early: {err}")
        try:
            req = urllib.request.Request(url)
            urllib.request.urlopen(req, timeout=2)
            return proc
        except Exception:
            time.sleep(0.3)
    return proc  # return even if health check didn't respond — proxy may still work


def fmt_line(model: str, result: dict, name_width: int) -> str:
    elapsed = f"{result['elapsed']:5.1f}s"
    if result["status"] == "ok":
        icon = f"{GREEN}✓{RESET}"
        detail = f"{DIM}{result['content']}{RESET}"
    elif result["status"] == "timeout":
        icon = f"{YELLOW}⏱{RESET}"
        detail = f"{YELLOW}timeout{RESET}"
    else:
        icon = f"{RED}✗{RESET}"
        detail = f"{RED}{result['error'][:100]}{RESET}"
    return f"  {icon}  {model:<{name_width}}  {elapsed}  {detail}"


def main():
    parser = argparse.ArgumentParser(description="Smoke-test all LiteLLM models")
    parser.add_argument("--port-forward", "-p", action="store_true",
                        help="Auto-start kubectl port-forward to litellm-proxy (namespace ai, port 4000)")
    parser.add_argument("--filter", "-f", default="",
                        help="Only test models whose name contains this string")
    parser.add_argument("--timeout", "-t", type=int, default=30,
                        help="Seconds per model (default: 30)")
    parser.add_argument("--workers", "-w", type=int, default=5,
                        help="Parallel workers (default: 5)")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List model names only, no testing")
    parser.add_argument("--key", "-k", default=LITELLM_API_KEY,
                        help="LiteLLM API key (overrides LITELLM_API_KEY env var)")
    parser.add_argument("--url", "-u", default=None,
                        help="LiteLLM base URL (overrides LITELLM_URL env var)")
    args = parser.parse_args()

    base_url = args.url or LITELLM_URL
    api_key = args.key

    print(f"\n{BOLD}{CYAN}LiteLLM Model Smoke-Tester{RESET}")

    pf_proc = None
    if args.port_forward:
        print(f"  Starting kubectl port-forward → litellm-proxy:4000...")
        try:
            pf_proc = start_port_forward()
            base_url = "http://localhost:4000"
            print(f"  {GREEN}port-forward ready{RESET}")
        except Exception as exc:
            print(f"  {RED}port-forward failed: {exc}{RESET}")
            sys.exit(1)

    print(f"  URL: {base_url}")
    print(f"  Key: {api_key[:10]}...")
    print()

    try:
        try:
            models = get_models(base_url)
        except Exception as exc:
            print(f"{RED}[ERROR] Cannot reach LiteLLM at {base_url}: {exc}{RESET}")
            print(f"  Tip: run with {BOLD}--port-forward{RESET} to auto-setup kubectl port-forward")
            sys.exit(1)

        if args.filter:
            models = [m for m in models if args.filter.lower() in m.lower()]

        if not models:
            print(f"{YELLOW}No models found (filter={args.filter!r}){RESET}")
            sys.exit(0)

        total = len(models)
        name_width = max(len(m) for m in models)

        print(f"Found {BOLD}{total}{RESET} model(s){f' matching {args.filter!r}' if args.filter else ''}:")

        if args.list:
            for m in models:
                print(f"  {m}")
            return

        print()
        print(f"  Prompt:  \"{TEST_PROMPT}\"")
        print(f"  Workers: {args.workers} parallel  |  Timeout: {args.timeout}s/model")
        print()

        results: dict[str, dict] = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(test_model, base_url, m, args.timeout, api_key): m
                for m in models
            }
            for fut in concurrent.futures.as_completed(futures):
                model = futures[fut]
                result = fut.result()
                results[model] = result
                print(fmt_line(model, result, name_width))

        # ── Summary ────────────────────────────────────────────────────────
        ok      = [m for m in models if results[m]["status"] == "ok"]
        timed   = [m for m in models if results[m]["status"] == "timeout"]
        failed  = [m for m in models if results[m]["status"] == "error"]

        print()
        print(f"{BOLD}─── Summary ({total} models) ───────────────────────────────────────{RESET}")
        print(f"  {GREEN}✓ OK       {len(ok):>3} / {total}{RESET}")
        if timed:
            print(f"  {YELLOW}⏱ Timeout  {len(timed):>3} / {total}{RESET}  → {', '.join(timed)}")
        if failed:
            print(f"  {RED}✗ Error    {len(failed):>3} / {total}{RESET}  → {', '.join(failed)}")

        # ── Full ordered table ─────────────────────────────────────────────
        print()
        print(f"  {BOLD}{'Model':<{name_width}}  {'Latency':>7}  Status     Response{RESET}")
        print("  " + "─" * (name_width + 46))
        for m in sorted(models):
            r = results[m]
            elapsed = f"{r['elapsed']:5.1f}s"
            if r["status"] == "ok":
                status = f"{GREEN}ok      {RESET}"
                detail = r["content"]
            elif r["status"] == "timeout":
                status = f"{YELLOW}timeout {RESET}"
                detail = ""
            else:
                status = f"{RED}error   {RESET}"
                detail = r["error"][:80]
            print(f"  {m:<{name_width}}  {elapsed}  {status}  {DIM}{detail}{RESET}")

        print()
        sys.exit(0 if not failed and not timed else 1)

    finally:
        if pf_proc and pf_proc.poll() is None:
            pf_proc.send_signal(signal.SIGTERM)


if __name__ == "__main__":
    main()
