import unittest
from unittest.mock import Mock

from url_helpers import DEFAULT_DOMAINS, canonical_url, extract_urls, host_allowed


class URLTests(unittest.TestCase):
    def test_known_domains(self):
        for u in (
            'https://youtu.be/123', 'https://youtube.com/watch?v=1',
            'https://www.instagram.com/reel/abc/',
            'https://vm.tiktok.com/abc/', 'https://x.com/test/status/123',
            'https://www.reddit.com/r/example/comments/abc',
        ):
            self.assertTrue(host_allowed(u, DEFAULT_DOMAINS), u)

    def test_reject_hostname_trick_and_credentials(self):
        for u in (
            'https://youtube.com.attacker.example/evil',
            'https://youtube.com@evil.example/video',
            'http://localhost/', 'file:///etc/shadow',
            'https://127.0.0.1/', 'https://youtube.com:8080/test',
        ):
            self.assertFalse(host_allowed(u, DEFAULT_DOMAINS), u)

    def test_messaging_punctuation(self):
        self.assertEqual(canonical_url('www.youtube.com/watch?v=12),'), 'https://www.youtube.com/watch?v=12')

    def test_visible_urls_in_text(self):
        msg = Mock(text='Watch https://youtu.be/abc! and https://x.com/u/status/123.', caption=None)
        msg.parse_entities.return_value = {}
        msg.parse_caption_entities.return_value = {}
        self.assertEqual(extract_urls(msg), [
            'https://youtu.be/abc', 'https://x.com/u/status/123'
        ])


if __name__ == '__main__':
    unittest.main()
