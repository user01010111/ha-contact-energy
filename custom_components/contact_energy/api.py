"""Asynchronous client for Contact Energy's customer API."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

API_BASE_URL = "https://api.contact-digital-prod.net"

# Contact ships this value in the unauthenticated MyAccount web bundle as the
# default x-api-key for both customer and payment API clients. It is therefore a
# publicly distributed application/gateway identifier, not a customer secret.
# Contact has not published a formal classification. Customer data still
# requires independent username, password, and session authentication. The
# Contact may rotate this public application identifier. If authentication stops
# working, compare it with the current MyAccount bundle and update it in a release.
PUBLIC_APPLICATION_API_KEY = "wg8mXRp7kQ82aOT7mTkzl9fsULf1sEcu7WMGtn6C"

REQUEST_TIMEOUT = 30
LOGIN_FAILURE_COOLDOWN = 5
RETRYABLE_STATUSES = frozenset({408, 425, 429})


class ContactEnergyApi:
    """Async Contact Energy API client with one bounded auth retry."""

    def __init__(
        self,
        hass: HomeAssistant,
        email: str,
        password: str,
        account_id: str | None = None,
        contract_id: str | None = None,
    ) -> None:
        """Initialize the API client."""
        self._email = email
        self._password = password
        self._account_id = account_id
        self._contract_id = contract_id
        self._session = async_get_clientsession(hass)
        self._hass = hass
        self._api_token: str | None = None
        self._login_lock = asyncio.Lock()
        self._login_task: asyncio.Task[None] | None = None
        self._login_failure_until = 0.0

    @property
    def account_id(self) -> str | None:
        """Return the configured account identifier."""
        return self._account_id

    @property
    def contract_id(self) -> str | None:
        """Return the configured contract identifier."""
        return self._contract_id

    def _headers(self, *, include_session: bool) -> dict[str, str]:
        """Build request headers without exposing them to logging."""
        headers = {"x-api-key": PUBLIC_APPLICATION_API_KEY}
        if include_session and self._api_token:
            headers["session"] = self._api_token
        return headers

    async def _async_request(
        self,
        method: str,
        url: str,
        endpoint: str,
        *,
        empty_statuses: frozenset[int] = frozenset(),
        **kwargs: Any,
    ) -> Any:
        """Perform one request and preserve distinct failure categories."""
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(method, url, **kwargs) as response:
                    _LOGGER.debug(
                        "Contact API %s endpoint returned status %s",
                        endpoint,
                        response.status,
                    )
                    if response.status in empty_statuses:
                        return None
                    if response.status == 401:
                        raise InvalidAuth
                    if response.status in RETRYABLE_STATUSES or response.status >= 500:
                        raise RetryableApiError(
                            response.status,
                            _parse_retry_after(response.headers),
                        )
                    if response.status < 200 or response.status >= 300:
                        raise UnexpectedResponse(response.status)

                    try:
                        return await response.json(content_type=None)
                    except (TypeError, ValueError) as err:
                        raise MalformedResponse(
                            f"Malformed JSON from {endpoint} endpoint"
                        ) from err
        except TimeoutError:
            _LOGGER.debug("Contact API %s endpoint timed out", endpoint)
            raise CannotConnect("Request timed out") from None
        except aiohttp.ClientConnectionError:
            _LOGGER.debug("Contact API %s endpoint connection failed", endpoint)
            raise CannotConnect("Connection failed") from None
        except aiohttp.ClientError:
            _LOGGER.debug("Contact API %s endpoint request failed", endpoint)
            raise CannotConnect("Request failed") from None

    async def async_login(
        self,
        *,
        stale_token: str | None = None,
    ) -> None:
        """Authenticate with one shared attempt for each concurrent wave."""
        async with self._login_lock:
            if self._api_token and (
                stale_token is None or self._api_token != stale_token
            ):
                return
            loop = asyncio.get_running_loop()
            if self._login_task is None or (
                self._login_task.done() and loop.time() >= self._login_failure_until
            ):
                self._api_token = None
                self._login_task = self._hass.async_create_task(
                    self._async_perform_login(),
                    "contact_energy_login",
                )
            login_task = self._login_task

        try:
            await asyncio.shield(login_task)
        except ContactEnergyError:
            async with self._login_lock:
                if self._login_task is login_task:
                    self._login_failure_until = (
                        asyncio.get_running_loop().time() + LOGIN_FAILURE_COOLDOWN
                    )
            raise
        else:
            async with self._login_lock:
                if self._login_task is login_task:
                    self._login_task = None
                    self._login_failure_until = 0.0

    async def _async_perform_login(self) -> None:
        """Perform one login request and retain only the validated token."""
        result = await self._async_request(
            "POST",
            f"{API_BASE_URL}/login/v2",
            "login",
            json={"username": self._email, "password": self._password},
            headers=self._headers(include_session=False),
        )
        if not isinstance(result, Mapping):
            raise MalformedResponse("Unexpected login response")
        token = result.get("token")
        if not isinstance(token, str) or not token.strip():
            raise MalformedResponse("Login response did not contain a token")
        self._api_token = token

    async def _async_authenticated_request(
        self,
        method: str,
        url: str,
        endpoint: str,
        *,
        empty_statuses: frozenset[int] = frozenset(),
    ) -> Any:
        """Perform a request with at most one re-login and one retry."""
        if not self._api_token:
            await self.async_login()

        token_used = self._api_token
        try:
            return await self._async_request(
                method,
                url,
                endpoint,
                empty_statuses=empty_statuses,
                headers=self._headers(include_session=True),
            )
        except InvalidAuth:
            await self.async_login(stale_token=token_used)
            return await self._async_request(
                method,
                url,
                endpoint,
                empty_statuses=empty_statuses,
                headers=self._headers(include_session=True),
            )

    async def async_get_accounts(self) -> dict[str, Any]:
        """Return the authenticated account response."""
        result = await self._async_authenticated_request(
            "GET",
            f"{API_BASE_URL}/accounts/v2",
            "accounts",
        )
        if not isinstance(result, dict):
            raise MalformedResponse("Unexpected accounts response")
        return result

    async def async_get_usage(self, date: str) -> list[dict[str, Any]]:
        """Return hourly usage for one ISO date, or an empty list for no data."""
        if not self._account_id or not self._contract_id:
            raise MalformedResponse("Usage request is missing configured identifiers")

        url = (
            f"{API_BASE_URL}/usage/v2/{self._contract_id}"
            f"?ba={self._account_id}&interval=hourly&from={date}&to={date}"
        )
        result = await self._async_authenticated_request(
            "POST",
            url,
            "usage",
            empty_statuses=frozenset({204, 404}),
        )
        if result is None:
            return []
        if not isinstance(result, list) or not all(
            isinstance(point, dict) for point in result
        ):
            raise MalformedResponse("Unexpected usage response")
        return result


def _parse_retry_after(
    headers: Mapping[str, str],
    now: datetime | None = None,
) -> int | None:
    """Return a bounded Retry-After delay from seconds or an HTTP date."""
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return min(max(int(value), 1), 3600)
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    seconds = math.ceil((retry_at - current).total_seconds())
    return min(max(seconds, 1), 3600)


class ContactEnergyError(HomeAssistantError):
    """Base error for Contact Energy API failures."""


class InvalidAuth(ContactEnergyError):
    """Credentials or a refreshed session were rejected."""


class CannotConnect(ContactEnergyError):
    """The service could not be reached."""


class RetryableApiError(ContactEnergyError):
    """The service rejected a request temporarily."""

    def __init__(self, status: int, retry_after: int | None) -> None:
        """Initialize the retryable failure without retaining a request URL."""
        super().__init__(f"Temporary Contact API response ({status})")
        self.status = status
        self.retry_after = retry_after


class MalformedResponse(ContactEnergyError):
    """The service returned malformed or incomplete data."""


class UnexpectedResponse(ContactEnergyError):
    """The service returned a non-retryable unexpected status."""

    def __init__(self, status: int) -> None:
        """Initialize the response failure without retaining a request URL."""
        super().__init__(f"Unexpected Contact API response ({status})")
        self.status = status
