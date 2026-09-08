"""Browser smoke test against an isolated synthetic workspace (agent-browser CLI).

Build frontend first, then run: python scripts/check_collaboration_ui.py
All database files are temporary. No LLM or external notification is used.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]


def serve(port):
    sys.path.insert(0, str(ROOT))
    from app import api
    import uvicorn

    run = api.review_run_store.create_run(contract_type="purchase")
    api.review_run_store.complete_run(run["id"], {
        "contract": {"name": "合成浏览器测试报告", "type": "purchase"},
        "risks": [{"id": "synthetic-risk", "riskType": "合成风险", "severity": "low",
                   "clauseQuote": "合成条款", "evidence": "合成说明", "legalBasis": "合成审查依据"}],
        "meta": {"mock": True}, "summary": {"total": 1},
    }, elapsed_ms=0, stage_times=[0]*4)
    api.review_run_store.set_disposition(run["id"], "synthetic-risk", "rejected", "合成处置理由：补充条款已覆盖")
    uvicorn.run(api.app, host="127.0.0.1", port=port, log_level="error")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--assets", action="store_true", help="verify asset import, search and obligations instead")
    parser.add_argument("--browser-bin")
    parser.add_argument("--screenshot", help="Optional output PNG for the synthetic workflow")
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
        return
    binary = args.browser_bin or shutil.which("agent-browser")
    if os.name == "nt" and (not binary or str(binary).endswith((".ps1", ".cmd"))):
        candidate = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/agent-browser/bin/agent-browser-win32-x64.exe"
        if candidate.exists():
            binary = str(candidate)
    if not binary:
        raise SystemExit("agent-browser is required; supply --browser-bin or install its CLI")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    session = "cra-collab-test-" + uuid.uuid4().hex[:10]

    def browser(*command):
        print(f"Browser: {command[0]}", flush=True)
        # Browser daemons can inherit pipe handles on Windows. Files avoid
        # waiting for EOF from a child that intentionally stays alive.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen([binary, "--session", session, "--json", *command], stdout=stdout, stderr=stderr)
            try:
                process.wait(timeout=40)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise AssertionError(f"Browser step {command[0]} timed out")
            stdout.seek(0); stderr.seek(0)
            output = stdout.read().decode("utf-8", errors="replace")
            errors = stderr.read().decode("utf-8", errors="replace")
        if process.returncode:
            raise AssertionError(f"Browser step {command[0]} failed: {output} {errors}")
        result = json.loads(output)
        if not result.get("success"):
            raise AssertionError(result)
        return result.get("data")

    def ready():
        browser("wait", "--load", "networkidle")
        return browser("snapshot", "-i")

    def action(*command):
        browser(*command)
        ready()

    with tempfile.TemporaryDirectory(prefix="cra-collaboration-ui-") as directory:
        env = {**os.environ, "REVIEW_RUNS_PATH": str(Path(directory) / "runs.db"),
               "CONTRACT_REVIEW_SETTINGS": str(Path(directory) / "settings.json"),
               "DSH_FORCE_MOCK": "1", "APP_ENV": "development", "DEEPSEEK_API_KEY": "", "SILICONFLOW_API_KEY": ""}
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve", str(port)], cwd=ROOT, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    with urlopen(url + "/health", timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if process.poll() is not None:
                        raise AssertionError("test server failed to start")
                    time.sleep(0.1)
            else:
                raise AssertionError("test server did not become ready")
            with urlopen(url + "/review-runs") as response:
                run_id = json.load(response)["runs"][0]["id"]
            browser("open", url)
            browser("wait", ".nav")
            browser("errors", "--clear")
            tree = ready()
            if args.assets:
                action("find", "role", "button", "click", "--name", "合同资产")
                source = Path(directory) / "synthetic-asset.txt"
                source.write_text("合成采购合同\n甲方：合成企业\n合同金额：100元\n付款期限为三十日。", encoding="utf-8")
                action("upload", '[data-testid="asset-files"]', str(source))
                action("fill", '[data-testid="asset-question"]', "付款期限")
                action("click", '[data-testid="asset-ask"]')
                evidence = browser("snapshot")
                assert "付款期限为三十日" in json.dumps(evidence, ensure_ascii=False), evidence
                action("fill", '[data-testid="obligation-title"]', "合成续签事项")
                action("eval", "const input = document.querySelector('[data-testid=obligation-due]'); input.value = '2020-01-01T09:00'; input.dispatchEvent(new Event('input', { bubbles: true }));")
                action("click", '[data-testid="add-obligation"]')
                evidence = browser("snapshot")
                assert "合成续签事项" in json.dumps(evidence, ensure_ascii=False), evidence
                with urlopen(url + "/contract-assets/notifications") as response:
                    assert len(json.load(response)["notifications"]) == 1
                action("click", '[data-testid="asset-review"]')
                evidence = browser("snapshot")
                assert "已读取" in json.dumps(evidence, ensure_ascii=False), evidence
                errors = browser("errors")
                assert not (errors or {}).get("errors"), errors
                if args.screenshot:
                    Path(args.screenshot).resolve().parent.mkdir(parents=True, exist_ok=True)
                    browser("screenshot", str(Path(args.screenshot).resolve()), "--full")
                print("PASS: asset import -> cited search -> obligation -> inbox reminder -> review workbench")
                return
            assert "审查协作" in json.dumps(tree, ensure_ascii=False), "Missing collaboration navigation"
            action("find", "role", "button", "click", "--name", "审查协作")
            action("fill", '[data-testid="request-title"]', "合成浏览器协作事项")
            action("click", '[data-testid="create-request"]')
            action("click", '[data-testid="submit-request"]')
            action("select", '[data-testid="assignee"]', "local-developer")
            action("select", '[data-testid="approver"]', "local-developer")
            # Use the form's visible default (three days ahead). Native browser
            # datetime-local controls do not reliably support CLI text filling.
            action("click", '[data-testid="assign-request"]')
            action("select", '[data-testid="run-id"]', run_id)
            action("click", '[data-testid="bind-run"]')
            action("fill", '[data-testid="comment-body"]', "合成评论：请复核此报告。")
            action("click", '[data-testid="add-comment"]')
            action("click", '[data-testid="request-approval"]')
            action("click", "article details.action-block > summary")
            evidence = browser("snapshot")
            assert "合成处置理由：补充条款已覆盖" in json.dumps(evidence, ensure_ascii=False), evidence
            assert "合成审查依据" in json.dumps(evidence, ensure_ascii=False), evidence
            action("fill", '[data-testid="decision-reason"]', "合成审批：依据已核对。")
            action("click", '[data-testid="approve-request"]')
            status = browser("get", "text", '[data-testid="request-status"]')
            assert "已批准" in json.dumps(status, ensure_ascii=False), status
            if args.screenshot:
                Path(args.screenshot).resolve().parent.mkdir(parents=True, exist_ok=True)
                browser("screenshot", str(Path(args.screenshot).resolve()), "--full")
            with urlopen(url + "/collaboration/requests?status=approved") as response:
                requests = json.load(response)["requests"]
            assert len(requests) == 1
            with urlopen(url + "/collaboration/requests/" + requests[0]["id"]) as response:
                request = json.load(response)
            assert request["approvalSnapshot"]["runId"] == run_id
            errors = browser("errors")
            assert not (errors or {}).get("errors"), errors
            print("PASS: create -> submit -> assign/SLA -> bind report -> comment -> approval; frozen report verified")
        except Exception:
            print(json.dumps(browser("snapshot"), ensure_ascii=False), flush=True)
            print(json.dumps(browser("errors"), ensure_ascii=False), flush=True)
            if args.screenshot:
                Path(args.screenshot).resolve().parent.mkdir(parents=True, exist_ok=True)
                browser("screenshot", str(Path(args.screenshot).resolve()), "--full")
            raise
        finally:
            try:
                browser("close")
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
