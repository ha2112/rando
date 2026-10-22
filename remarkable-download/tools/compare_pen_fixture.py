"""Verify the frozen Pen fixture and gate reference pixels before comparing ink.

Run from the project root with the project virtualenv. Outputs are disposable;
the only reference PDF is never rewritten. No live cache or tablet is accessed.
"""
import argparse
import hashlib
import json
import logging
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
import rmscene

from rm_sync.parser import DocumentParser, StrokeProcessor
from rm_sync.renderer import PDFRenderer


def sha(data):
    return hashlib.sha256(data).hexdigest()


def poppler(pdf, prefix):
    result = subprocess.run(['pdftoppm', '-r', '144', '-png', str(pdf), str(prefix)],
                            capture_output=True, text=True, check=True)
    return result.stderr, sorted(prefix.parent.glob(prefix.name + '-*.png'))


def pixel_metrics(a, b):
    assert (a.width, a.height, a.n) == (b.width, b.height, b.n)
    # Restrict to painted pixels so white page margins cannot inflate agreement.
    count = changed = total = maximum = 0
    aa, bb = a.samples, b.samples
    for i in range(0, len(aa), 3):
        av, bv = aa[i:i+3], bb[i:i+3]
        if min(av + bv) < 250:
            error = max(abs(x - y) for x, y in zip(av, bv))
            total += sum(abs(x - y) for x, y in zip(av, bv))
            maximum = max(maximum, error)
            changed += error > 16
            count += 1
    return dict(ink_union_pixels=count, pixels_differing_over_16=changed,
                mean_absolute_channel_error_on_ink=total / max(1, count * 3),
                max_channel_error=maximum)


