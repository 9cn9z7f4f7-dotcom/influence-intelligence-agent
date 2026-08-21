from local_connector.instagram_connector import _instagram_username_from_meta
from app.analytics.next_move import NextMoveBuilder
from app.models import Creator, Competitor, Integration, SourceMode
from config.settings import Settings


def test_instagram_username_from_current_og_description_format():
    text = '39 likes, 14 comments - zhusupova.zhanar August 20, 2026: "hello @syntx_ai"'
    assert _instagram_username_from_meta(text, brand_handle='syntx_ai') == 'zhusupova.zhanar'


def test_instagram_meta_does_not_return_brand_as_creator():
    text = '20 likes, 2 comments - syntx_ai August 20, 2026: "brand post"'
    assert _instagram_username_from_meta(text, brand_handle='syntx_ai') is None


def test_organic_social_creator_can_remain_hunting_candidate():
    creator = Creator(creator_id='c1', name='creator1', canonical_url='https://instagram.com/creator1/', platform='instagram', source_mode=SourceMode.LIVE, topic_tags=['tech'])
    competitor = Competitor(competitor_id='b1', name='brand', source_mode=SourceMode.LIVE)
    organic = Integration(integration_id='i1', competitor_id='b1', creator_id='c1', platform='instagram', category='organic_mention', source_mode=SourceMode.LIVE)
    builder = NextMoveBuilder([creator], [organic], Settings(), potential_creator_ids={'c1'})
    result = builder.build_for_competitor(competitor)
    assert result['candidates']
    assert result['candidates'][0]['candidate'] == 'creator1'
