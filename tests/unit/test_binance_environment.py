import pytest

from app.data.binance.rest import RestClientConfig
from app.data.binance.websocket import WebSocketClientConfig


def test_rest_environment_selection_is_opt_in():
    mainnet = RestClientConfig("https://fapi.binance.com")
    testnet = RestClientConfig("https://fapi.binance.com", binance_env="testnet")
    assert mainnet.base_url == "https://fapi.binance.com"
    assert mainnet.binance_env == "mainnet"
    assert testnet.base_url == "https://testnet.binancefuture.com"


def test_websocket_environment_selection_is_opt_in():
    mainnet = WebSocketClientConfig("wss://fstream.binance.com/stream")
    testnet = WebSocketClientConfig("wss://fstream.binance.com/stream", binance_env="testnet")
    assert mainnet.base_ws_url == "wss://fstream.binance.com/stream"
    assert testnet.base_ws_url == "wss://stream.binancefuture.com/stream"


@pytest.mark.parametrize("factory", [
    lambda: RestClientConfig("https://fapi.binance.com", binance_env="sandbox"),
    lambda: WebSocketClientConfig("wss://fstream.binance.com/stream", binance_env="sandbox"),
])
def test_unknown_environment_is_rejected(factory):
    with pytest.raises(ValueError, match="binance_env"):
        factory()
