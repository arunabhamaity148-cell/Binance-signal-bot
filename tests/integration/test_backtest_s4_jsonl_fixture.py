"""Valid S4 candidate fixture under unchanged configured trend/pullback/OI rules."""
from __future__ import annotations
from app.config import load_all
from app.core.math import OHLC
from app.core.models import DerivativesState,MarketSnapshot,SymbolKlines,TimestampedValue

def build_s4_trigger_snapshot():
    cfg=load_all(); base=1_800_000_000_000; iv5=300_000
    def rising(n,start,step,interval):
        return [OHLC(start+step*i,start+step*i+max(step,0.2),start+step*i-max(step,0.2),start+step*i,100,base+i*interval) for i in range(n)]
    # History lengths end together so replay has the configured HTF warmup.
    b5=rising(120,970,1,iv5); b1=rising(100,1000,1,3_600_000); b4=rising(60,1000,2,14_400_000)
    import math
    b15=[]
    for i in range(100):
        close=1000+i*0.9+10*math.sin(i*2*math.pi/10)
        b15.append(OHLC(close,close+2,close-2,close,100,base+i*900_000))
    end=b5[-1].close_time_ms
    # Align each timeframe to the common replay endpoint while retaining spacing.
    def align(bars,interval):
        shift=end-bars[-1].close_time_ms
        return [OHLC(x.open,x.high,x.low,x.close,x.volume,x.close_time_ms+shift) for x in bars]
    b15=align(b15,900_000); b1=align(b1,3_600_000); b4=align(b4,14_400_000)
    oi=[TimestampedValue(1_000_000+i*10_000,end-50*iv5+i*iv5,end-50*iv5+i*iv5) for i in range(51)]
    deriv=DerivativesState("BTCUSDT",[],oi,oi,oi,oi,[],[],None)
    return MarketSnapshot("s4-jsonl","BTCUSDT",end,{
        "5m":SymbolKlines("BTCUSDT","5m",b5),"15m":SymbolKlines("BTCUSDT","15m",b15),
        "1h":SymbolKlines("BTCUSDT","1h",b1),"4h":SymbolKlines("BTCUSDT","4h",b4)},
        None,None,deriv,{},0.1,0.001,0.001,2.0,5.0)
