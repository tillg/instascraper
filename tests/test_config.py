"""Network-free tests for the .env config store."""

from insta_scraper.config import load_config, save_config


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / ".env"
    save_config({"IG_USERNAME": "tillg", "INSTASCRAPE_OUTPUT": "data"}, path=p)
    cfg = load_config(p)
    assert cfg["IG_USERNAME"] == "tillg"
    assert cfg["INSTASCRAPE_OUTPUT"] == "data"


def test_save_merges_and_skips_none(tmp_path):
    p = tmp_path / ".env"
    save_config({"IG_USERNAME": "tillg"}, path=p)
    save_config({"IG_PASSWORD": "secret", "IG_USERNAME": None, "X": ""}, path=p)
    cfg = load_config(p)
    assert cfg["IG_USERNAME"] == "tillg"   # preserved (update was None)
    assert cfg["IG_PASSWORD"] == "secret"  # added
    assert "X" not in cfg                   # empty skipped


def test_load_ignores_comments_and_blanks(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\n\nIG_USERNAME=tillg\nbad line no equals\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg == {"IG_USERNAME": "tillg"}


def test_saved_file_is_chmod_600(tmp_path):
    import stat
    p = tmp_path / ".env"
    save_config({"IG_PASSWORD": "secret"}, path=p)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600
