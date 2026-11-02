"""A2(실패 귀속, Observer/G5)·A3(차단 프레이밍) — artex 구조를 참고해 적용한 두 가지(CLAUDE.md §6/§8, 2026-11).
순수 함수라 DB 없이 검증한다."""
import unittest

from diagnostic_workbench import policy, worker


class ClassifyOutcomeTest(unittest.TestCase):
    def test_skipped_is_not_classified(self):
        self.assertIsNone(worker.classify_outcome("SKIPPED", "아무 내용"))

    def test_succeeded_with_no_block_signal_is_ok(self):
        self.assertEqual(worker.classify_outcome("SUCCEEDED", "정상 응답 200 OK"), "ok")

    def test_succeeded_with_block_signal_is_blocked(self):
        self.assertEqual(worker.classify_outcome("SUCCEEDED", "HTTP/1.1 403 Forbidden"), "blocked")
        self.assertEqual(worker.classify_outcome("SUCCEEDED", "요청이 차단됨"), "blocked")

    def test_failed_without_block_signal_is_error(self):
        self.assertEqual(worker.classify_outcome("FAILED", "연결 시간 초과"), "error")

    def test_failed_with_block_signal_is_blocked_not_error(self):
        self.assertEqual(worker.classify_outcome("FAILED", "429 Too Many Requests"), "blocked")


class FrameBlockMessageTest(unittest.TestCase):
    def test_wraps_reason_with_platform_control_framing(self):
        msg = policy.frame_block_message("도구 'x' 은(는) 등록되지 않음")
        self.assertIn("도구 'x' 은(는) 등록되지 않음", msg)
        self.assertIn("워크벤치 정책 통제", msg)
        self.assertIn("우회", msg)


if __name__ == "__main__":
    unittest.main()
