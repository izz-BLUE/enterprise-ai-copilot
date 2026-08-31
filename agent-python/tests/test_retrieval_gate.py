import unittest
import unittest.mock

from app.retrieval.retrieval_gate import (
    CandidateSignals,
    evaluate_gate,
    evaluate_gate_timed_fail_open,
    get_gate_metrics,
    reset_gate_metrics,
)


class RetrievalGateTest(unittest.TestCase):
    def test_gate_is_fixed_off_for_empty_and_low_signal_candidates(self):
        for candidates in (
            [],
            [CandidateSignals('weak', vector_score=0.1, bm25_score=0.01)],
        ):
            decision = evaluate_gate(candidates)
            self.assertTrue(decision.answerable)
            self.assertEqual('gate_disabled', decision.reason_code)
            self.assertEqual('gate_disabled', decision.mode_reason_code)

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

    def test_evaluator_error_remains_fail_open(self):
        reset_gate_metrics()
        with unittest.mock.patch(
            'app.retrieval.retrieval_gate.evaluate_gate',
            side_effect=RuntimeError('sensitive query must not be logged'),
        ), self.assertLogs('agent', level='ERROR') as captured:
            decision, _latency = evaluate_gate_timed_fail_open(
                [CandidateSignals('a', vector_score=0.1)], trace_id='trace-error',
            )

        self.assertTrue(decision.answerable)
        self.assertEqual('gate_evaluation_error', decision.reason_code)
        self.assertEqual('gate_disabled', decision.mode_reason_code)
        self.assertEqual(1, get_gate_metrics()['gate_evaluation_error'])
        self.assertNotIn('sensitive query', '\n'.join(captured.output))


if __name__ == '__main__':
    unittest.main()
