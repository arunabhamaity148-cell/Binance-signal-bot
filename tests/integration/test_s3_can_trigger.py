from app.config import load_all
from app.strategies.s3_funding_crowding import S3FundingCrowding
from tests.strategies.test_s3_funding_crowding import build_realistic_s3_short_reversal_snapshot, _empty_news

CFG = load_all().strategy


def test_s3_can_trigger_from_documented_crowding_reversal():
    snapshot = build_realistic_s3_short_reversal_snapshot()
    assert S3FundingCrowding().evaluate(snapshot, _empty_news(snapshot.as_of_ts_ms), CFG)
