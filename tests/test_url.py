import pytest

from instascraper.url import parse_shortcode


@pytest.mark.parametrize(
    "url, expected",
    [
        # /p/
        ("https://www.instagram.com/p/DXOCAyzEX8i/", "DXOCAyzEX8i"),
        ("https://www.instagram.com/p/DXOCAyzEX8i", "DXOCAyzEX8i"),
        # /reel/ (real examples from SAMPLE_URLS.md)
        ("https://www.instagram.com/reel/DXOCAyzEX8i/", "DXOCAyzEX8i"),
        ("https://www.instagram.com/reel/DZWyZzugn-z/", "DZWyZzugn-z"),
        ("https://www.instagram.com/reel/DZ_KsKvKAW0", "DZ_KsKvKAW0"),
        # /tv/
        ("https://www.instagram.com/tv/DZ74ozYM30a/", "DZ74ozYM30a"),
        # query string / fragment
        ("https://www.instagram.com/reel/DZSg37uTTRc/?igsh=abc123", "DZSg37uTTRc"),
        ("https://www.instagram.com/p/DZ71hUfiZIO/#anchor", "DZ71hUfiZIO"),
        # without www
        ("https://instagram.com/reel/DZ33SNmDOf0/", "DZ33SNmDOf0"),
        # http instead of https
        ("http://www.instagram.com/reel/DZxmXbvoy7O/", "DZxmXbvoy7O"),
    ],
)
def test_parse_shortcode_valid(url, expected):
    assert parse_shortcode(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123",
        "https://www.instagram.com/someuser/",
        "https://instagram.com/",
        "not a url at all",
    ],
)
def test_parse_shortcode_invalid(url):
    with pytest.raises(ValueError):
        parse_shortcode(url)
