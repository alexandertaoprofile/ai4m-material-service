"""Preview four existing scientific cases; no generation, upload or live writes."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--image-root', required=True)
    parser.add_argument('--render-python', required=True)
    parser.add_argument('--skip-render', action='store_true', help='Reuse previously rendered assets')
    parser.add_argument('--comparison-only', action='store_true', help='Refresh the comparison PNG, reuse existing rotation and 3D assets')
    args = parser.parse_args()
    service = Path(__file__).resolve().parents[1]
    live_results = Path('/data/se42/alpha_project/material_service_hub/material_discovery/src/MNS_CaseHub/cases/material_discovery_demo/results/new_material')
    cases = {
        'CSP': live_results / 'omatg-live-csp-smoke-20261001-01',
        'DNG': live_results / 'omatg-live-dng-smoke-20261001-10',
        'pending': live_results / 'omatg-live-dng-smoke-20261001-01',
        'open': Path('/data/se42/omatg_local/1115_shadow_diversity_20260929/results/output-open-s42-full-20260929-r01'),
    }
    environment = {**os.environ, 'OPENAI_API_KEY': 'test', 'NEW_MATERIAL_GENERATOR': 'omatg',
        'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': '/data/mattersim_tmp', 'MPLCONFIGDIR': str(args.output_root / 'matplotlib-cache')}
    for label, source in cases.items():
        output = args.output_root / label
        assets = output / 'assets'
        output.mkdir(parents=True, exist_ok=True)
        manifest = source / 'new_material_pipeline_manifest.json'
        original = manifest.read_bytes()
        if not args.skip_render:
            renderer = 'preview_omatg_chart.py' if args.comparison_only else 'render_new_material_assets.py'
            subprocess.run([args.render_python, str(service / 'tools' / renderer),
                '--manifest', str(manifest), '--output-dir', str(assets)], env=environment, check=True)
        subprocess.run([sys.executable, str(service / 'tools/preview_saved_omatg.py'), '--result-dir', str(source),
            '--output-dir', str(output), '--asset-dir', str(assets), '--image-root', args.image_root + '/' + label + '/assets'], env=environment, check=True)
        subprocess.run([sys.executable, str(service / 'tools/verify_omatg_readable_report.py'),
            '--result-dir', str(source), '--preview-dir', str(output)], env=environment, check=True)
        assert manifest.read_bytes() == original
        plan = (output / 'calculation_plan.md').read_text(encoding='utf-8')
        report = (output / 'frontend_preview.md').read_text(encoding='utf-8')
        (output / 'user_flow_preview.md').write_text(plan + '\n\n---\n\n' + report, encoding='utf-8')
        print(f'Preview ready: {label}', flush=True)
    sys.path.insert(0, str(service))
    from src.material_workflow.omatg_report import build_review_context
    prompt = {'idea': '为芯片封装绝缘层探索新的无机晶体，带隙至少 3 eV'}
    popup = build_review_context(prompt) + '\n\n请选择一种起点填写：\n\n'
    popup += '- 化学式：LiAlO2；带隙至少 3 eV。\n- 元素：Li Al O；必须包含 Li 和 O。\n- 开放探索：为芯片封装绝缘层寻找新晶体，带隙至少 3 eV。\n'
    (args.output_root / 'popup_preview.md').write_text(popup, encoding='utf-8')


if __name__ == '__main__':
    main()
