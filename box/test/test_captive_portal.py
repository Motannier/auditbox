import asyncio

from auditbox.gateway.app import PUBLIC_URL, app, captive_portal_probe


def test_captive_probe_paths_are_registered() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}
    assert {
        "/hotspot-detect.html",
        "/connecttest.txt",
        "/ncsi.txt",
        "/generate_204",
        "/gen_204",
        "/canonical.html",
        "/success.txt",
        "/redirect",
    } <= paths


def test_captive_probe_redirects_to_public_url() -> None:
    response = asyncio.run(captive_portal_probe())
    assert response.status_code == 302
    assert response.headers["location"] == PUBLIC_URL
