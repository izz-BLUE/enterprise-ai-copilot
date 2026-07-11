import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.config import _load_rag_gate_settings
from app.retrieval.retrieval_gate import (
    CandidateSignals,
    evaluate_gate,
    evaluate_gate_timed_fail_open,
    get_gate_metrics,
    reset_gate_metrics,
)


class RetrievalGateTest(unittest.TestCase):
    def test_vector_strong_passes(self):
        decision = evaluate_gate([CandidateSignals('a', vector_score=0.65)], mode='shadow')
        self.assertTrue(decision.answerable)
        self.assertEqual('vector_strong', decision.reason_code)

    def test_same_candidate_weak_signals_pass(self):
        decision = evaluate_gate([
            CandidateSignals('a', vector_score=0.61, bm25_score=2.10),
        ], mode='shadow')
        self.assertTrue(decision.answerable)
        self.assertEqual('vector_bm25_weak_combined', decision.reason_code)

    def test_signals_from_different_candidates_do_not_combine(self):
        decision = evaluate_gate([
            CandidateSignals('vector-only', vector_score=0.61),
            CandidateSignals('bm25-only', bm25_score=99.0),
        ], mode='shadow')
        self.assertFalse(decision.answerable)

    def test_vector_weak_only_blocks(self):
        decision = evaluate_gate([
            CandidateSignals('a', vector_score=0.62),
        ], mode='shadow')
        self.assertFalse(decision.answerable)

    def test_bm25_high_alone_cannot_pass(self):
        decision = evaluate_gate([
            CandidateSignals('a', bm25_score=999.0),
        ], mode='shadow')
        self.assertFalse(decision.answerable)

    def test_both_signals_below_threshold_block(self):
        decision = evaluate_gate([
            CandidateSignals('a', vector_score=0.60, bm25_score=2.09),
        ], mode='shadow')
        self.assertFalse(decision.answerable)
        self.assertEqual('below_threshold', decision.reason_code)

    def test_threshold_equality_passes(self):
        strong = evaluate_gate([
            CandidateSignals('strong', vector_score=0.65),
        ], mode='shadow')
        combined = evaluate_gate([
            CandidateSignals('combined', vector_score=0.61, bm25_score=2.10),
        ], mode='shadow')
        self.assertTrue(strong.answerable)
        self.assertTrue(combined.answerable)

    def test_empty_candidates_block(self):
        decision = evaluate_gate([], mode='shadow')
        self.assertFalse(decision.answerable)
        self.assertEqual('empty_candidates', decision.reason_code)

    def test_gate_off_returns_disabled(self):
        decision = evaluate_gate([], mode='off')
        self.assertTrue(decision.answerable)
        self.assertEqual('gate_disabled', decision.reason_code)

    def test_invalid_configuration(self):
        self.assertEqual('off', _load_rag_gate_settings({})[0])
        with self.assertRaisesRegex(ValueError, 'off\|shadow\|enforce'):
            _load_rag_gate_settings({'RAG_GATE_MODE': 'invalid'})
        with self.assertRaisesRegex(ValueError, '尚未开放'):
            _load_rag_gate_settings({'RAG_GATE_MODE': 'enforce'})
        with self.assertRaisesRegex(ValueError, '大于或等于'):
            _load_rag_gate_settings({
                'RAG_GATE_MODE': 'shadow',
                'RAG_VECTOR_STRONG_THRESHOLD': '0.60',
                'RAG_VECTOR_WEAK_THRESHOLD': '0.61',
            })
        with self.assertRaisesRegex(ValueError, '\[0, 1\]'):
            _load_rag_gate_settings({
                'RAG_GATE_MODE': 'shadow',
                'RAG_VECTOR_STRONG_THRESHOLD': '1.1',
            })

    def test_candidate_merge_keeps_scores_on_matching_chunk(self):
        from app.retrieval.hybrid_retriever import _merge_candidate_signals

        vector_chunk = {'id': 'vector'}
        shared_chunk = {'id': 'shared'}
        bm25_chunk = {'id': 'bm25'}
        signals = _merge_candidate_signals(
            [(vector_chunk, 0.70), (shared_chunk, 0.62)],
            [(bm25_chunk, 9.0), (shared_chunk, 2.5)],
        )
        by_id = {signal.chunk_id: signal for signal in signals}
        self.assertIsNone(by_id['vector'].bm25_score)
        self.assertIsNone(by_id['bm25'].vector_score)
        self.assertEqual(0.62, by_id['shared'].vector_score)
        self.assertEqual(2.5, by_id['shared'].bm25_score)
        self.assertEqual(2, by_id['shared'].vector_rank)
        self.assertEqual(2, by_id['shared'].bm25_rank)

    def test_evaluator_error_fails_open_and_increments_metric(self):
        reset_gate_metrics()
        with unittest.mock.patch(
            'app.retrieval.retrieval_gate.evaluate_gate',
            side_effect=RuntimeError('sensitive query must not be logged'),
        ), unittest.mock.patch(
            'app.retrieval.retrieval_gate.RAG_GATE_MODE', 'shadow',
        ), self.assertLogs('agent', level='ERROR') as captured:
            decision, _latency = evaluate_gate_timed_fail_open(
                [CandidateSignals('a', vector_score=0.1)], trace_id='trace-error',
            )

        self.assertTrue(decision.answerable)
        self.assertEqual('gate_evaluation_error', decision.reason_code)
        self.assertEqual('shadow_fail_open', decision.mode_reason_code)
        self.assertEqual(1, get_gate_metrics()['gate_evaluation_error'])
        self.assertNotIn('sensitive query', '\n'.join(captured.output))


if __name__ == '__main__':
    unittest.main()
