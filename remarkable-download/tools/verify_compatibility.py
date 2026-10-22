"""Render the two frozen compatibility fixtures without changing the live cache."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from typer.testing import CliRunner
from rm_sync.cli import app


def verify(fixture, output):
    manifest = json.loads((fixture / 'manifest.json').read_text())
    for item in manifest['files']:
        data = (fixture / item['path']).read_bytes()
        assert len(data) == item['bytes']
        assert hashlib.sha256(data).hexdigest() == item['sha256'], item['path']
    for doc in manifest['documents']:
        reference = fixture / doc['reference']['path_from_fixture']
        assert hashlib.sha256(reference.read_bytes()).hexdigest() == doc['reference']['sha256']

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        # Rendering state is written here; original inputs stay untouched.
        for item in manifest['files']:
            target = cache / item['path']; target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(fixture / item['path'])
        start = time.perf_counter()
        result = CliRunner().invoke(app, ['render', '--cache', str(cache), '--output', str(output)])
        print(result.output)
        assert result.exit_code == 0, repr(result.exception)
        state = json.loads((cache / '.rm_render_state.json').read_text())
        report = dict(cli_exit_code=result.exit_code, seconds=time.perf_counter()-start,
                      fixture_files_verified=len(manifest['files']), documents=[])
        for doc in manifest['documents']:
            with fitz.open(output / (doc['name'] + '.pdf')) as rendered, \
                 fitz.open(fixture / doc['reference']['path_from_fixture']) as reference:
                assert len(rendered) == len(reference) == doc['reference']['page_count']
                entry = dict(name=doc['name'], pages=len(rendered),
                             fidelity=state[doc['uuid']]['fidelity'])
                if (fixture / (doc['uuid'] + '.pdf')).exists():
                    equal = sum(a.get_pixmap().samples == b.get_pixmap().samples
                                for a, b in zip(rendered, reference))
                    assert equal == len(rendered)
                    entry.update(equal_pages_at_72_dpi=equal,
                        unsupported_blocks=state[doc['uuid']]['fidelity_warnings'][0]['unsupported_blocks'])
                else:
                    # Grid background is absent from our renderer; compare handwriting vectors.
                    expected = [p for p in reference[0].get_drawings() if p['width'] > .2]
                    actual = rendered[0].get_drawings()
                    assert len(expected) == len(actual) == 242
                    errors = []
                    for a, b in zip(actual, expected):
                        assert len(a['items']) == len(b['items'])
                        assert abs(a['width'] - b['width']) < .00001
                        for ai, bi in zip(a['items'], b['items']):
                            assert ai[0] == bi[0] == 'l'
                            errors.extend(abs(ap - bp) for ap, bp in zip(ai[1:], bi[1:]))
                    assert max(errors) < .001
                    entry.update(first_page_ink_paths=len(actual), max_landmark_error_pt=max(errors),
                        page_size_differences=[dict(page=i+1, actual=list(a.rect)[2:],
                                                   reference=list(b.rect)[2:])
                            for i, (a, b) in enumerate(zip(rendered, reference)) if a.rect != b.rect])
                report['documents'].append(entry)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'compatibility-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'test_data/compatibility')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.fixture.resolve(), args.output.resolve()), indent=2))
