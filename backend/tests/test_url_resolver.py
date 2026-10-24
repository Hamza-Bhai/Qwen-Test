"""Unit tests for the channel URL resolver (valid, invalid, unsupported)."""

import pytest

from app.services.url_resolver import (
    InvalidURLError,
    UnsupportedURLError,
    parse_channel_reference,
)

VALID_CHANNEL_ID = "UC8-Th83bH_thdKZDJCrn88g"


class TestSupportedFormats:
    def test_handle_url(self):
        t = parse_channel_reference("https://www.youtube.com/@mkbhd")
        assert (t.kind, t.identifier) == ("handle", "@mkbhd")

    def test_handle_url_with_trailing_path_and_query(self):
        t = parse_channel_reference("https://youtube.com/@MKBHD/videos?view=0#anchor")
        assert t.identifier == "@MKBHD"

    def test_channel_id_url(self):
        t = parse_channel_reference(f"https://www.youtube.com/channel/{VALID_CHANNEL_ID}")
        assert (t.kind, t.identifier) == ("channel_id", VALID_CHANNEL_ID)

    def test_channel_id_url_mobile_host(self):
        t = parse_channel_reference(f"http://m.youtube.com/channel/{VALID_CHANNEL_ID}/about")
        assert t.identifier == VALID_CHANNEL_ID

    def test_bare_channel_id(self):
        t = parse_channel_reference(VALID_CHANNEL_ID)
        assert t.kind == "channel_id"

    def test_bare_handle_with_at(self):
        t = parse_channel_reference("@mkbhd")
        assert (t.kind, t.identifier) == ("handle", "@mkbhd")

    def test_bare_handle_without_at(self):
        t = parse_channel_reference("mkbhd")
        assert t.identifier == "@mkbhd"

    def test_http_scheme_accepted(self):
        t = parse_channel_reference("http://youtube.com/@some.channel-1")
        assert t.identifier == "@some.channel-1"


class TestUnsupportedLegacyFormats:
    """Legacy /c/ and /user/ URLs must be REPORTED, never guessed."""

    def test_custom_url_reported(self):
        with pytest.raises(UnsupportedURLError) as exc:
            parse_channel_reference("https://www.youtube.com/c/SomeCustomName")
        assert exc.value.kind == "custom URL (/c/)"
        assert exc.value.identifier == "SomeCustomName"
        # ValueError subclass so generic handlers still catch it
        assert isinstance(exc.value, ValueError)

    def test_user_url_reported(self):
        with pytest.raises(UnsupportedURLError) as exc:
            parse_channel_reference("https://www.youtube.com/user/someusername")
        assert "legacy username" in exc.value.kind
        assert "Unsupported legacy YouTube URL format" in str(exc.value)


class TestInvalidInputs:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "not a url at all!!",
            "https://vimeo.com/12345",
            "https://youtube.com",
            "https://youtube.com/",
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            f"https://youtube.com/channel/{VALID_CHANNEL_ID}TOOSHORT",
            "https://youtube.com/channel/notachannelid",
            "https://youtube.com/@",
            "https://youtube.com/c/",
            "https://youtube.com/user/",
        ],
    )
    def test_invalid_raises(self, raw):
        with pytest.raises((InvalidURLError, UnsupportedURLError)):
            parse_channel_reference(raw)

    def test_empty_message(self):
        with pytest.raises(InvalidURLError, match="empty"):
            parse_channel_reference("")
