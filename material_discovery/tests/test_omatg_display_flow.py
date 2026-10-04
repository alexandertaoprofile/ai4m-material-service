"""Customer request, stages and inline report match saved science without GPU calls."""
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from src.material_workflow.constraints import constraint_from_payload
from src.material_workflow.omatg_chart import stability_number
from src.material_workflow.omatg_pipeline import screening_state
from src.material_workflow.omatg_report import build_plan, build_result_report, build_review_context, result_visuals
from src.material_workflow.requirement_review import merge_review_answer
from src.material_workflow.schemas import ValidationResult
from src.material_workflow.user_requirements import explicit_elements, explicit_property_bounds
from tests import test_omatg_report_readability as readable


class RequestRequirementsTest(TestCase):
    def contract(self, payload):
        with patch.dict('os.environ', {'NEW_MATERIAL_GENERATOR': 'omatg'}):
            return constraint_from_payload(payload)

    def test_explicit_property_bounds_and_units(self):
        bounds = explicit_property_bounds('带隙至少 3000 meV，体积模量不超过 0.2 TPa；介电常数至少 10')
        self.assertEqual(bounds['band_gap'], {'min': 3., 'unit': 'eV'})
        self.assertEqual(bounds['bulk_modulus'], {'max': 200., 'unit': 'GPa'})
        self.assertEqual(bounds['dielectric_constant_x']['min'], 10)

    def test_interval_and_double_bound(self):
        for phrase in ('带隙为 2–5 eV', '带隙至少 2 且不大于 5 eV', '带隙不小于 2 eV，不超过 5 eV'):
            with self.subTest(phrase=phrase):
                self.assertEqual(explicit_property_bounds(phrase)['band_gap'], {'unit': 'eV', 'min': 2., 'max': 5.})

    def test_qualitative_negative_and_unrelated_quantities_are_not_limits(self):
        for phrase in ('带隙尽量高，温度不超过300 K', '不要求带隙至少3 eV', '取消带隙至少3 eV',
                       '带隙不要求超过3 eV', '带隙尽量高，温度在200-300 K', '介电常数越低越好'):
            with self.subTest(phrase=phrase):
                self.assertEqual(explicit_property_bounds(phrase), {})

    def test_invalid_units_and_conflicting_bounds_ask_for_clarification(self):
        for phrase in ('带隙至少3', '带隙至少3 GPa', '带隙至少5 eV，最多2 eV', '带隙大于3 eV且小于3 eV'):
            with self.subTest(phrase=phrase), self.assertRaises(ValueError):
                explicit_property_bounds(phrase)

    def test_natural_required_and_excluded_elements(self):
        self.assertEqual(explicit_elements('Fe Cr Ni 中必须包含 Fe、Cr，禁止包含 Pb'), (['Fe', 'Cr'], ['Pb']))
        self.assertEqual(explicit_elements('必须包含铁和铬，排除铅'), (['Fe', 'Cr'], ['Pb']))
        self.assertEqual(explicit_elements('不要求必须包含 Fe'), ([], []))
        self.assertEqual(explicit_elements('禁止使用金属，必须包含树脂'), ([], []))
        c = self.contract({'taskid': 'rule', 'idea': '在 Fe Cr Ni 中探索晶体，必须包含 Fe、Cr，禁止包含 Pb；带隙至少3 eV'})
        self.assertEqual(c.required_elements, ['Fe', 'Cr'])
        self.assertEqual(c.excluded_elements, ['Pb'])
        self.assertNotIn('Pb', c.allowed_elements)
        self.assertEqual(c.property_constraints['band_gap']['min'], 3)
        self.assertIn('下限 3 eV', build_plan(c))

    def test_explicit_sources_are_tightened_without_mutating_payload(self):
        payload = {'taskid': 'tight', 'idea': 'LiAlO2 带隙至少3 eV', 'new_material': {'property_constraints': {'band_gap': {'min': 2, 'max': 5}}}}
        before = deepcopy(payload)
        c = self.contract(payload)
        self.assertEqual(c.property_constraints['band_gap'], {'min': 3., 'max': 5., 'unit': 'eV'})
        self.assertEqual(payload, before)

    def test_old_history_does_not_impose_numeric_limits(self):
        c = self.contract({'taskid': 'history', 'idea': 'LiAlO2 结构探索', 'history': '用户：之前带隙至少5 eV，必须包含 Ni。'})
        self.assertEqual(c.required_elements, [])
        self.assertEqual(c.property_constraints, {})

    def test_popup_keeps_performance_before_formula_supplement(self):
        payload = {'taskid': 'popup', 'idea': '为芯片封装绝缘层探索新晶体，带隙至少3 eV'}
        merged = merge_review_answer(payload, '化学式：LiAlO2；体积模量至少100 GPa')
        c = self.contract(merged)
        self.assertEqual(c.target_formula, 'LiAlO2')
        self.assertEqual(c.property_constraints['band_gap']['min'], 3)
        self.assertEqual(c.property_constraints['bulk_modulus']['min'], 100)
        self.assertIn('芯片封装', c.task_context['scenario'])
        baseline = build_review_context(payload)
        self.assertIn('芯片封装', baseline)
        for choice in ('化学式', '元素范围', '开放探索'):
            self.assertIn(choice, baseline)

    def test_strict_comparison_is_used_by_screening(self):
        c = self.contract({'taskid': 'strict', 'idea': 'LiAlO2 带隙大于3 eV'})
        v = ValidationResult(candidate_id='c', status='ok', is_valid=True, energy_above_hull=.08,
            metadata={'mattersim': {'relaxation_converged': True}}, property_predictions={'band_gap': {'value': 3.}})
        self.assertEqual(screening_state(v, c)['state'], 'failed')
        v.property_predictions['band_gap']['value'] = 3.001
        self.assertEqual(screening_state(v, c)['state'], 'passed')
        self.assertTrue(screening_state(v, c)['stability_review'])

    def test_popup_preserves_explicit_stability_reference(self):
        payload = merge_review_answer({'taskid': 'hull-popup', 'idea': '探索新晶体，E_hull <= 0.1 eV/atom'}, '化学式：LiAlO2')
        c = self.contract(payload)
        self.assertEqual(c.target_properties['energy_above_hull'], .1)

    def test_only_exclusions_cannot_silently_authorize_open_generation(self):
        with self.assertRaises(ValueError):
            self.contract({'taskid': 'exclude-only', 'idea': '新晶体禁止包含 Pb'})
        c = self.contract({'taskid': 'open-with-rule', 'idea': '开放晶体探索，必须包含 Fe，禁止包含 Pb',
                           'new_material': {'allow_open_chemical_space': True}})
        self.assertEqual(c.allowed_elements, [])
        self.assertEqual(c.required_elements, ['Fe'])
        self.assertEqual(c.excluded_elements, ['Pb'])


