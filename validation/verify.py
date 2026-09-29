"""Validate independently submitted source commits and their original failures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET

name = sys.argv[1]
meta = json.loads((Path(__file__).parent / 'cases.json').read_text())[name]
work = Path(sys.argv[2]).resolve()
evidence = Path(sys.argv[3]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
python = sys.executable
source = work / meta['source']
fixed = source.read_bytes()
summary = {}
expected_focused = 11 if name == 'population' else 16
expected_failures = 7 if name == 'population' else 9


def run(label, command, expected=0, cwd=work, timeout=300):
    env = dict(os.environ, PYTHONPATH=str(cwd))
    with (evidence / (label + '.log')).open('w') as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log,
                                stderr=subprocess.STDOUT, timeout=timeout)
    output = (evidence / (label + '.log')).read_text(errors='replace')
    print(label, result.returncode, output[-500:], flush=True)
    assert result.returncode == expected, (label, output[-8000:])
    return output


def test(label, files, expected=0, cwd=work, coverage=False):
    command = [python, '-m', 'pytest', '-n', '0', *files, '-q', '--tb=short',
               '--junitxml=' + str(evidence / (label + '.xml'))]
    if coverage:
        command += ['--cov=meltingpot', '--cov-branch',
                    '--cov-report=json:' + str(evidence / 'coverage.json')]
    run(label, command, expected=expected, cwd=cwd)
    suites = list(ET.parse(evidence / (label + '.xml')).getroot().iter('testsuite'))
    counts = [sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')]
    summary[label] = counts
    print(label, counts, flush=True)
    return counts


assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=work, text=True).strip() == meta['head']
run('versions', [python, '-m', 'pip', 'freeze'])
run('dependency-check', [python, '-m', 'pip', 'check'])
original = subprocess.check_output(['git', 'show', meta['base'] + ':' + meta['source']], cwd=work)
try:
    assert test('fixed-focused', [meta['test']], coverage=True) == [expected_focused, 0, 0, 0]
    assert test('fixed-utilities', ['meltingpot/utils']) == [306 + expected_focused, 0, 0, 0]
    source.write_bytes(original)
    assert test('original-focused', [meta['test']], expected=1) == [expected_focused, expected_failures, 0, 0]
    assert test('original-utilities', ['meltingpot/utils', '--ignore=' + meta['test']]) == [306, 0, 0, 0]
finally:
    source.write_bytes(fixed)
assert test('restored-focused', [meta['test']]) == [expected_focused, 0, 0, 0]
run('source-restoration', ['git', 'diff', '--exit-code'])
run('patch-check', ['git', 'diff', '--check', meta['base']])
run('format-tests', [python, '-m', 'pyink', '--target-version', 'py311', '--check', meta['test']])
run('pylint', [python, '-m', 'pylint', '--errors-only', meta['source'], meta['test']])
run('ruff', [python, '-m', 'ruff', 'check', '--isolated', '--select', 'E9,F63,F7,F82', meta['source'], meta['test']])
run('compile', [python, '-m', 'compileall', '-q', meta['source'], meta['test']])
if sys.version_info[:2] == (3, 11):
    run('pytype', [python, '-m', 'pytype', '-j', '2', '--output', str(evidence / 'pytype'), meta['source'], meta['test']], timeout=300)
with tempfile.TemporaryDirectory() as temporary:
    exported = Path(temporary) / 'source'
    exported.mkdir()
    archive = Path(temporary) / 'source.tar'
    subprocess.run(['git', 'archive', '--format=tar', '-o', str(archive), meta['head']], cwd=work, check=True)
    with tarfile.open(archive) as tar:
        tar.extractall(exported, filter='data')
    run('exported-import', [python, '-c', 'import meltingpot; from pathlib import Path; p=Path(meltingpot.__file__).resolve(); print(p); assert p.is_relative_to(Path.cwd())'], cwd=exported)
    assert test('exported-utilities', ['meltingpot/utils'], cwd=exported) == [306 + expected_focused, 0, 0, 0]
(evidence / 'summary.json').write_text(json.dumps(summary, indent=2))
print('VERIFIED', name, meta['head'], flush=True)
