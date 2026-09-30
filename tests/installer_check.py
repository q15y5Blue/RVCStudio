"""Exercise Inno with explicit fixture components, then real corrupt archive rejection."""
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'
fixture = BUILD / 'installer-test/Installer-Test-Only.exe'
report = {'component_mode': 'MOCK engine and driver for wizard flow; actual packaged GUI'}


def run_installer(exe, name, extra=()):
    target = (BUILD / name).resolve()
    assert target.is_relative_to(BUILD.resolve()) and not target.exists()
    log = BUILD / (name + '.log')
    result = subprocess.run([str(exe), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
        '/NOICONS', '/TASKS=', '/DIR=' + str(target), '/TESTDATA=' + str(BUILD / (name + '-data')),
        '/LOG=' + str(log), *extra], timeout=120)
    return target, result.returncode, log


target, code, log = run_installer(fixture, 'integrated-flow-success')
assert code == 0, (code, str(log))
assert (target / 'RVCStudio.exe').is_file()
assert (target / 'RVCSetupHelper.exe').is_file()
env = os.environ.copy()
env['RVC_STUDIO_DATA'] = str(BUILD / 'installed-v02-smoke-data')
smoke = BUILD / 'installed-v02-smoke.json'
result = subprocess.run([str(target / 'RVCStudio.exe'), '--smoke-test', str(smoke)],
    cwd=r'C:\Windows\System32', env=env, timeout=30)
assert result.returncode == 0 and json.loads(smoke.read_text(encoding='utf-8'))['driver_bundled']
report['fixture_success'] = {'exit': code, 'installed_gui_smoke': 'passed'}
# Only uninstall this test's exact installation, contained within the workspace build directory.
assert target.is_relative_to(BUILD.resolve()) and target.name == 'integrated-flow-success'
result = subprocess.run([str(target / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], timeout=60)
for _ in range(50):
    if not (target / 'RVCStudio.exe').exists():
        break
    time.sleep(.1)
assert result.returncode == 0 and not (target / 'RVCStudio.exe').exists()
report['fixture_uninstall'] = {'exit': result.returncode, 'app_removed': True}

target, code, log = run_installer(fixture, 'integrated-flow-failure', ['/ENGINEARCHIVE=fixture-error'])
assert code != 0 and not (target / 'RVCStudio.exe').exists()
assert '50%' in log.read_text(encoding='utf-8-sig')
report['fixture_failure'] = {'exit': code, 'app_not_installed': True, 'unicode_percent_error': 'passed'}

bad = BUILD / 'invalid-engine.zip'
bad.write_bytes(b'test: intentionally invalid official runtime archive')
target, code, log = run_installer(ROOT / 'dist/RVCStudio-Setup-0.2.0-online.exe',
    'shipping-invalid-archive', ['/ENGINEARCHIVE=' + str(bad)])
assert code != 0 and not (target / 'RVCStudio.exe').exists()
assert 'ZIP' in log.read_text(encoding='utf-8-sig')
report['shipping_corrupt_archive'] = {'exit': code, 'app_not_installed': True, 'real_helper': True}
(BUILD / 'integrated-installer-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
