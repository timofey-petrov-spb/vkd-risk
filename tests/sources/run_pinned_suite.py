"""Full suite with stable app input transports; source tests still exercise A4.

Run from the repository root: python tests/sources/run_pinned_suite.py
This does not fix the application's live diagnostics in historical snapshots.
"""
from datetime import datetime, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    import pytest
    from vkd.sources import Fetch, tle_latest
    now = datetime(2026, 9, 18, 22, 30, tzinfo=timezone.utc)
    off = Fetch('offline_test', False, False, None, None,
                'Нет наблюдений: закреплённый тестовый вход', None, None)
    with TemporaryDirectory() as folder:
        tle, fetch = tle_latest(disabled=True, now=now, cache_dir=folder)
        if tle is None:
            raise RuntimeError('Pinned TLE fixture has changed or failed validation')
        class PinAppInputs:
            @pytest.fixture(autouse=True)
            def stable_app_inputs(self, request):
                # Deployment tests exercise the real guard with their own hanging
                # transport/DNS. A global patch would bypass exactly that code.
                if request.node.path.name == 'test_deploy_fetch_guard.py':
                    yield
                    return
                with patch('app.fetch_guard.goes_latest', return_value=(None, {}, off)), \
                     patch('app.fetch_guard.kp_latest', return_value=(None, {}, off)), \
                     patch('app.fetch_guard.noaa_latest', return_value=((), {}, off)), \
                     patch('app.fetch_guard.tle_latest', return_value=(tle, fetch)):
                    yield
        return pytest.main(['-q', *sys.argv[1:]], plugins=[PinAppInputs()])



if __name__ == '__main__':
    raise SystemExit(main())