class DisplayFlowTest(TestCase):
    def test_worker_value_error_is_a_runtime_failure_not_missing_user_input(self):
        import src.team_config as team
        result = readable.ReadableReportTest().result()
        class Socket:
            client_state = SimpleNamespace(name='CONNECTED')
            async def send_json(self, event): pass
            async def send_text(self, text): pass
        class LLM:
            def __init__(self, **kwargs): pass
        async def waiting(*args, **kwargs): await asyncio.Future()
        async def relay(*args, **kwargs): pass
        with patch.dict('os.environ', {'NEW_MATERIAL_GENERATOR': 'omatg'}), \
             patch.object(team, 'load_config', return_value={'base_url_1': 'test', 'api_key': 'test'}), \
             patch.object(team, 'SeLLM', LLM), patch.object(team, 'resolve_generation_request', return_value=({'taskid': result.taskid}, result.constraints)), \
             patch.object(team, 'stream_discovery_progress', waiting), patch.object(team, 'run_upstream_request', side_effect=ValueError('malformed scientific result')), \
             patch.object(team.InorganicNewMaterialDiscoveryAction, '_stream_authoritative_markdown', staticmethod(relay)):
            with self.assertRaises(team.DiscoveryExecutionError):
                asyncio.run(team.InorganicNewMaterialDiscoveryAction().run({}, Socket(), 'test', result.taskid, []))

    def test_stability_precision_agrees_with_threshold(self):
        for value in (.050002, .05000001, .04999999, .05, .08):
            with self.subTest(value=value):
                self.assertEqual(float(stability_number(value)) > .05, value > .05)
        self.assertEqual(stability_number(None), '—（稳定性结果未记录）')

    def test_requested_property_column_and_inline_image_locations(self):
        result = readable.ReadableReportTest().result()
        result.constraints.validation_targets = {'dielectric_constant_x': None}
        result.validations[0].property_predictions['dielectric_constant_x'] = {'value': 12.4, 'unit': '1'}
        text = build_result_report(result, {'candidate_comparison': '![比较图](/preview/compare.png)', 'candidate_rotation': '![结构图](/preview/rotation.gif)'})
        self.assertIn('介电常数', text.split('#### 4.2')[1].split('#### 4.3')[0])
        self.assertLess(text.index('#### 4.3'), text.index('![比较图]'))
        self.assertLess(text.index('![比较图]'), text.index('### 5.'))
        self.assertLess(text.index('#### 5.1'), text.index('![结构图]'))
        self.assertLess(text.index('![结构图]'), text.index('#### 5.2'))
        self.assertEqual(text.count('![比较图]'), 1)

    def test_existing_publisher_markdown_is_used_without_rewriting_url(self):
        manifest = {'assets': [{'path': '/task/candidate_comparison.png', 'name': '比较图'}]}
        markdown = '![比较图](https://example.invalid/task/candidate_comparison.png)'
        self.assertEqual(result_visuals(manifest, markdown), {'candidate_comparison': markdown})

    def test_failed_property_reason_uses_chinese_label(self):
        result = readable.ReadableReportTest().result(state='failed')
        result.validations[0].metadata['screening']['failures'] = ['性质范围未满足：band_gap']
        text = build_result_report(result)
        self.assertIn('性质范围未满足：带隙', text)
        self.assertNotIn('性质范围未满足：band_gap', text)

    def test_nonconvergence_has_no_passing_recommendation(self):
        result = readable.ReadableReportTest().result(state='incomplete', hull=None)
        result.validations[0].metadata['mattersim']['relaxation_converged'] = False
        text = build_result_report(result)
        self.assertIn('0 个结构优化收敛', text)
        self.assertNotIn('### 5. 优选晶体', text)
        self.assertNotIn('初筛已完成', text)

    def test_same_phase_properties_stage_is_emitted(self):
        import src.material_workflow.presentation as module
        class Socket:
            def __init__(self): self.events = []
            async def send_json(self, event): self.events.append(event)
        socket = Socket()
        async def stop(_seconds):
            if len(socket.events) == 2: raise asyncio.CancelledError
        with patch.dict('os.environ', {'NEW_MATERIAL_GENERATOR': 'omatg'}), \
             patch.object(module, '_phase_from_files', side_effect=[(3, '结构优化', '正在优化原子排布'), (3, '性质评价', '正在运行 ALIGNN')]), \
             patch.object(module, '_write_progress_state'), patch.object(module.asyncio, 'sleep', stop):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(module.stream_discovery_progress(socket, Path('/nonexistent-preview'), 'progress', step_id='FILAMENT_SELECTION_OPTIMIZATION'))
        self.assertIn('ALIGNN', socket.events[-1]['content']['text'])
        self.assertEqual([e['seq'] for e in socket.events], [1, 2])

    def test_completion_follows_inline_report_and_glb(self):
        import src.team_config as team
        result = readable.ReadableReportTest().result()
        result.artifacts['presentation'] = {'assets': [{'name': '比较图', 'path': '/saved/candidate_comparison.png'}, {'name': '旋转图', 'path': '/saved/candidate_rotation.gif'}]}
        class Socket:
            client_state = SimpleNamespace(name='CONNECTED')
            def __init__(self): self.events = []
            async def send_json(self, event): self.events.append(('json', event))
            async def send_text(self, text): self.events.append(('text', text))
        class LLM:
            def __init__(self, **kwargs): pass
        async def waiting(*args, **kwargs): await asyncio.Future()
        async def relay(_llm, websocket, _step, text):
            await websocket.send_text('<<<CONTENT_START:FILAMENT_SELECTION_OPTIMIZATION>>>')
            await websocket.send_text(text)
            await websocket.send_text('<<<CONTENT_END:FILAMENT_SELECTION_OPTIMIZATION>>>')
        async def publish(socket, _result, **kwargs):
            await socket.send_json({'type': 'MaterialsGLB', 'stepId': team.FRONTEND_STEP_ID})
            return '![比较图](https://example.invalid/compare.png)\n\n![旋转图](https://example.invalid/rotation.gif)'
        socket = Socket()
        c = result.constraints
        with patch.dict('os.environ', {'NEW_MATERIAL_GENERATOR': 'omatg'}), \
             patch.object(team, 'load_config', return_value={'base_url_1': 'test', 'api_key': 'test'}), \
             patch.object(team, 'SeLLM', LLM), patch.object(team, 'resolve_generation_request', return_value=({'taskid': c.taskid}, c)), \
             patch.object(team, 'stream_discovery_progress', waiting), patch.object(team, 'run_upstream_request', return_value=result) as worker, \
             patch.object(team, 'emit_presentation_assets', publish), patch.object(team, 'write_terminal_progress') as terminal, \
             patch.object(team.InorganicNewMaterialDiscoveryAction, '_stream_authoritative_markdown', staticmethod(relay)):
            asyncio.run(team.InorganicNewMaterialDiscoveryAction().run({}, socket, 'test', c.taskid, []))
        worker.assert_called_once_with({'taskid': c.taskid}, team.NEW_MATERIAL_RESULTS_ROOT, defer_completion=True)
        glb_index = next(i for i, (kind, event) in enumerate(socket.events) if kind == 'json' and event.get('type') == 'MaterialsGLB')
        last_content = max(i for i, (kind, event) in enumerate(socket.events) if kind == 'text' and 'CONTENT_END' in event)
        self.assertGreater(glb_index, last_content)
        self.assertEqual(socket.events[-1][1]['content']['status'], 'completed')
        terminal.assert_called_once()
        self.assertEqual(sum('<<<CONTENT_START' in event for kind, event in socket.events if kind == 'text'), 2)
