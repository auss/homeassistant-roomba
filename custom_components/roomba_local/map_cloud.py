"""On-demand iRobot cloud map fetch with ephemeral credentials.

Account email/password and cloud tokens are used only for a single fetch run and
must never be written to a Home Assistant config entry.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .const import CLOUD_APP_ID, DEFAULT_COUNTRY

_LOGGER = logging.getLogger(__name__)

DISCOVERY_URL = "https://disc-prod.iot.irobotapi.com/v1/discover/endpoints"


@dataclass
class MapFetchResult:
    """Result of a one-shot map fetch (no secrets)."""

    png_bytes: bytes
    blid: str
    pmap_id: str
    pmapv_id: str
    fetched_at: str
    robot_name: str | None = None
    content_type: str = "image/png"
    notes: list[str] = field(default_factory=list)


class MapFetchError(Exception):
    """Raised when cloud map fetch fails."""


def _http_json(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> Any:
    """Perform an HTTP request and parse JSON."""
    req_headers = dict(headers or {})
    body = data
    if form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        req_headers.setdefault(
            "Content-Type", "application/x-www-form-urlencoded"
        )
    request = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:500]
        raise MapFetchError(f"HTTP {err.code} for {url}: {detail}") from err
    except urllib.error.URLError as err:
        raise MapFetchError(f"Network error for {url}: {err}") from err
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as err:
        raise MapFetchError(f"Invalid JSON from {url}") from err


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sigv4_headers(
    *,
    method: str,
    host: str,
    path: str,
    query: str,
    region: str,
    access_key: str,
    secret_key: str,
    session_token: str,
    service: str = "execute-api",
) -> dict[str, str]:
    """Build SigV4 headers for a GET against API Gateway-style hosts."""
    now = datetime.now(tz=UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-security-token:{session_token}\n"
    )
    signed_headers = "host;x-amz-date;x-amz-security-token"
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical_request = "\n".join(
        [
            method,
            path,
            query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _sign(
        _sign(
            _sign(_sign(("AWS4" + secret_key).encode("utf-8"), date_stamp), region),
            service,
        ),
        "aws4_request",
    )
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    return {
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-security-token": session_token,
        "Authorization": authorization,
    }


def discover_endpoints(country: str) -> dict[str, Any]:
    """Load Gigya + deployment endpoints for a country code."""
    url = f"{DISCOVERY_URL}?country_code={urllib.parse.quote(country)}"
    data = _http_json("GET", url)
    if not isinstance(data, dict):
        raise MapFetchError("Unexpected discovery response")
    return data


def gigya_login(
    *,
    email: str,
    password: str,
    api_key: str,
    datacenter_domain: str,
) -> dict[str, str]:
    """Login to Gigya; returns uid / signature / timestamp only."""
    url = f"https://accounts.{datacenter_domain}/accounts.login"
    data = _http_json(
        "POST",
        url,
        form={
            "apiKey": api_key,
            "loginID": email,
            "password": password,
            "format": "json",
            "targetEnv": "mobile",
        },
    )
    if not isinstance(data, dict) or "UID" not in data:
        raise MapFetchError("Gigya login failed")
    return {
        "uid": str(data["UID"]),
        "signature": str(data["UIDSignature"]),
        "timestamp": str(data["signatureTimestamp"]),
    }


def cloud_v2_login(
    *,
    http_base: str,
    gigya: dict[str, str],
    app_id: str = CLOUD_APP_ID,
) -> dict[str, Any]:
    """Exchange Gigya session for cloud robots + temporary AWS credentials."""
    url = f"{http_base.rstrip('/')}/v2/login"
    payload = {
        "app_id": app_id,
        "assume_robot_ownership": 0,
        "gigya": {
            "uid": gigya["uid"],
            "signature": gigya["signature"],
            "timestamp": gigya["timestamp"],
        },
    }
    data = _http_json(
        "POST",
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    if not isinstance(data, dict) or "credentials" not in data:
        raise MapFetchError("Cloud login failed")
    return data


def signed_get_json(
    *,
    host: str,
    path: str,
    query: str,
    region: str,
    credentials: dict[str, str],
) -> Any:
    """GET JSON from the cloud REST API with SigV4."""
    headers = _sigv4_headers(
        method="GET",
        host=host,
        path=path,
        query=query,
        region=region,
        access_key=credentials["AccessKeyId"],
        secret_key=credentials["SecretKey"],
        session_token=credentials["SessionToken"],
    )
    url = f"https://{host}{path}"
    if query:
        url = f"{url}?{query}"
    return _http_json("GET", url, headers=headers)


def list_active_pmaps(
    *,
    host: str,
    region: str,
    credentials: dict[str, str],
    blid: str,
) -> list[tuple[str, str]]:
    """Return [(pmap_id, pmapv_id), ...] for active maps."""
    path = f"/v1/{blid}/pmaps"
    query = "activeDetails=2"
    data = signed_get_json(
        host=host, path=path, query=query, region=region, credentials=credentials
    )
    if not isinstance(data, list):
        raise MapFetchError("Unexpected pmaps response")
    result: list[tuple[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        pmap_id = item.get("pmap_id")
        details = item.get("active_pmapv_details") or {}
        active = details.get("active_pmapv") or {}
        pmapv_id = active.get("pmapv_id")
        if pmap_id and pmapv_id:
            result.append((str(pmap_id), str(pmapv_id)))
    return result


def fetch_umf(
    *,
    host: str,
    region: str,
    credentials: dict[str, str],
    blid: str,
    pmap_id: str,
    pmapv_id: str,
) -> dict[str, Any]:
    """Fetch UMF map JSON for one persistent map version."""
    path = f"/v1/{blid}/pmaps/{pmap_id}/versions/{pmapv_id}/umf"
    query = "activeDetails=2"
    data = signed_get_json(
        host=host, path=path, query=query, region=region, credentials=credentials
    )
    if not isinstance(data, dict):
        raise MapFetchError("Unexpected UMF response")
    return data


def _iter_polygons(node: Any) -> list[list[tuple[float, float]]]:
    """Best-effort extraction of 2D polygons from nested UMF JSON."""
    polygons: list[list[tuple[float, float]]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in {"polygon", "geometry", "simplifiedGeometry", "coords"}:
                    poly = _as_polygon(value)
                    if poly:
                        polygons.append(poly)
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(node)
    return polygons


def _as_polygon(value: Any) -> list[tuple[float, float]] | None:
    if isinstance(value, dict):
        for key in ("coordinates", "points", "list"):
            if key in value:
                return _as_polygon(value[key])
        return None
    if not isinstance(value, list) or len(value) < 3:
        return None
    points: list[tuple[float, float]] = []
    for item in value:
        if isinstance(item, list | tuple) and len(item) >= 2:
            try:
                points.append((float(item[0]), float(item[1])))
            except (TypeError, ValueError):
                return None
        elif isinstance(item, dict) and "x" in item and "y" in item:
            try:
                points.append((float(item["x"]), float(item["y"])))
            except (TypeError, ValueError):
                return None
        else:
            return None
    return points if len(points) >= 3 else None


def render_map_png(
    umf: dict[str, Any],
    *,
    title: str,
    subtitle: str,
) -> bytes:
    """Render a simple PNG from UMF geometry (Pillow)."""
    from PIL import Image, ImageDraw

    width, height = 800, 800
    img = Image.new("RGB", (width, height), color=(245, 248, 250))
    draw = ImageDraw.Draw(img)
    polygons = _iter_polygons(umf)

    if polygons:
        xs = [x for poly in polygons for x, _ in poly]
        ys = [y for poly in polygons for _, y in poly]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1e-6)
        span_y = max(max_y - min_y, 1e-6)
        pad = 40

        def tx(x: float, y: float) -> tuple[int, int]:
            px = int(pad + (x - min_x) / span_x * (width - 2 * pad))
            py = int(height - pad - (y - min_y) / span_y * (height - 2 * pad))
            return px, py

        colors = [(64, 120, 180), (80, 160, 120), (180, 140, 60), (140, 100, 160)]
        for idx, poly in enumerate(polygons[:40]):
            pts = [tx(x, y) for x, y in poly]
            draw.polygon(pts, outline=(30, 40, 50), fill=colors[idx % len(colors)])
    else:
        draw.rectangle((40, 40, width - 40, height - 40), outline=(120, 130, 140), width=2)
        draw.text(
            (60, height // 2 - 10),
            "Map geometry unavailable; metadata only",
            fill=(60, 60, 60),
        )

    draw.rectangle((0, 0, width, 36), fill=(25, 35, 45))
    draw.text((12, 10), title[:80], fill=(240, 240, 240))
    draw.text((12, height - 28), subtitle[:100], fill=(40, 40, 40))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def fetch_map_image(
    *,
    email: str,
    password: str,
    blid: str,
    country: str = DEFAULT_COUNTRY,
) -> MapFetchResult:
    """One-shot login → map fetch → PNG. Caller must discard secrets.

    Intentionally takes email/password as locals only; nothing here writes them
    to disk or returns them in MapFetchResult.
    """
    notes: list[str] = []
    discovery = discover_endpoints(country.strip().upper() or DEFAULT_COUNTRY)
    gigya_cfg = discovery.get("gigya") or {}
    api_key = gigya_cfg.get("api_key")
    datacenter = gigya_cfg.get("datacenter_domain")
    deployments = discovery.get("deployments") or {}
    if not api_key or not datacenter or not deployments:
        raise MapFetchError("Discovery missing Gigya/deployment data")

    deployment = deployments[next(iter(deployments))]
    http_base = deployment["httpBase"]
    auth_base = deployment.get("httpBaseAuth") or http_base
    region = deployment.get("awsRegion") or "us-east-1"
    host = urllib.parse.urlparse(auth_base).netloc
    if not host:
        raise MapFetchError("Could not resolve cloud API host")

    gigya_session = gigya_login(
        email=email,
        password=password,
        api_key=str(api_key),
        datacenter_domain=str(datacenter),
    )
    # Drop account password from this frame as soon as Gigya succeeds.
    password = ""  # noqa: F841 — intentional scrub

    login = cloud_v2_login(http_base=str(http_base), gigya=gigya_session)
    gigya_session.clear()

    credentials = {
        "AccessKeyId": str(login["credentials"]["AccessKeyId"]),
        "SecretKey": str(login["credentials"]["SecretKey"]),
        "SessionToken": str(login["credentials"]["SessionToken"]),
    }
    robots = login.get("robots") or {}
    robot_name = None
    if blid in robots and isinstance(robots[blid], dict):
        robot_name = robots[blid].get("name")
    elif robots:
        notes.append("BLID not present in cloud robot list; still requesting maps by BLID")

    pmaps = list_active_pmaps(
        host=host, region=str(region), credentials=credentials, blid=blid
    )
    if not pmaps:
        # Scrub tokens before raising.
        credentials.clear()
        login.clear()
        raise MapFetchError("No active persistent maps found for this robot")

    pmap_id, pmapv_id = pmaps[0]
    if len(pmaps) > 1:
        notes.append(f"Multiple maps found; using first ({pmap_id})")

    umf = fetch_umf(
        host=host,
        region=str(region),
        credentials=credentials,
        blid=blid,
        pmap_id=pmap_id,
        pmapv_id=pmapv_id,
    )

    # Scrub cloud tokens before rendering/returning.
    credentials.clear()
    login.clear()

    fetched_at = datetime.now(tz=UTC).isoformat()
    title = robot_name or f"Roomba {blid[-6:].upper()}"
    subtitle = f"pmap={pmap_id} · {fetched_at}"
    png = render_map_png(umf, title=title, subtitle=subtitle)
    return MapFetchResult(
        png_bytes=png,
        blid=blid,
        pmap_id=pmap_id,
        pmapv_id=pmapv_id,
        fetched_at=fetched_at,
        robot_name=robot_name,
        notes=notes,
    )