def compare(fixture, output):
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((fixture / 'manifest.json').read_text())
    reference_path = (fixture / manifest['reference_pdf']['path_from_fixture']).resolve()
    for item in manifest['files']:
        data = (fixture / item['path']).read_bytes()
        assert len(data) == item['bytes'] and sha(data) == item['sha256'], item['path']
    original_hash = sha(reference_path.read_bytes())
    assert original_hash == manifest['reference_pdf']['sha256']
    result = dict(input_hashes_verified=20, reference_sha256=original_hash,
                  comparison_dpi=144, versions=dict(pymupdf=fitz.VersionBind))
    audit = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parents[1] /
                           'rm-sync-analysis/unread_data_probe.py'),
                           str(fixture / manifest['document_uuid'])],
                           capture_output=True, text=True, check=True)
    audit_path = output / 'decoder-audit.json'; audit_path.write_text(audit.stdout)
    result['decoder_summary'] = json.loads(audit.stdout)['summary']

    # Investigate the broken resource BEFORE rendering appearance comparisons.
    reference = fitz.open(reference_path)
    repaired = fitz.open(reference_path)
    states = []
    for page in repaired:
        resource = int(repaired.xref_get_key(page.xref, 'Resources')[1].split()[0])
        assert repaired.xref_get_key(resource, 'ExtGState/GSa')[0] == 'null'
        first_state = int(repaired.xref_get_key(resource, 'ExtGState/FXE1')[1].split()[0])
        candidate = first_state - 1
        state = repaired.xref_object(candidate)
        assert all(s in state for s in ('/Type /ExtGState', '/CA 1', '/ca 1', '/SMask /None'))
        assert page.read_contents().startswith(b'/GSa gs ')
        states.append(dict(page=page.number + 1, resource_xref=resource,
                           unused_default_state_xref=candidate, state=state))
        # Test the apparent missing binding on a separate, explicitly named copy.
        repaired.xref_set_key(resource, 'ExtGState/GSa', f'{candidate} 0 R')
    copy = output / 'reference-diagnostic-gsa.pdf'
    repaired.save(copy)
    repaired.close()
    repaired = fitz.open(copy)
    mupdf_equal = []
    for original, diagnostic in zip(reference, repaired):
        a = original.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        b = diagnostic.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        mupdf_equal.append(a.samples == b.samples)
    raw_warnings, raw_images = poppler(reference_path, output / 'reference-original')
    fixed_warnings, fixed_images = poppler(copy, output / 'reference-diagnostic')
    poppler_equal = []
    for a, b in zip(raw_images, fixed_images):
        poppler_equal.append(fitz.Pixmap(str(a)).samples == fitz.Pixmap(str(b)).samples)
    assert len(raw_images) == len(fixed_images) == 9
    assert all(mupdf_equal) and all(poppler_equal)
    assert 'GSa' in raw_warnings and 'GSa' not in fixed_warnings
    result['reference_graphics_state'] = dict(
        missing_binding_pages=states, mupdf_copy_pixels_equal=mupdf_equal,
        poppler_copy_pixels_equal=poppler_equal, original_poppler_warnings=raw_warnings,
        diagnostic_poppler_warnings=fixed_warnings,
        conclusion='Missing binding to apparently orphaned default state; adding it to a '
                   'diagnostic copy changes no pixels at 144 dpi in either engine. '
                   'This does not establish intended alpha semantics or exact revision pairing.')

    # Preserve reference page geometry; never crop/register by the ink bounds.
    processor = StrokeProcessor(); renderer = PDFRenderer()
    pages = DocumentParser(fixture, manifest['document_uuid']).parse_pages()
    assert [p.uuid for p in pages] == [p['page_uuid'] for p in manifest['pages']]
    start = time.perf_counter()
    renderer.build_document(pages, None, processor, lambda page: processor.decode_rm_file(
        fixture / manifest['document_uuid'] / (page.uuid + '.rm')))
    rendered_path = output / 'pen-test-rendered.pdf'
    renderer.save(rendered_path)
    result['render_seconds'] = time.perf_counter() - start
    result['render_bytes'] = rendered_path.stat().st_size
    result['fidelity_warnings'] = renderer.fidelity_warnings
    renderer.close()
    rendered = fitz.open(rendered_path)
    result['page_sizes_pt'] = [list(p.rect)[2:] for p in rendered]
    assert result['page_sizes_pt'] == [p['pdf_size_pt'] for p in manifest['pages']]

    # Fineliner vector measurements provide geometry evidence independent of pixels.
    actual = rendered[1].get_drawings(); expected = repaired[1].get_drawings()
    assert len(actual) == len(expected) == 41
    errors = []; width_errors = []; color_errors = []
    for a, b in zip(actual, expected):
        assert len(a['items']) == len(b['items'])
        width_errors.append(abs(a['width'] - b['width']))
        color_errors.extend(abs(x - y) for x, y in zip(a['color'], b['color']))
        for ai, bi in zip(a['items'], b['items']):
            assert ai[0] == bi[0] == 'l'
            errors.extend(math.hypot(ap.x-bp.x, ap.y-bp.y) for ap, bp in zip(ai[1:], bi[1:]))
    result['fineliner_vectors'] = dict(max_landmark_error_pt=max(errors),
        max_width_error_pt=max(width_errors), max_color_channel_error=max(color_errors))
    assert max(errors) < .001 and max(width_errors) < .00001 and max(color_errors) < .00001
    result['pixel_diagnostics'] = []
    for index, label in ((0, 'ballpoint'), (1, 'fineliner')):
        a = rendered[index].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        b = repaired[index].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        a.save(output / (label + '-rendered.png'))
        b.save(output / (label + '-reference.png'))
        result['pixel_diagnostics'].append(dict(page=index+1, pen=label,
            metrics=pixel_metrics(a, b),
            status='diagnostic only; no strict pixel/alpha acceptance claim'))
    assert sha(reference_path.read_bytes()) == original_hash
    reference.close(); repaired.close(); rendered.close()
    (output / 'comparison.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'test_data/pen_test')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not shutil.which('pdftoppm'):
        parser.error('pdftoppm (Poppler) is required for the independent reference check')
    logging.basicConfig(level=logging.WARNING)
    report = compare(args.fixture.resolve(), args.output.resolve())
    print(json.dumps({k: report[k] for k in ('fineliner_vectors', 'pixel_diagnostics',
                                           'render_seconds', 'render_bytes')}, indent=2))
