"""Tests for the pure comment-selection logic in scraper.select_top_comments."""

from __future__ import annotations

from datetime import datetime

from insta_scraper.models import Comment
from insta_scraper.scraper import select_top_comments


def test_likes_ranking():
    comments = [
        Comment("a", 1, "low"),
        Comment("b", 5, "high"),
        Comment("c", 3, "mid"),
    ]
    result = select_top_comments(comments, n=2, sort="likes")
    assert [c.username for c in result] == ["b", "c"]


def test_recency_tiebreaker_on_equal_likes():
    older = Comment("old", 5, "older", created_at=datetime(2024, 1, 1, 12, 0))
    newer = Comment("new", 5, "newer", created_at=datetime(2024, 6, 1, 12, 0))
    result = select_top_comments([older, newer], sort="likes")
    assert [c.username for c in result] == ["new", "old"]


def test_none_created_at_treated_as_oldest():
    has_date = Comment("dated", 5, "dated", created_at=datetime(2024, 1, 1, 12, 0))
    no_date = Comment("undated", 5, "undated", created_at=None)
    result = select_top_comments([no_date, has_date], sort="likes")
    assert [c.username for c in result] == ["dated", "undated"]


def test_instagram_mode_preserves_order():
    comments = [
        Comment("first", 1, "first"),
        Comment("second", 99, "second"),
        Comment("third", 50, "third"),
    ]
    result = select_top_comments(comments, n=2, sort="instagram")
    assert [c.username for c in result] == ["first", "second"]


def test_n_larger_than_list():
    comments = [Comment("a", 2, "a"), Comment("b", 1, "b")]
    result = select_top_comments(comments, n=10, sort="likes")
    assert [c.username for c in result] == ["a", "b"]


def test_empty_list():
    assert select_top_comments([], n=5, sort="likes") == []
    assert select_top_comments([], n=5, sort="instagram") == []


def test_does_not_mutate_input():
    comments = [
        Comment("a", 1, "a"),
        Comment("b", 5, "b"),
    ]
    original_order = list(comments)
    select_top_comments(comments, sort="likes")
    assert comments == original_order
