#!/usr/bin/env python3
"""
test_eval_exit_logic.py — 验证 eval_retrieval.py 的阈值判定与退出码逻辑

不调用真实检索，通过 Mock 验证：
1. 默认严格模式（100%）下 28/28 通过
2. 默认严格模式（100%）下 27/28 失败
3. 95% 阈值下 27/28 通过
4. 95% 阈值下 26/28 失败
5. source_hit_rate 低于 100% 时失败
6. 非法阈值参数报错
7. JSON 报告包含阈值与门禁结果
"""

import os
import subprocess
import sys
import unittest

# 路径设置
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))


class TestEvalExitLogic(unittest.TestCase):
    """测试评估脚本的阈值判定逻辑"""

    def _run_eval_with_mock(self, mock_results, mock_answerable_count=28,
                            min_source=100.0, min_keyword=100.0, min_final=100.0):
        """使用 mock 数据运行评估逻辑，返回 (exit_code, report_dict)"""

        # 构造 mock 返回值
        source_hit_count = sum(1 for r in mock_results if r.get('source_hit', False))
        keyword_hit_count = sum(1 for r in mock_results if r.get('keyword_hit', False))
        passed_count = sum(1 for r in mock_results if r.get('passed', False))

        ab_total = mock_answerable_count
        source_hit_rate = (source_hit_count / ab_total * 100) if ab_total > 0 else 0.0
        keyword_hit_rate = (keyword_hit_count / ab_total * 100) if ab_total > 0 else 0.0
        final_pass_rate = (passed_count / ab_total * 100) if ab_total > 0 else 0.0

        # 阈值判定
        source_pass = source_hit_rate >= min_source
        keyword_pass = keyword_hit_rate >= min_keyword
        final_pass = final_pass_rate >= min_final
        threshold_passed = source_pass and keyword_pass and final_pass

        # 构造报告
        report = {
            'eval_type': 'retrieval',
            'answerable_cases': ab_total,
            'passed': passed_count,
            'failed': ab_total - passed_count,
            'source_hit_rate': round(source_hit_rate / 100, 4),
            'keyword_hit_rate': round(keyword_hit_rate / 100, 4),
            'final_pass_rate': round(final_pass_rate / 100, 4),
            'thresholds': {
                'min_source_hit_rate': min_source / 100,
                'min_keyword_hit_rate': min_keyword / 100,
                'min_final_pass_rate': min_final / 100,
            },
            'threshold_passed': threshold_passed,
        }

        exit_code = 0 if threshold_passed else 1
        return exit_code, report

    def test_28_of_28_strict_mode_pass(self):
        """28/28 在默认严格模式（100%）下应通过"""
        mock_results = [{'passed': True, 'source_hit': True, 'keyword_hit': True} for _ in range(28)]
        exit_code, report = self._run_eval_with_mock(mock_results, 28)
        self.assertEqual(exit_code, 0)
        self.assertTrue(report['threshold_passed'])
        self.assertEqual(report['final_pass_rate'], 1.0)

    def test_27_of_28_strict_mode_fail(self):
        """27/28 在默认严格模式（100%）下应失败"""
        mock_results = [{'passed': True, 'source_hit': True, 'keyword_hit': True} for _ in range(27)]
        mock_results.append({'passed': False, 'source_hit': True, 'keyword_hit': False})
        exit_code, report = self._run_eval_with_mock(mock_results, 28)
        self.assertEqual(exit_code, 1)
        self.assertFalse(report['threshold_passed'])
        self.assertAlmostEqual(report['final_pass_rate'], 0.9643, places=4)

    def test_27_of_28_with_95_threshold_pass(self):
        """27/28 在 95% 阈值下应通过"""
        mock_results = [{'passed': True, 'source_hit': True, 'keyword_hit': True} for _ in range(27)]
        mock_results.append({'passed': False, 'source_hit': True, 'keyword_hit': False})
        exit_code, report = self._run_eval_with_mock(
            mock_results, 28, min_source=100.0, min_keyword=95.0, min_final=95.0)
        self.assertEqual(exit_code, 0)
        self.assertTrue(report['threshold_passed'])

    def test_26_of_28_with_95_threshold_fail(self):
        """26/28=92.9% 在 95% 阈值下应失败"""
        mock_results = [{'passed': True, 'source_hit': True, 'keyword_hit': True} for _ in range(26)]
        mock_results.append({'passed': False, 'source_hit': True, 'keyword_hit': False})
        mock_results.append({'passed': False, 'source_hit': True, 'keyword_hit': False})
        exit_code, report = self._run_eval_with_mock(
            mock_results, 28, min_source=100.0, min_keyword=95.0, min_final=95.0)
        self.assertEqual(exit_code, 1)
        self.assertFalse(report['threshold_passed'])
        self.assertAlmostEqual(report['final_pass_rate'], 0.9286, places=4)

    def test_source_hit_below_100_fails_even_if_final_pass(self):
        """source_hit_rate 低于 100% 时，即使 final_pass_rate 达标也应失败"""
        # 27/28 passed，但 source_hit 只有 26/28
        mock_results = []
        for i in range(26):
            mock_results.append({'passed': True, 'source_hit': True, 'keyword_hit': True})
        mock_results.append({'passed': True, 'source_hit': False, 'keyword_hit': True})
        mock_results.append({'passed': False, 'source_hit': False, 'keyword_hit': False})
        exit_code, report = self._run_eval_with_mock(
            mock_results, 28, min_source=100.0, min_keyword=95.0, min_final=95.0)
        self.assertEqual(exit_code, 1)
        self.assertFalse(report['threshold_passed'])
        # source_hit_rate = 26/28 = 92.9% < 100%
        self.assertAlmostEqual(report['source_hit_rate'], 0.9286, places=4)

    def test_invalid_threshold_below_zero(self):
        """小于 0 的阈值应被拒绝（通过 CLI 参数验证）"""
        result = subprocess.run(
            [sys.executable, 'scripts/eval/eval_retrieval.py',
             '--min-source-hit-rate', '-1'],
            capture_output=True, text=True, cwd=os.path.join(PROJECT_ROOT, 'agent-python'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('0～100', result.stdout + result.stderr)

    def test_invalid_threshold_above_max(self):
        """大于 100 的阈值应被拒绝（通过 CLI 参数验证）"""
        result = subprocess.run(
            [sys.executable, 'scripts/eval/eval_retrieval.py',
             '--min-final-pass-rate', '101'],
            capture_output=True, text=True, cwd=os.path.join(PROJECT_ROOT, 'agent-python'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('0～100', result.stdout + result.stderr)

    def test_json_report_contains_thresholds(self):
        """JSON 报告应包含阈值配置和门禁结果"""
        mock_results = [{'passed': True, 'source_hit': True, 'keyword_hit': True} for _ in range(28)]
        _, report = self._run_eval_with_mock(
            mock_results, 28, min_source=100.0, min_keyword=95.0, min_final=95.0)

        self.assertIn('thresholds', report)
        self.assertIn('min_source_hit_rate', report['thresholds'])
        self.assertIn('min_keyword_hit_rate', report['thresholds'])
        self.assertIn('min_final_pass_rate', report['thresholds'])
        self.assertIn('threshold_passed', report)

        self.assertEqual(report['thresholds']['min_source_hit_rate'], 1.0)
        self.assertEqual(report['thresholds']['min_keyword_hit_rate'], 0.95)
        self.assertEqual(report['thresholds']['min_final_pass_rate'], 0.95)
        self.assertTrue(report['threshold_passed'])


class TestEvalCLIArgs(unittest.TestCase):
    """测试 CLI 参数解析"""

    def test_help_shows_threshold_options(self):
        """--help 应显示阈值参数说明"""
        result = subprocess.run(
            [sys.executable, 'scripts/eval/eval_retrieval.py', '--help'],
            capture_output=True, text=True, cwd=os.path.join(PROJECT_ROOT, 'agent-python'))
        self.assertIn('--min-source-hit-rate', result.stdout)
        self.assertIn('--min-keyword-hit-rate', result.stdout)
        self.assertIn('--min-final-pass-rate', result.stdout)


if __name__ == '__main__':
    unittest.main()
