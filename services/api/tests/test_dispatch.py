"""The worker wake-up call: exact GitHub request, config hygiene, and honest result codes."""

import httpx
import pytest

from app import deps

TOKEN = "github_pat_" + "A1b2C3d4" * 10


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return httpx.Response(r, request=httpx.Request("POST", url))


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Kuulbudam/ai4elections")
    return monkeypatch


def run(monkeypatch, responses):
    rec = Recorder(responses)
    monkeypatch.setattr(deps.httpx, "post", rec)
    return deps.Dispatcher()("job-1"), rec


def test_sends_the_documented_repository_dispatch_request(env):
    result, rec = run(env, [204])
    assert result == "ok"
    call = rec.calls[0]
    assert call["url"] == "https://api.github.com/repos/Kuulbudam/ai4elections/dispatches"
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert call["headers"]["Accept"] == "application/vnd.github+json"
    assert call["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    assert call["json"] == {"event_type": "ai4e-job", "client_payload": {"job_id": "job-1"}}


def test_whitespace_pasted_into_vercel_is_stripped(env):
    env.setenv("GITHUB_DISPATCH_TOKEN", f"  {TOKEN}\n")
    env.setenv("GITHUB_REPOSITORY", " Kuulbudam/ai4elections/ \n")
    result, rec = run(env, [204])
    assert result == "ok"
    assert rec.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert rec.calls[0]["url"].endswith("/repos/Kuulbudam/ai4elections/dispatches")


@pytest.mark.parametrize("status", [401, 403, 404, 422])
def test_client_errors_are_reported_and_not_retried(env, status):
    result, rec = run(env, [status])
    assert result == f"http_{status}" and len(rec.calls) == 1


def test_server_error_is_retried_once(env):
    result, rec = run(env, [502, 204])
    assert result == "ok" and len(rec.calls) == 2


def test_network_errors(env):
    result, rec = run(env, [httpx.ConnectError("x"), httpx.ConnectError("x")])
    assert result == "network_error" and len(rec.calls) == 2


def test_not_configured_and_invalid_config(monkeypatch):
    monkeypatch.delenv("GITHUB_DISPATCH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Kuulbudam/ai4elections")
    assert deps.Dispatcher()("j") == "not_configured"
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", "https://github.com/Kuulbudam/ai4elections")
    assert deps.Dispatcher()("j") == "invalid_config"
    monkeypatch.setenv("GITHUB_REPOSITORY", "Kuulbudam/ai4elections")
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "has spaces inside token value")
    assert deps.Dispatcher()("j") == "invalid_config"


def test_result_codes_never_contain_the_token(env):
    for responses in ([401], [204], [httpx.ConnectError(TOKEN), httpx.ConnectError(TOKEN)]):
        result, _ = run(env, responses)
        assert TOKEN not in result
