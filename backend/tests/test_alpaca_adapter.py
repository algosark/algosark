from unittest.mock import Mock, patch

import pytest
import requests

from spsg.broker.alpaca_adapter import AlpacaAdapter


def connected_adapter():
    adapter = AlpacaAdapter()
    adapter.connect("key", "secret", paper=True)
    return adapter


def tradable_assets_response():
    response = Mock()
    response.json.return_value = [
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "exchange": "NASDAQ",
            "class": "us_equity",
            "status": "active",
            "tradable": True,
            "fractionable": True,
        },
        {
            "symbol": "GBPUSD",
            "name": "GBP/USD",
            "exchange": "OTC",
            "class": "us_equity",
            "status": "active",
            "tradable": False,
            "fractionable": False,
        },
        {
            "symbol": "OLD",
            "name": "Inactive Inc.",
            "exchange": "NYSE",
            "class": "us_equity",
            "status": "inactive",
            "tradable": True,
            "fractionable": False,
        },
    ]
    return response


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

    with (
        patch("spsg.broker.alpaca_adapter.requests.get", return_value=tradable_assets_response()),
        patch("spsg.broker.alpaca_adapter.requests.post", return_value=response) as post,
    ):
        adapter.place_order("aapl", 1.0, "buy", order_type="stop", stop_price=190)

    payload = post.call_args.kwargs["json"]
    assert payload["symbol"] == "AAPL"
    assert payload["stop_price"] == "190"
    assert "limit_price" not in payload


def test_broker_rejection_includes_alpaca_response_message():
    adapter = connected_adapter()
    response = Mock()
    response.status_code = 422
    response.json.return_value = {"message": "insufficient buying power"}
    response.raise_for_status.side_effect = requests.HTTPError("422 Client Error")

    with (
        patch("spsg.broker.alpaca_adapter.requests.get", return_value=tradable_assets_response()),
        patch("spsg.broker.alpaca_adapter.requests.post", return_value=response),
    ):
        with pytest.raises(RuntimeError, match="insufficient buying power"):
            adapter.place_order("AAPL", 1.0, "buy")


def test_nontradable_symbol_is_rejected_before_posting_order():
    adapter = connected_adapter()
    with (
        patch("spsg.broker.alpaca_adapter.requests.get", return_value=tradable_assets_response()),
        patch("spsg.broker.alpaca_adapter.requests.post") as post,
    ):
        with pytest.raises(ValueError, match="not an active, tradable"):
            adapter.place_order("GBPUSD", 1.0, "buy")
    post.assert_not_called()


def test_limit_and_stop_orders_require_a_price():
    adapter = connected_adapter()

    with pytest.raises(ValueError, match="limit_price"):
        adapter.place_order("AAPL", 1, "buy", order_type="limit")
    with pytest.raises(ValueError, match="stop_price"):
        adapter.place_order("AAPL", 1, "buy", order_type="stop")


def test_tradable_assets_only_include_active_tradable_us_equities():
    adapter = connected_adapter()
    with patch(
        "spsg.broker.alpaca_adapter.requests.get",
        return_value=tradable_assets_response(),
    ) as get:
        assets = adapter.get_tradable_assets()

    assert [asset["symbol"] for asset in assets] == ["AAPL"]
    assert get.call_args.kwargs["params"] == {
        "status": "active",
        "asset_class": "us_equity",
    }


def test_non_fractionable_asset_rejects_fractional_quantity():
    adapter = connected_adapter()
    response = tradable_assets_response()
    response.json.return_value.append({
        "symbol": "BRK.A",
        "name": "Berkshire Hathaway",
        "exchange": "NYSE",
        "class": "us_equity",
        "status": "active",
        "tradable": True,
        "fractionable": False,
    })

    with patch("spsg.broker.alpaca_adapter.requests.get", return_value=response):
        with pytest.raises(ValueError, match="does not support fractional-share"):
            adapter.place_order("BRK.A", 0.5, "buy")


def test_order_history_uses_authenticated_trading_endpoint():
    adapter = connected_adapter()
    response = Mock()
    response.json.return_value = [{"id": "order-1", "symbol": "AAPL"}]

    with patch("spsg.broker.alpaca_adapter.requests.get", return_value=response) as get:
        orders = adapter.get_orders(limit=20)

    assert orders[0]["id"] == "order-1"
    assert get.call_args.args[0] == "https://paper-api.alpaca.markets/v2/orders"
    assert get.call_args.kwargs["params"] == {
        "status": "all",
        "limit": 20,
        "direction": "desc",
    }
