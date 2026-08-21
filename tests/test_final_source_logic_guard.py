from app.article_parser import ArticleParseResult
from app.platforms.articles import _is_article_like
from app.query_generator import generate_article_queries
from app.platforms.instagram import InstagramPlatformAdapter


def test_product_schema_rejected_even_with_long_prose():
    page = ArticleParseResult(
        source_url="https://shop.example.com/nike-pegasus",
        canonical_url="https://shop.example.com/nike-pegasus",
        title="Nike Pegasus",
        main_text="Detailed product description and sizing information. " * 40,
        metadata={"schema_types": ["Product"], "paragraph_count": 8},
    )
    assert _is_article_like(page) is False


def test_generic_editorial_page_with_real_prose_is_accepted():
    page = ArticleParseResult(
        source_url="https://example.com/nike-pegasus-analysis",
        canonical_url="https://example.com/nike-pegasus-analysis",
        title="We tested Nike Pegasus for a month",
        main_text="Long-form editorial testing notes and running context. " * 20,
        metadata={"paragraph_count": 6},
    )
    assert _is_article_like(page) is True


def test_article_queries_do_not_intentionally_search_for_stores():
    queries = [q.lower() for q in generate_article_queries("Nike")]
    assert not any("where to buy" in q for q in queries)
    assert any("review" in q or "article" in q or "blog" in q or "news" in q for q in queries)


def test_social_detector_uses_observed_external_link_not_profile_url_as_commercial_signal():
    adapter = InstagramPlatformAdapter()
    item = {
        "username": "runner",
        "profile_url": "https://www.instagram.com/runner/",
        "caption": "Nike — смотрите по ссылке",
        "brand_mention": True,
        "paid_partnership_label": False,
        "collaboration_label": False,
        "links": ["https://www.nike.com/some-product?utm_source=creator"],
    }
    result = adapter.detect_integration(item, ["Nike"])
    assert result.category == "confirmed"

    # An Instagram profile URL by itself must never be treated as a brand/product link.
    no_external_link = dict(item, caption="Nike мне нравится", links=[])
    result2 = adapter.detect_integration(no_external_link, ["Nike"])
    assert result2.category != "confirmed"
