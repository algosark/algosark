from unittest.mock import Mock, patch

import pytest
import requests

from spsg.broker.alpaca_adapter import AlpacaAdapter


def connected_adapter():
    adapter = AlpacaAdapter()
    adapter.connect("key", "secret", paper=True)
    return adapter


def test_stop_order_sends_stop_price():
    adapter = connected_adapter()
    response = Mock()
    response.json.return_value = {
        "id": "order-1",
        "symbol": "AAPL",
        "side": "buy",
        "qty": "1",
        "type": "stop",
        "status": "accepted",
        "filled_avg_price": None,
    }

    with patch("spsg.broker.alpaca_adapter.requests.post", return_value=response) as post:
        adapter.place_order("aapl", 1, "buy", order_type="stop", stop_price=190)

    payload = post.call_args.kwargs["json"]
    assert payload["symbol"] == "AAPL"
    assert payload["stop_price"] == "190"
    assert "limit_price" not in payload


def test_broker_rejection_includes_alpaca_response_message():
    adapter = connected_adapter()
    response = Mock()
    response.status_code = 422
    response.json.return_value = {"message": "asset GBPUSD is not tradable"}
    response.raise_for_status.side_effect = requests.HTTPError("422 Client Error")

    with patch("spsg.broker.alpaca_adapter.requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="asset GBPUSD is not tradable"):
            adapter.place_order("GBPUSD", 1, "buy")


def test_limit_and_stop_orders_require_a_price():
    adapter = connected_adapter()

    with pytest.raises(ValueError, match="limit_price"):
        adapter.place_order("AAPL", 1, "buy", order_type="limit")
    with pytest.raises(ValueError, match="stop_price"):
        adapter.place_order("AAPL", 1, "buy", order_type="stop")
