from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, VETO_CFG
from app.core.models import SymbolKlines

def test_g1_rejects_out_of_order_bars():
    s=_base_snapshot(); bars=list(s.klines["5m"].bars); bars[1],bars[2]=bars[2],bars[1]
    s= s.__class__(**{**s.__dict__, "klines":{"5m":SymbolKlines("BTCUSDT","5m",bars)}})
    r=__import__('app.risk.veto',fromlist=['']).guard_g1_data_integrity(s,_empty_news(),None,VETO_CFG["g1_data_integrity"])
    assert not r.passed
