import pytest

from stackchan_gateway.xiaozhi_bridge import validate_endpoint


def test_validate_endpoint_hides_but_accepts_tokenized_url():
    host = validate_endpoint("wss://api.xiaozhi.me/mcp/?token=secret")

    assert host == "api.xiaozhi.me"


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "https://api.xiaozhi.me/mcp/?token=secret",
        "wss://api.xiaozhi.me/mcp/",
    ],
)
def test_validate_endpoint_rejects_invalid_values(endpoint):
    with pytest.raises(ValueError):
        validate_endpoint(endpoint)
