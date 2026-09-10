"""Explicit gateway identification; never infer a gateway from a model alias."""

from urllib.parse import urlsplit


def endpoint(value):
    url = urlsplit(str(value))
    if url.scheme not in ("http", "https") or not url.hostname:
        raise ValueError("Gateway URLs must be absolute HTTP(S) URLs")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError(
            "Gateway URLs must not contain credentials, queries or fragments"
        )
    return (
        url.scheme,
        url.hostname,
        url.port or (443 if url.scheme == "https" else 80),
        url.path.rstrip("/"),
    )


def matches_endpoint(resource, urls):
    if not urls:
        return False
    try:
        client = resource._client
        return endpoint(client.base_url) in {endpoint(url) for url in urls}
    except (AttributeError, TypeError, ValueError):
        return False


def gateway_name(
    resource,
    litellm_urls=(),
    openrouter_urls=(),
    portkey_urls=(),
    bifrost_urls=(),
    truefoundry_urls=(),
):
    if matches_endpoint(resource, litellm_urls):
        return "litellm"
    if matches_endpoint(resource, ("https://openrouter.ai/api/v1", *openrouter_urls)):
        return "openrouter"
    if matches_endpoint(resource, ("https://api.portkey.ai/v1", *portkey_urls)):
        return "portkey"
    if matches_endpoint(resource, bifrost_urls):
        return "bifrost"
    if matches_endpoint(resource, truefoundry_urls):
        return "truefoundry"
    return None
