"""Serve the production frontend with the existing API in an isolated mock workspace.

Usage: python frontend/tests/serve-visitor.py --port 8765
Temporary databases and generated test configuration are removed on exit.
No provider keys or real contracts are used. The real review pipeline runs in
its existing DSH_FORCE_MOCK mode; model quality is outside this UI check.
"""
import argparse
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix='cra-visitor-ui-') as directory:
        os.environ.update({
            'REVIEW_RUNS_PATH': str(Path(directory) / 'runs.db'),
            'CONTRACT_REVIEW_SETTINGS': str(Path(directory) / 'settings.json'),
            'DSH_FORCE_MOCK': '1', 'APP_ENV': 'development',
            'CRA_API_KEYS': '{}', 'CRA_VISITOR_SIGNING_SECRET': secrets.token_urlsafe(32),
            'CRA_VISITOR_UPLOAD_LIMIT': '20', 'DEEPSEEK_API_KEY': '', 'SILICONFLOW_API_KEY': '',
        })
        from app import api
        import uvicorn

        # The upload endpoint checks provider availability before entering the
        # forced-mock pipeline. Supply only that test probe, never a real key.
        api.active_llm_config = lambda *_: {'model': 'ui-test-mock'}
        real_pipeline = api.run_pipeline

        def observable_pipeline(*args, **kwargs):
            # Leave enough time to verify progress and refresh recovery.
            time.sleep(8)
            return real_pipeline(*args, **kwargs)

        api.run_pipeline = observable_pipeline
        print(f'Isolated visitor UI: http://127.0.0.1:{args.port}', flush=True)
        uvicorn.run(api.app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
