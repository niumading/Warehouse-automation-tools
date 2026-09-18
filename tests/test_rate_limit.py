"""限流器的确定性边界测试。"""
import unittest

from app.core.rate_limit import FixedWindowRateLimiter


class RateLimiterTest(unittest.TestCase):
    def test_blocks_only_after_limit_and_recovers_after_window(self):
        limiter = FixedWindowRateLimiter()
        self.assertEqual(limiter.allow("login:127.0.0.1", 2, now=100), (True, 0))
        self.assertEqual(limiter.allow("login:127.0.0.1", 2, now=101), (True, 0))
        allowed, retry = limiter.allow("login:127.0.0.1", 2, now=102)
        self.assertFalse(allowed)
        self.assertEqual(retry, 58)
        self.assertEqual(limiter.allow("login:127.0.0.1", 2, now=161), (True, 0))

    def test_scopes_and_users_do_not_share_quota(self):
        limiter = FixedWindowRateLimiter()
        self.assertTrue(limiter.allow("ocr:1:1", 1, now=10)[0])
        self.assertFalse(limiter.allow("ocr:1:1", 1, now=11)[0])
        self.assertTrue(limiter.allow("ocr:1:2", 1, now=11)[0])
        self.assertTrue(limiter.allow("api:1:1", 1, now=11)[0])


if __name__ == "__main__":
    unittest.main()
