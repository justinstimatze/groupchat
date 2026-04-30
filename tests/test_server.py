#!/usr/bin/env python3
"""Unit tests for meme_server machinery (cooldown, meme_info, fuzzy lookup)."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

# Add parent dir so we can import meme_server
sys.path.insert(0, str(Path(__file__).parent.parent))
import meme_server


def _clear_drops():
    if meme_server.DROPS_FILE.exists():
        meme_server.DROPS_FILE.unlink()


def test_cooldown_clear_when_no_drops():
    _clear_drops()
    state = meme_server._cooldown_state()
    assert state["state"] == "clear"
    assert state["threshold"] == 0.0
    assert state["last_drop_secs"] is None
    print("  PASS  cooldown clear on fresh start")


def test_cooldown_tight_immediately_after_drop():
    _clear_drops()
    meme_server._record_drop()
    state = meme_server._cooldown_state()
    assert state["state"] == "tight"
    assert state["threshold"] > 0.7
    print(f"  PASS  cooldown tight after drop (threshold={state['threshold']:.2f})")


def test_cooldown_decays_over_time():
    _clear_drops()
    # Simulate a drop 5 minutes ago
    old_ts = time.time() - 300
    meme_server.DROPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    meme_server.DROPS_FILE.write_text(json.dumps([old_ts]))
    state = meme_server._cooldown_state()
    assert state["state"] == "clear"
    assert state["threshold"] < 0.1
    print(f"  PASS  cooldown clear after 5 min (threshold={state['threshold']:.3f})")
    _clear_drops()


def test_record_drop_keeps_last_20():
    _clear_drops()
    for _ in range(25):
        meme_server._record_drop()
    drops = meme_server._load_drops()
    assert len(drops) == 20
    print("  PASS  drops file capped at 20 entries")
    _clear_drops()


def test_meme_info_exact_match():
    result = json.loads(meme_server.meme_info("surprised-pikachu"))
    assert result["name"] == "surprised-pikachu"
    assert "key" in result
    assert "deploy_when" in result
    assert "too_much_if" in result
    assert "mechanism" in result
    assert isinstance(result["mechanism"], list)
    assert "text_dependent" in result
    print(f"  PASS  meme_info exact match (mechanism={result['mechanism']})")


def test_meme_info_fuzzy_match():
    result = json.loads(meme_server.meme_info("surprised-pikach"))  # typo
    assert result["name"] == "surprised-pikachu"
    print("  PASS  meme_info fuzzy match on typo")


def test_meme_info_not_found():
    result = meme_server.meme_info("totally-made-up-meme-xyz")
    assert "not found" in result
    print("  PASS  meme_info returns not-found for unknown name")


def test_drop_meme_not_found_message():
    _clear_drops()
    result = meme_server.drop_meme("definitely-not-a-meme")
    assert "not found" in result
    assert "meme_info" in result
    print("  PASS  drop_meme not-found message references meme_info")


def test_drop_meme_renders_and_records_drop():
    _clear_drops()
    with (
        patch.object(meme_server, "_resolve_img_url", return_value="https://media.tenor.com/x.gif"),
        patch.object(meme_server, "_cached_gif", return_value="/nonexistent/test.gif"),
        patch.object(meme_server, "_render_delayed"),
    ):
        result = meme_server.drop_meme("success-kid")
    assert "success-kid" in result
    drops = meme_server._load_drops()
    assert len(drops) == 1
    print(f"  PASS  drop_meme renders and records drop (result={result!r})")
    _clear_drops()


def test_drop_meme_cooldown_warning():
    _clear_drops()
    meme_server._record_drop()  # simulate recent drop
    with (
        patch.object(meme_server, "_resolve_img_url", return_value="https://media.tenor.com/x.gif"),
        patch.object(meme_server, "_cached_gif", return_value="/nonexistent/test.gif"),
        patch.object(meme_server, "_render_delayed"),
    ):
        result = meme_server.drop_meme("this-is-fine")
    assert "cooldown" in result
    assert "⚠️" in result, f"expected tight cooldown warning (⚠️), got: {result!r}"
    print(f"  PASS  drop_meme shows cooldown after recent drop (result={result!r})")
    _clear_drops()


def test_all_memes_have_mechanism():
    memes = meme_server._load_db()
    missing = [m["name"] for m in memes if not m.get("mechanism")]
    assert not missing, f"Missing mechanism: {missing}"
    print(f"  PASS  all {len(memes)} memes have mechanism field")


def test_all_memes_have_alt_text():
    memes = meme_server._load_db()
    missing = [m["name"] for m in memes if not m.get("alt_text")]
    assert not missing, f"Missing alt_text: {missing}"
    print(f"  PASS  all {len(memes)} memes have alt_text field")


def test_meme_info_includes_alt_text():
    result = json.loads(meme_server.meme_info("surprised-pikachu"))
    assert "alt_text" in result, "meme_info should include alt_text"
    assert result["alt_text"], "alt_text should be non-empty"
    print("  PASS  meme_info includes alt_text")


def test_list_memes_includes_mechanism():
    result = json.loads(meme_server.list_memes())
    required = {"name", "deploy_when", "text_dependent", "mechanism", "native_platform", "vitality"}
    for m in result:
        missing = required - m.keys()
        assert not missing, f"{m.get('name', '?')} missing fields in list_memes: {missing}"
    print(f"  PASS  list_memes has all required fields for all {len(result)} memes")


def test_cached_gif_rejects_bad_host():
    """_cached_gif must refuse unknown hosts to prevent SSRF."""
    try:
        meme_server._cached_gif("test", "https://evil.example.com/x.gif")
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        assert "untrusted" in str(e).lower() or "Refusing" in str(e)
    print("  PASS  _cached_gif rejects unknown host")


def test_cached_gif_accepts_trusted_hosts():
    """_cached_gif should accept all known image CDNs."""
    from unittest.mock import MagicMock, patch

    trusted = [
        "https://media1.tenor.com/a.gif",
        "https://media.giphy.com/a.gif",
        "https://i.imgur.com/a.gif",
        "https://i.kym-cdn.com/a.gif",
    ]
    for url in trusted:
        dest = meme_server.GIF_CACHE / "test-trust.gif"
        dest.unlink(missing_ok=True)
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b"GIF89a"
        with patch("meme_server.urllib.request.urlopen", return_value=mock_response):
            meme_server._cached_gif("test-trust", url)
        dest.unlink(missing_ok=True)
    print(f"  PASS  _cached_gif accepts all {len(trusted)} trusted hosts")


def test_find_returns_none_for_unknown():
    memes = meme_server._load_db()
    result = meme_server._find(memes, "zzzz-completely-unknown-xyz")
    assert result is None
    print("  PASS  _find returns None for completely unknown name")


def test_find_exact_over_fuzzy():
    """Exact match must win even when another name is also close."""
    memes = meme_server._load_db()
    result = meme_server._find(memes, "doge")
    assert result is not None and result["name"] == "doge"
    print("  PASS  _find exact match 'doge' wins over fuzzy candidates")


if __name__ == "__main__":
    tests = [
        test_cooldown_clear_when_no_drops,
        test_cooldown_tight_immediately_after_drop,
        test_cooldown_decays_over_time,
        test_record_drop_keeps_last_20,
        test_meme_info_exact_match,
        test_meme_info_fuzzy_match,
        test_meme_info_not_found,
        test_drop_meme_not_found_message,
        test_drop_meme_renders_and_records_drop,
        test_drop_meme_cooldown_warning,
        test_all_memes_have_mechanism,
        test_all_memes_have_alt_text,
        test_meme_info_includes_alt_text,
        test_list_memes_includes_mechanism,
        test_cached_gif_rejects_bad_host,
        test_cached_gif_accepts_trusted_hosts,
        test_find_returns_none_for_unknown,
        test_find_exact_over_fuzzy,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1

    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(0 if failed == 0 else 1)
