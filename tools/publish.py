"""Relay a reviewed public installer; never request access to the source repository."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile

def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)

manifest = json.loads(Path('release-manifest.json').read_text())
version = manifest['version']; tag = 'v' + version
repo = os.environ['GH_REPO']
assert repo == 'hilltahuang/amiabot-downloads'
url = os.environ['TRANSFER_URL']; parsed = urlparse(url)
assert parsed.scheme == 'https' and not parsed.username and parsed.port in (443, None)
assert parsed.hostname and parsed.hostname.endswith(('.blob.core.windows.net', '.actions.githubusercontent.com'))
# The ticket only serves this already reviewed installer archive. Do not log it.
archive = Path('source.zip'); digest = hashlib.sha256(); total = 0
with urlopen(url, timeout=120) as response, archive.open('wb') as file:
    assert response.status == 200
    while chunk := response.read(1024 * 1024):
        total += len(chunk); assert total <= 240 * 1024 * 1024
        digest.update(chunk); file.write(chunk)
assert digest.hexdigest() == manifest['archiveSha256']
out = Path('release-files'); out.mkdir()
with zipfile.ZipFile(archive) as source:
    meta = json.loads(source.read('artifacts/native/windows.json'))
    smoke = json.loads(source.read('artifacts/native/windows-smoke.json'))
    assert meta['commit'] == manifest['sourceCommit'] and meta['dirty'] is False and meta['channel'] == 'unsigned'
    assert smoke['commit'] == meta['commit']
    for key in ['installation', 'webview2NativeSignIn', 'offlineEntry', 'normalClose', 'uninstall']:
        assert smoke[key] == 'passed'
    assert len(meta['files']) == 1
    entry = meta['files'][0]
    assert entry['bytes'] == manifest['bytes'] and entry['sha256'] == manifest['sha256']
    data = source.read(entry['path'])
    assert len(data) == manifest['bytes'] and hashlib.sha256(data).hexdigest() == manifest['sha256']
    (out / manifest['name']).write_bytes(data)
checksum = f'amiabot-{version}-windows-SHA256SUMS.txt'
(out / checksum).write_text(manifest['sha256'] + '  ' + manifest['name'] + '\n')
bundle = out / f'amiabot-{version}-windows-x64.zip'
with zipfile.ZipFile(bundle, 'w') as package:
    for name, data in [(manifest['name'], (out / manifest['name']).read_bytes()),
                       (checksum, (out / checksum).read_bytes()),
                       ('INSTALL.txt', Path('windows-install.txt').read_bytes())]:
        info = zipfile.ZipInfo(name, (2026, 11, 9, 0, 0, 0))
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o100644 << 16
        package.writestr(info, data)
bundle_checksum = out / f'amiabot-{version}-windows-zip-SHA256SUMS.txt'
bundle_checksum.write_text(hashlib.sha256(bundle.read_bytes()).hexdigest() + '  ' + bundle.name + '\n')

title = f'amiabot {version} — official free downloads'
releases = json.loads(gh('api', f'repos/{repo}/releases'))
candidates = [r for r in releases if r['tag_name'] == tag or (r['draft'] and r['name'] == title)]
assert len(candidates) <= 1
request = {'tag_name': tag, 'name': title, 'body': Path('release-notes.md').read_text(), 'draft': True,
           'target_commitish': os.environ['GITHUB_SHA']}
Path('release-request.json').write_text(json.dumps(request))
if candidates:
    release = candidates[0]
    if release['draft']:
        release = json.loads(gh('api', '--method', 'PATCH', f'repos/{repo}/releases/{release["id"]}', '--input', 'release-request.json'))
    else:
        assert os.environ.get('BUNDLE_ONLY') == 'true', 'Existing releases only accept an explicitly requested verified bundle.'
else:
    release = json.loads(gh('api', '--method', 'POST', f'repos/{repo}/releases', '--input', 'release-request.json'))
assets = {a['name']: a for a in json.loads(gh('api', f'repos/{repo}/releases/{release["id"]}/assets'))}
for file in sorted(out.iterdir()):
    digest = 'sha256:' + hashlib.sha256(file.read_bytes()).hexdigest()
    if file.name in assets:
        a = assets[file.name]
        assert a['state'] == 'uploaded' and a['size'] == file.stat().st_size and a.get('digest') == digest
        continue
    if not release['draft']:
        assert file.name in (bundle.name, bundle_checksum.name), 'Published installers cannot be replaced.'
    gh('release', 'upload', tag, str(file))
assets = {a['name']: a for a in json.loads(gh('api', f'repos/{repo}/releases/{release["id"]}/assets'))}
for file in out.iterdir():
    a = assets[file.name]
    assert a['state'] == 'uploaded' and a['size'] == file.stat().st_size
    assert a['digest'] == 'sha256:' + hashlib.sha256(file.read_bytes()).hexdigest()
# An earlier browser draft may contain completed Android mirrors. Retain only
# independently verified complete assets; never expose an incomplete transfer.
for name, a in assets.items():
    if a['state'] != 'uploaded':
        # Discard only a failed browser transfer in this unpublished draft.
        # Completed public files are never replaced by this workflow.
        assert release['draft'] and name in manifest['optionalMirrors']
        gh('api', '--method', 'DELETE', f'repos/{repo}/releases/assets/{a["id"]}')
        continue
    if name not in {p.name for p in out.iterdir()}:
        expected = manifest['optionalMirrors'].get(name)
        assert expected and a['size'] == expected['bytes'] and a['digest'] == 'sha256:' + expected['sha256']
if release['draft']:
    Path('release-publish.json').write_text(json.dumps({'draft': False, 'make_latest': 'true'}))
    release = json.loads(gh('api', '--method', 'PATCH', f'repos/{repo}/releases/{release["id"]}', '--input', 'release-publish.json'))
assert not release['draft']
print(release['html_url'])
