"""Tests for the setup block: everything every column shares (plan section 3.4).

No network and no server. Redis is a recording fake, HTTP is httpx.MockTransport, and
every secret in here is an obvious fake.
"""

import ast
import builtins
import dis
import html
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
from datetime import datetime, timedelta, timezone
from types import CodeType, SimpleNamespace
from urllib.parse import quote, quote_plus

import httpx
import notebook_ast as nbast
import pytest
import redis
from kernel_driver import drive

# Deliberately awkward: needs percent-encoding, JSON escaping and HTML escaping, and has
# a non-ASCII character. Not shaped like a real key, so only the registry can mask it.
TRICKY = 'p@ss/w"rd&<x>é#+'
ADMIN_KEY = "adminKEY-fake-0123456789"
AGENT_KEY = "minted-agent-fake-9f8e7d6c5b4a"
DB_PASSWORD = "db-pass-fake-777"

LC = "https://lc.example/v1/caches/CACHE1"
AM = "https://am.example/v1/stores/STORE1"
CR = "https://cr.example/context-surfaces/api/v1"


class Btn:
    """Stands in for a mo.ui.run_button: only .value matters."""

    def __init__(self, value):
        self.value = value


def refused(nb, seen, *args, **kwargs):
    """Assert http_call refuses this request and never lets it reach the transport."""
    before = len(seen)
    with pytest.raises(nb.ServiceError) as caught:
        nb.http_call(*args, **kwargs)
    assert caught.value.kind == "refused", caught.value
    assert len(seen) == before, "a refused request must never reach the transport"
    return caught.value


def iso_in(seconds):
    """An RFC 3339 timestamp this many seconds from now (negative for the past)."""
    when = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return when.isoformat(timespec="seconds")


def other_run(nb):
    """A run id that is not this session's."""
    return "deadbeef" if nb.RUN != "deadbeef" else "cafef00d"


def mint_key(nb, extra=None, **kwargs):
    """A create-agent-key call that passes the choke points: a ledgered surface, a short expiry."""
    nb.ledger_add("cr", "surface", "S1")
    body = {"name": "k", "expires_at": iso_in(3600)} | (extra or {})
    return nb.http_call("cr", "POST", f"{CR}/context-surfaces/S1/agent-keys", json=body, **kwargs)


def create_surface_body(nb, **changes):
    """A create-surface request that satisfies every rule; tests spoil it one field at a time."""
    body = {
        "name": nb.RUN_TAG,
        "metadata": {"created_by": nb.CR_CREATED_BY, "run": nb.RUN},
        "data_model": {
            "entities": [
                {"name": "Customer", "redis_key_template": f"{nb.CR_PREFIX}customer:{{customer_id}}"},
                {"name": "Order", "redis_key_template": f"{nb.CR_PREFIX}order:{{order_id}}"},
            ]
        },
        "data_source": {
            "type": "redis",
            "connection_config": {
                "addr": "h:1", "username": "default", "password": DB_PASSWORD,
                "db": 0, "pool_size": 2, "min_idle_conns": 0,
            },
        },
    }  # fmt: skip
    return body | changes


def am_memory(nb, n=1, **changes):
    """A bulk-create item that satisfies every rule."""
    item = {"id": f"{nb.AM_OWNER}-m{n}", "text": "likes ridge trails", "ownerId": nb.AM_OWNER,
            "namespace": nb.AM_NAMESPACE}  # fmt: skip
    return item | changes


def am_event(nb, **changes):
    """A session event that satisfies every rule."""
    event = {"sessionId": f"{nb.AM_OWNER}-s1", "actorId": nb.AM_OWNER, "role": "USER",
             "content": [{"text": "hello"}], "createdAt": "2026-09-29T12:00:00Z"}  # fmt: skip
    return event | changes


def scoped_search(nb, **changes):
    """A long-term-memory search that satisfies every rule."""
    return {"text": "trail shoes", "filter": {"ownerId": {"eq": nb.AM_OWNER}}} | changes


def foreign_surface(nb, **changes):
    """A stale surface of another run, as the list call returns it."""
    run = other_run(nb)
    surface = {"id": "ORPHAN1", "name": f"iris-basics-{run}", "created_at": iso_in(-3 * 3600),
               "metadata": {"created_by": nb.CR_CREATED_BY, "run": run}}  # fmt: skip
    return surface | changes


# ---- settings -----------------------------------------------------------------------
class TestEnvParser:
    def test_crlf_quotes_and_empty(self, nb):
        text = (
            'LANGCACHE_HOST="https://a.example"\r\n'
            "LANGCACHE_CACHE_ID='abc'\r\n"
            "# a comment\r\n"
            "export REDIS_HOST=localhost\r\n"
            'MEMORY_STORE_ID=""\r\n'
            "REDIS_PORT= 6380 \r\n"
            "REDIS_PASSWORD=a=b=c\r\n"
            "\r\n"
            "not a setting line\r\n"
        )
        assert nb.parse_env(text) == {
            "LANGCACHE_HOST": "https://a.example",
            "LANGCACHE_CACHE_ID": "abc",
            "REDIS_HOST": "localhost",
            "REDIS_PORT": "6380",
            "REDIS_PASSWORD": "a=b=c",
        }

    def test_bare_cr_and_lf_line_endings(self, nb):
        assert nb.parse_env("REDIS_HOST=a\rREDIS_PORT=1\nREDIS_DB=2") == {
            "REDIS_HOST": "a", "REDIS_PORT": "1", "REDIS_DB": "2",
        }  # fmt: skip

    def test_empty_and_whitespace_only_are_unset(self, nb):
        assert nb.parse_env("REDIS_HOST=\nREDIS_PORT=   \nREDIS_DB=''\n") == {}
        assert nb.parse_env("") == {}

    def test_an_inline_comment_is_not_stripped(self, nb):
        # python-dotenv, which marimo's own loader uses whenever it is installed, would read
        # "1" here; marimo's fallback parser reads "1 # note". The loaders disagree, hence the
        # advice to keep comments on their own lines. A secret carrying one has a space in it,
        # so validate_secret rejects it.
        assert nb.parse_env("REDIS_DB=1 # note") == {"REDIS_DB": "1 # note"}

    def test_only_allowlisted_names_are_kept(self, nb):
        text = "CTX_SURFACE_ID=s1\nMCP_AGENT_KEY=k\nOPENAI_API_KEY=o\nDEMO_X=1\nREDIS_HOST=h\n"
        assert nb.parse_env(text) == {"REDIS_HOST": "h"}


class TestEnvLookup:
    def test_allowlist_is_exactly_the_contract(self, nb):
        assert nb.ENV_ALLOWLIST == {
            "REDIS_URL", "REDIS_HOST", "REDIS_PORT", "REDIS_USERNAME", "REDIS_PASSWORD",
            "REDIS_SSL", "REDIS_DB", "LANGCACHE_HOST", "LANGCACHE_CACHE_ID",
            "LANGCACHE_API_KEY", "LANGCACHE_SCOPE_ATTR", "CTX_ADMIN_KEY", "CTX_API_URL",
            "CTX_MCP_URL", "MEMORY_API_BASE_URL", "MEMORY_STORE_ID", "MEMORY_API_KEY",
        }  # fmt: skip
        assert nb.SECRET_ENV <= nb.ENV_ALLOWLIST

    @pytest.mark.parametrize(
        "name",
        ["CTX_SURFACE_ID", "MCP_AGENT_KEY", "MEMORY_OWNER_ID", "MEMORY_NAMESPACE",
         "OPENAI_API_KEY", "DEMO_PASSWORD", "PATH", "HOME"],
    )  # fmt: skip
    def test_never_read_names_raise_even_when_set(self, nb, monkeypatch, tmp_path, name):
        settings = tmp_path / "s.env"
        settings.write_text(f"{name}=from-file\n", encoding="utf-8")
        monkeypatch.setenv("IRIS_BASICS_ENV_FILE", str(settings))
        monkeypatch.setenv(name, "from-environ")
        with pytest.raises(KeyError):
            nb.env(name)
        assert name not in nb.ENV_ALLOWLIST

    def test_environ_wins_and_empty_environ_falls_through(self, nb, monkeypatch, tmp_path):
        settings = tmp_path / "s.env"
        settings.write_text("REDIS_HOST=file-host\nREDIS_PORT=6111\n", encoding="utf-8")
        monkeypatch.setenv("IRIS_BASICS_ENV_FILE", str(settings))
        monkeypatch.setenv("REDIS_HOST", "environ-host")
        monkeypatch.setenv("REDIS_PORT", "  ")  # empty counts as unset
        assert nb.env("REDIS_HOST") == "environ-host"
        assert nb.env("REDIS_PORT") == "6111"
        assert nb.env("REDIS_DB") == ""

    def test_the_file_is_never_written_into_environ(self, nb, monkeypatch, tmp_path):
        settings = tmp_path / "s.env"
        settings.write_text("LANGCACHE_HOST=h\n", encoding="utf-8")
        monkeypatch.setenv("IRIS_BASICS_ENV_FILE", str(settings))
        assert nb.env("LANGCACHE_HOST") == "h"
        assert "LANGCACHE_HOST" not in os.environ

    def test_bom_crlf_file_and_edits_are_picked_up(self, nb, monkeypatch, tmp_path):
        settings = tmp_path / "s.env"
        settings.write_bytes(b"\xef\xbb\xbfREDIS_HOST=one\r\n")
        monkeypatch.setenv("IRIS_BASICS_ENV_FILE", str(settings))
        assert nb.env("REDIS_HOST") == "one"
        settings.write_bytes(b"\xef\xbb\xbfREDIS_HOST=two-longer\r\n")
        assert nb.env("REDIS_HOST") == "two-longer"

    def test_missing_settings_file_is_not_an_error(self, nb, monkeypatch, tmp_path):
        monkeypatch.setenv("IRIS_BASICS_ENV_FILE", str(tmp_path / "nope.env"))
        assert nb.env("REDIS_HOST") == ""
        assert nb.env_present() == []

    def test_default_dotenv_sits_next_to_the_notebook(self, nb, monkeypatch, tmp_path):
        (tmp_path / ".env").write_text("REDIS_HOST=beside\n", encoding="utf-8")
        monkeypatch.delenv("IRIS_BASICS_ENV_FILE")
        monkeypatch.delenv("IRIS_BASICS_NO_DOTENV")
        monkeypatch.setattr(nb.mo, "notebook_dir", lambda: tmp_path)
        assert nb.env("REDIS_HOST") == "beside"
        monkeypatch.setenv("IRIS_BASICS_NO_DOTENV", "1")
        assert nb.env("REDIS_HOST") == ""

    def test_notebook_dir_none_falls_back_to_cwd(self, nb, monkeypatch, tmp_path):
        (tmp_path / ".env").write_text("REDIS_HOST=cwd\n", encoding="utf-8")
        monkeypatch.delenv("IRIS_BASICS_ENV_FILE")
        monkeypatch.delenv("IRIS_BASICS_NO_DOTENV")
        monkeypatch.setattr(nb.mo, "notebook_dir", lambda: None)
        monkeypatch.chdir(tmp_path)
        assert nb.env("REDIS_HOST") == "cwd"

    def test_no_dotenv_does_not_disable_an_explicit_file(self, nb, monkeypatch, tmp_path):
        settings = tmp_path / "s.env"
        settings.write_text("REDIS_HOST=explicit\n", encoding="utf-8")
        monkeypatch.setenv("IRIS_BASICS_ENV_FILE", str(settings))
        monkeypatch.setenv("IRIS_BASICS_NO_DOTENV", "1")
        assert nb.env("REDIS_HOST") == "explicit"

    def test_secret_settings_are_registered_for_scrubbing(self, nb, monkeypatch):
        monkeypatch.setenv("LANGCACHE_API_KEY", ADMIN_KEY)
        monkeypatch.setenv("REDIS_HOST", "hostname-fake")
        nb.env("LANGCACHE_API_KEY")
        nb.env("REDIS_HOST")
        assert nb.scrub(f"x {ADMIN_KEY} x") == "x **** x"
        assert nb.scrub("x hostname-fake x") == "x hostname-fake x"  # not a secret

    def test_env_present_lists_names_only(self, nb, monkeypatch):
        monkeypatch.setenv("LANGCACHE_API_KEY", ADMIN_KEY)
        monkeypatch.setenv("REDIS_DB", "3")
        assert nb.env_present() == ["LANGCACHE_API_KEY", "REDIS_DB"]


class TestRedisUrl:
    def test_redis_url_wins_and_registers_the_password(self, nb, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://default:p%40ss%2Fw@h:6379/0")
        monkeypatch.setenv("REDIS_HOST", "ignored")
        assert nb.redis_url_from_env() == "redis://default:p%40ss%2Fw@h:6379/0"
        assert nb.scrub("bad password p@ss/w for user") == "bad password **** for user"

    def test_composed_from_split_fields_with_encoded_userinfo(self, nb, monkeypatch):
        for name, value in {
            "REDIS_HOST": "h.example", "REDIS_PORT": "6380", "REDIS_USERNAME": "us er",
            "REDIS_PASSWORD": "p@ss/w:rd#", "REDIS_DB": "2",
        }.items():  # fmt: skip
            monkeypatch.setenv(name, value)
        url = nb.redis_url_from_env()
        assert url == "redis://us%20er:p%40ss%2Fw%3Ard%23@h.example:6380/2"
        assert nb.scrub("p@ss/w:rd#") == "****"

    def test_ssl_flag_switches_scheme_and_defaults_apply(self, nb, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "h")
        monkeypatch.setenv("REDIS_SSL", "True")
        assert nb.redis_url_from_env() == "rediss://default@h:6379"
        monkeypatch.setenv("REDIS_SSL", "false")
        assert nb.redis_url_from_env().startswith("redis://")

    def test_nothing_configured_is_an_empty_string(self, nb):
        assert nb.redis_url_from_env() == ""


# ---- secrets --------------------------------------------------------------------------
class TestSecret:
    def test_repr_and_str_are_masked(self, nb):
        secret = nb.Secret("abcd1234wxyz")
        for shown in (repr(secret), str(secret), f"{secret}", "%s %r" % (secret, secret)):
            assert "abcd1234wxyz" not in shown
        assert str(secret) == "••••wxyz"
        assert repr(secret) == "Secret(••••wxyz)"
        assert secret.reveal() == "abcd1234wxyz"

    def test_containers_and_short_values_do_not_leak(self, nb):
        assert "abcd1234wxyz" not in repr({"k": [nb.Secret("abcd1234wxyz")]})
        short = nb.Secret("hunter22")  # 8 chars: a tail would give away half
        assert str(short) == "••••" and "hunter" not in repr(short)

    def test_is_not_a_str_and_has_no_dict(self, nb):
        secret = nb.Secret("abcd1234wxyz")
        assert not isinstance(secret, str)
        assert not hasattr(secret, "__dict__")

    def test_truthiness_and_registration(self, nb):
        assert nb.Secret("abcd1234wxyz") and not nb.Secret("")
        assert nb.scrub("see abcd1234wxyz") == "see ****"

    def test_only_strings_and_secrets_can_be_registered(self, nb):
        # str(None) or str(6379) would register "None" or "6379" and mangle all later output.
        for junk in (None, 6379, b"bytes-key-1234", ["list-key-1234"]):
            with pytest.raises(TypeError):
                nb.register_secret(junk)
            with pytest.raises(TypeError):
                nb.Secret(junk)
        assert len(nb.SECRETS) == 0
        assert nb.scrub("None of the 6379 ports") == "None of the 6379 ports"
        assert nb.register_secret(nb.Secret("abcd1234wxyz")) == "abcd1234wxyz"
        assert nb.scrub("abcd1234wxyz") == "****"

    @pytest.mark.parametrize(
        "stray",
        [" ", "\n", "\t", "\r", "\x00", "\x1b", "\x7f", "\u00a0", "\u2028", "\u200b", "\ufeff"],
    )
    def test_validate_rejects_whitespace_and_control_characters(self, nb, stray):
        for bad in (f"KEYBODY{stray}TAIL", f"{stray}KEYBODY", f"KEYBODY{stray}"):
            with pytest.raises(ValueError) as caught:
                nb.validate_secret("LANGCACHE_API_KEY", bad)
            assert "LANGCACHE_API_KEY" in str(caught.value)
            assert "KEYBODY" not in str(caught.value), "the value must never be echoed"

    def test_validate_accepts_a_plausible_key_and_rejects_empty(self, nb):
        assert nb.validate_secret("X", ADMIN_KEY) == ADMIN_KEY
        with pytest.raises(ValueError):
            nb.validate_secret("X", "")

    def test_resolve_secret_prefers_the_field_then_the_environment(self, nb, monkeypatch):
        assert nb.resolve_secret("MEMORY_API_KEY") is None
        assert nb.resolve_secret("MEMORY_API_KEY", None) is None
        monkeypatch.setenv("MEMORY_API_KEY", "env-key-fake-1234")
        assert nb.resolve_secret("MEMORY_API_KEY").reveal() == "env-key-fake-1234"
        typed = nb.Secret("typed-key-fake-99")
        assert nb.resolve_secret("MEMORY_API_KEY", typed).reveal() == "typed-key-fake-99"
        emptied = nb.Secret("")  # a field that was typed in and cleared again
        assert nb.resolve_secret("MEMORY_API_KEY", emptied).reveal() == "env-key-fake-1234"
        with pytest.raises(ValueError):
            nb.resolve_secret("MEMORY_API_KEY", nb.Secret("two words"))

    @pytest.mark.parametrize("typed", ["typed-in-a-plain-text-field", "", b"bytes-key-1234"])
    def test_resolve_secret_refuses_anything_but_a_secret(self, nb, typed):
        # A str would mean a field that keeps the key as text, which the Variables panel shows.
        with pytest.raises(TypeError):
            nb.resolve_secret("MEMORY_API_KEY", typed)

    def test_secret_hint_names_the_variable_and_masks_the_value(self, nb, monkeypatch):
        assert nb.secret_hint("LANGCACHE_API_KEY") == "paste LANGCACHE_API_KEY"
        monkeypatch.setenv("LANGCACHE_API_KEY", "abcd1234wxyz")
        assert nb.secret_hint("LANGCACHE_API_KEY") == "using $LANGCACHE_API_KEY (••••wxyz)"

    def test_the_redis_url_hint_follows_the_split_fields_the_column_falls_back_on(
        self, nb, monkeypatch
    ):
        assert nb.secret_hint("REDIS_URL") == "paste REDIS_URL"
        for name, value in {
            "REDIS_HOST": "db.example", "REDIS_PORT": "12345", "REDIS_PASSWORD": "pw-fake-9f3a1c",
        }.items():  # fmt: skip
            monkeypatch.setenv(name, value)
        hint = nb.secret_hint("REDIS_URL")
        assert hint == "using $REDIS_HOST, $REDIS_PASSWORD (••••2345)" and "pw-fake" not in hint
        assert "$REDIS_HOST" in html.unescape(nb.SecretText("REDIS_URL").text)
        monkeypatch.setenv("REDIS_URL", "redis://default:url-pw-fake-5678@h:6379")
        assert nb.secret_hint("REDIS_URL") == "using $REDIS_URL (••••6379)"

    def test_only_the_redis_url_hint_looks_at_the_split_fields(self, nb, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "db.example")
        assert nb.secret_hint("LANGCACHE_API_KEY") == "paste LANGCACHE_API_KEY"


class TestSecretText:
    """The password field every column uses for a secret."""

    def test_its_value_is_none_until_something_is_typed_then_a_secret(self, nb):
        field = nb.SecretText("LANGCACHE_API_KEY", label="API key")
        assert field.value is None
        field._update("  typed-key-fake-0123456789 \n")  # what the frontend sends on Enter
        assert isinstance(field.value, nb.Secret)
        assert field.value.reveal() == "typed-key-fake-0123456789"  # stripped
        assert nb.scrub("oops typed-key-fake-0123456789") == "oops ****"  # and registered
        field._update("   ")
        assert field.value is None

    def test_it_is_a_password_field_whose_placeholder_names_the_variable(self, nb, monkeypatch):
        empty = html.unescape(nb.SecretText("LANGCACHE_API_KEY").text)
        assert "paste LANGCACHE_API_KEY" in empty and '"password"' in empty
        monkeypatch.setenv("LANGCACHE_API_KEY", "abcd1234wxyz")
        shown = html.unescape(nb.SecretText("LANGCACHE_API_KEY").text)
        assert "using $LANGCACHE_API_KEY (••••wxyz)" in shown and "abcd1234" not in shown

    def test_it_feeds_resolve_secret(self, nb):
        field = nb.SecretText("MEMORY_API_KEY")
        assert nb.resolve_secret("MEMORY_API_KEY", field.value) is None
        field._update("typed-key-fake-99")
        assert nb.resolve_secret("MEMORY_API_KEY", field.value).reveal() == "typed-key-fake-99"
        field._update("two words")
        with pytest.raises(ValueError):
            nb.resolve_secret("MEMORY_API_KEY", field.value)


class TestScrub:
    SPELLINGS = {
        "raw": lambda s: s,
        "percent": lambda s: quote(s, safe=""),
        "percent-keeping-slash": lambda s: quote(s),
        "plus": quote_plus,
        "json": lambda s: json.dumps(s)[1:-1],
        "json-unicode": lambda s: json.dumps(s, ensure_ascii=False)[1:-1],
        "html": html.escape,
        "html-unquoted": lambda s: html.escape(s, quote=False),
    }

    @pytest.mark.parametrize("form", SPELLINGS)
    def test_every_spelling_of_a_registered_secret_is_masked(self, nb, form):
        nb.register_secret(TRICKY)
        assert nb.scrub(f"before {self.SPELLINGS[form](TRICKY)} after") == "before **** after"

    def test_masks_inside_a_full_json_document_and_url(self, nb):
        nb.register_secret(TRICKY)
        document = json.dumps(
            {"detail": f"bad password {TRICKY}", "url": f"redis://u:{quote(TRICKY, safe='')}@h"}
        )
        assert "p@ss" not in nb.scrub(document) and "p%40ss" not in nb.scrub(document)

    def test_short_values_are_not_registered(self, nb):
        nb.register_secret("abc")
        assert nb.scrub("abc def") == "abc def"

    def test_registry_changes_take_effect_immediately(self, nb):
        assert nb.scrub("value-one-1234") == "value-one-1234"
        nb.register_secret("value-one-1234")
        assert nb.scrub("value-one-1234") == "****"
        nb.register_secret("value-two-5678")
        assert nb.scrub("value-one-1234 value-two-5678") == "**** ****"
        nb.SECRETS.clear()
        assert nb.scrub("value-one-1234") == "value-one-1234"

    def test_longer_spelling_wins_over_a_shorter_one_it_contains(self, nb):
        nb.register_secret("abcd")
        nb.register_secret("abcd-and-more")
        assert nb.scrub("x abcd-and-more y") == "x **** y"

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("key cs_admin_AbC123xyz here", "key **** here"),
            ("key cs_agent_Q9z-8_x here", "key **** here"),
            ("Authorization: Bearer abc.DEF-1 x", "Authorization: Bearer **** x"),
            ("authorization: bearer abc.DEF-1", "authorization: Bearer ****"),
            ('-H "Authorization: Bearer tok-9"', '-H "Authorization: Bearer ****"'),
            (  # an as_curl placeholder is kept, so the curl still pastes
                '-H "Authorization: Bearer $LANGCACHE_API_KEY"',
                '-H "Authorization: Bearer $LANGCACHE_API_KEY"',
            ),
            ("redis://default:hunter22@host:6379/0", "redis://****@host:6379/0"),
            ("redis://:hunter2pw@host:6379/0", "redis://****@host:6379/0"),  # empty username
            ("rediss://us:p%40ss@h", "rediss://****@h"),
            ("redis://localhost:6379", "redis://localhost:6379"),
            ("redis://user@host:6379", "redis://user@host:6379"),  # no password
            ("redis://:@host", "redis://:@host"),  # an empty password is nothing to hide
            ("cs_admin_ is the prefix", "cs_admin_ is the prefix"),
            ("nothing to hide", "nothing to hide"),
        ],
    )
    def test_shapes_are_masked_without_registration(self, nb, raw, expected):
        assert nb.scrub(raw) == expected

    def test_non_strings_are_stringified(self, nb):
        assert nb.scrub(None) == "None"
        assert nb.scrub(42) == "42"

    def test_service_error_message_is_scrubbed(self, nb):
        nb.register_secret(TRICKY)
        error = nb.ServiceError("am", 401, "http", f"key {TRICKY} rejected")
        assert TRICKY not in str(error) and TRICKY not in error.detail
        assert str(error) == "am HTTP 401 http: key **** rejected"
        assert str(nb.ServiceError("rs", None, "connect", "down")) == "rs connect: down"


# ---- redaction ----------------------------------------------------------------------
class TestRedact:
    def test_headers_by_name(self, nb):
        out = nb.redact({"Authorization": "Bearer x", "X-API-Key": "y", "x_api_key": "z",
                         "Content-Type": "application/json", "Cookie": "c=1"})  # fmt: skip
        assert out == {"Authorization": "****", "X-API-Key": "****", "x_api_key": "****",
                       "Content-Type": "application/json", "Cookie": "****"}  # fmt: skip

    @pytest.mark.parametrize(
        "name",
        [
            "password",
            "pass",
            "key",
            "api_key",
            "apiKey",
            "API-KEY",
            "token",
            "secret",
            "authorization",
        ],
    )
    def test_every_listed_key_name_is_redacted_at_any_depth(self, nb, name):
        out = nb.redact({"a": [{"b": {name: "value-1234"}}]})
        assert out == {"a": [{"b": {name: "****"}}]}

    def test_fake_create_agent_key_response(self, nb):
        reply = {"id": "ak_1", "key": AGENT_KEY, "name": "iris-basics", "context_surface_id": "s1",
                 "key_type": "agent", "created_at": "2026-09-29T00:00:00Z"}  # fmt: skip
        out = nb.redact(reply)
        assert out["key"] == "****"
        assert out["id"] == "ak_1" and out["key_type"] == "agent"
        assert AGENT_KEY not in json.dumps(out)

    def test_create_surface_body_masks_all_of_connection_config_except_addr(self, nb):
        body = {
            "name": "iris-basics-x",
            "data_source": {
                "type": "redis",
                "connection_config": {
                    "addr": "host.example:6379", "username": "default", "password": DB_PASSWORD,
                    "db": 0, "tls_enabled": False, "pool_size": 2, "extra": {"nested": "v"},
                },
            },
        }  # fmt: skip
        out = nb.redact(body)
        config = out["data_source"]["connection_config"]
        assert config["addr"] == "host.example:6379"
        assert all(v == "****" for k, v in config.items() if k != "addr")
        assert DB_PASSWORD not in json.dumps(out)
        assert out["data_source"]["type"] == "redis" and out["name"] == "iris-basics-x"

    def test_connection_config_that_is_not_a_dict_is_masked(self, nb):
        assert nb.redact({"connection_config": f"redis://u:{DB_PASSWORD}@h"}) == {
            "connection_config": "****"
        }

    def test_strings_are_scrubbed_and_bytes_elided_and_input_untouched(self, nb):
        nb.register_secret(TRICKY)
        original = {"detail": f"bad {TRICKY}", "blob": b"\x00" * 100, "ok": [1, 2.5, None, True]}
        out = nb.redact(original)
        assert out == {"detail": "bad ****", "blob": "<100 bytes>", "ok": [1, 2.5, None, True]}
        assert original["detail"] == f"bad {TRICKY}"


# ---- http_call ----------------------------------------------------------------------
class TestHttpCall:
    def test_credentials_come_back_sealed_and_are_traced_redacted(self, nb, mock_http):
        reply = {"id": "ak_1", "key": AGENT_KEY, "key_type": "agent"}
        seen = mock_http(status=201, json=reply)
        status, body = mint_key(nb, headers={"X-API-Key": ADMIN_KEY})
        assert status == 201 and body["key"].reveal() == AGENT_KEY  # the caller can still use it
        assert AGENT_KEY not in repr(body) and AGENT_KEY not in str(body)
        assert body["id"] == "ak_1" and body["key_type"] == "agent"
        call = nb.HTTP_TRACE[-1]
        assert call.response["key"] == "****"
        assert call.request["headers"] == {"X-API-Key": "****"}
        assert call.request["json"]["name"] == "k"
        assert (call.component, call.method, call.status) == ("cr", "POST", 201)
        assert seen[0].headers["x-api-key"] == ADMIN_KEY  # ...and the wire got the real one

    def test_only_credential_fields_are_sealed(self, nb, mock_http):
        mock_http(json={
            "items": [{"name": "n", "token": "tok-fake-1234", "password": "pw-fake-1234"}],
            "nested": {"apiKey": "ak-fake-1234"},
            "key": "a-bare-key-is-just-a-field",  # no key_type beside it
            "count": 3,
        })  # fmt: skip
        _, body = nb.http_call("cr", "GET", f"{CR}/context-surfaces")
        assert body["items"][0]["token"].reveal() == "tok-fake-1234"
        assert body["items"][0]["password"].reveal() == "pw-fake-1234"
        assert body["nested"]["apiKey"].reveal() == "ak-fake-1234"
        assert body["items"][0]["name"] == "n" and body["count"] == 3
        assert body["key"] == "a-bare-key-is-just-a-field"

    def test_minted_keys_are_learned_so_later_text_is_scrubbed(self, nb, mock_http):
        mock_http(status=201, json={"id": "ak_1", "key": AGENT_KEY, "key_type": "agent"})
        mint_key(nb)
        assert nb.scrub(f"agent said {AGENT_KEY}") == "agent said ****"

    def test_request_credentials_are_learned_from_headers_and_bodies(self, nb, mock_http):
        mock_http(json={})
        nb.http_call("lc", "POST", f"{LC}/entries/search",
                     headers={"Authorization": f"Bearer {ADMIN_KEY}"}, json={"password": DB_PASSWORD})  # fmt: skip
        assert nb.scrub(f"{ADMIN_KEY} / {DB_PASSWORD}") == "**** / ****"

    def test_create_surface_password_never_reaches_a_trace_or_view(self, nb, mock_http):
        mock_http(status=201, json={"id": "S1", "name": "iris-basics-x", "tools": ["a"]})
        nb.http_call("cr", "POST", f"{CR}/context-surfaces", headers={"X-API-Key": ADMIN_KEY},
                     json=create_surface_body(nb), timeout=nb.T_WRITE)  # fmt: skip
        call = nb.HTTP_TRACE[-1]
        for shown in (
            repr(call), nb.as_curl(call), nb.wire_view("w", call).text, json.dumps(call.request),
        ):  # fmt: skip
            assert DB_PASSWORD not in shown and ADMIN_KEY not in shown
        config = call.request["json"]["data_source"]["connection_config"]
        assert config["addr"] == "h:1" and config["password"] == "****"
        assert all(value == "****" for key, value in config.items() if key != "addr")

    def test_error_reply_keeps_type_and_detail_and_is_scrubbed(self, nb, mock_http):
        nb.register_secret(TRICKY)
        mock_http(status=424, json={"type": "/errors/model-quota-exceeded",
                                    "detail": f"quota gone for {TRICKY}", "status": 424})  # fmt: skip
        with pytest.raises(nb.ServiceError) as caught:
            nb.http_call("am", "POST", f"{AM}/long-term-memory/search", json=scoped_search(nb))
        error = caught.value
        assert (error.component, error.status, error.kind) == ("am", 424, "http")
        assert "/errors/model-quota-exceeded" in str(error) and "quota gone for ****" in str(error)
        assert TRICKY not in str(error)
        assert nb.HTTP_TRACE[-1].status == 424

    def test_json_rpc_error_shape_is_readable(self, nb, mock_http):
        mock_http(status=400, json={"error": {"code": -32602, "message": "invalid params: enum"}})
        with pytest.raises(nb.ServiceError, match="invalid params: enum"):
            nb.http_call("cr", "POST", "https://mcp.example/mcp", json={"method": "tools/call"})

    def test_expect_widens_what_counts_as_success(self, nb, mock_http):
        mock_http(status=404, json={"type": "/errors/cache/entry/not-found"})
        entry = "e" * 32
        nb.ledger_add("lc", "entry", entry)
        assert nb.http_call("lc", "DELETE", f"{LC}/entries/{entry}", expect=(204, 404))[0] == 404
        with pytest.raises(nb.ServiceError):
            nb.http_call("lc", "DELETE", f"{LC}/entries/{entry}")

    @pytest.mark.parametrize(
        "handler_error, kind",
        [(httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectError("no route"), "network"),
         (RuntimeError("weird"), "network")],
    )  # fmt: skip
    def test_transport_failures_become_service_errors(self, nb, mock_http, handler_error, kind):
        def boom(request):
            raise handler_error

        mock_http(boom)
        with pytest.raises(nb.ServiceError) as caught:
            nb.http_call("lc", "POST", f"{LC}/entries/search", json={"prompt": "p"})
        assert caught.value.kind == kind and caught.value.status is None
        assert (
            caught.value.__context__ is None and caught.value.__cause__ is None
        )  # no chain that could carry a secret
        assert nb.HTTP_TRACE[-1].status == 0

    def test_a_failure_that_echoes_a_secret_is_scrubbed_in_the_trace_too(self, nb, mock_http):
        def boom(request):
            # httpx and the OS put the URL, query string included, into their messages
            raise httpx.ConnectError(f"cannot reach {request.url}")

        mock_http(boom)
        with pytest.raises(nb.ServiceError) as caught:
            nb.http_call(
                "lc", "POST", f"{LC}/entries/search",
                headers={"Authorization": f"Bearer {ADMIN_KEY}"}, params={"api_key": ADMIN_KEY},
            )  # fmt: skip
        assert ADMIN_KEY not in str(caught.value)
        assert ADMIN_KEY not in repr(nb.HTTP_TRACE)
        assert nb.HTTP_TRACE[-1].response.startswith(
            "ConnectError: cannot reach https://lc.example"
        )

    def test_body_shapes(self, nb, mock_http):
        mock_http(status=204)
        assert nb.http_call("lc", "POST", f"{LC}/entries/search") == (204, None)
        mock_http(text="plain words")
        assert nb.http_call("lc", "POST", f"{LC}/entries/search") == (200, "plain words")

    def test_delete_carries_its_json_body(self, nb, mock_http):
        seen = mock_http(json={"deletedEntriesCount": 1})
        nb.set_lc_scope("scope")
        nb.http_call("lc", "DELETE", f"{LC}/entries", json={"attributes": {"scope": nb.RUN_TAG}})
        assert seen[0].method == "DELETE"
        assert json.loads(seen[0].content) == {"attributes": {"scope": nb.RUN_TAG}}

    def test_params_are_sent_and_traced(self, nb, mock_http):
        seen = mock_http(json={})
        nb.http_call("cr", "GET", f"{CR}/context-surfaces", params={"page_size": 5})
        assert seen[0].url.query == b"page_size=5"
        assert nb.HTTP_TRACE[-1].url.endswith("?page_size=5")

    def test_the_trace_shows_the_address_exactly_as_it_was_sent(self, nb, mock_http):
        # httpx lets params replace the URL's own query, and leaves it alone without params.
        seen = mock_http(json={})
        nb.http_call("cr", "GET", f"{CR}/context-surfaces?stale=1", params={"page_size": 5})
        nb.http_call("cr", "GET", f"{CR}/context-surfaces?kept=1")
        assert [r.url.query for r in seen] == [b"page_size=5", b"kept=1"]
        assert [c.url.partition("?")[2] for c in nb.HTTP_TRACE] == ["page_size=5", "kept=1"]

    @pytest.mark.parametrize(
        "url", ["https://h.example:{}/x", "https://h.example/{}\x00", "http://[::1{}/x"]
    )
    def test_a_malformed_url_is_a_traced_service_error_that_quotes_nothing(
        self, nb, mock_http, url
    ):
        pasted = "pasted-credential-fake-98765"  # what a mis-paste into a host field looks like
        seen = mock_http(json={})
        with pytest.raises(nb.ServiceError) as caught:
            nb.http_call("lc", "POST", url.format(pasted) + "/entries/search")
        error = caught.value
        assert (error.kind, error.status, error.component) == ("config", None, "lc")
        assert error.__context__ is None and error.__cause__ is None
        assert seen == [] and nb.HTTP_TRACE[-1].status == 0
        assert pasted not in str(error) and pasted not in repr(nb.HTTP_TRACE)

    def test_timeout_is_passed_through(self, nb, mock_http):
        seen = mock_http(json={})
        nb.http_call("cr", "GET", f"{CR}/context-surfaces", timeout=nb.T_WRITE)
        assert seen[0].extensions["timeout"]["read"] == nb.T_WRITE == 30.0
        assert nb.T_READ == 10.0

    def test_unknown_component_is_a_programming_error(self, nb, mock_http):
        seen = mock_http(json={})
        with pytest.raises(ValueError):
            nb.http_call("langcache", "DELETE", f"{LC}/flush")
        assert seen == []

    def test_trace_is_bounded(self, nb, mock_http):
        mock_http(json={"ok": 1})
        for _ in range(nb.HTTP_TRACE.maxlen + 5):
            nb.http_call("lc", "POST", f"{LC}/entries/search")
        assert len(nb.HTTP_TRACE) == nb.HTTP_TRACE.maxlen
        assert nb.HTTP_TRACE[-1].url.endswith("/entries/search")

    def test_as_curl_uses_env_placeholders_and_is_pasteable(self, nb, mock_http):
        mock_http(json={})
        nb.http_call("lc", "POST", f"{LC}/entries/search", headers={"Authorization": f"Bearer {ADMIN_KEY}",
                     "Content-Type": "application/json"}, json={"prompt": "it's"})  # fmt: skip
        curl = nb.as_curl(nb.HTTP_TRACE[-1])
        assert ADMIN_KEY not in curl
        assert '-H "Authorization: Bearer $LANGCACHE_API_KEY"' in curl
        assert '-H "Content-Type: application/json"' in curl
        assert """-d '{"prompt":"it'\\''s"}'""" in curl
        assert curl.startswith(f"curl -X POST '{LC}/entries/search'")

    @pytest.mark.parametrize(
        "component, method, url, header, placeholder",
        [("am", "GET", f"{AM}/health", "Authorization", "Bearer $MEMORY_API_KEY"),
         ("cr", "GET", f"{CR}/context-surfaces", "X-API-Key", "$CTX_ADMIN_KEY"),
         ("cr", "POST", "https://mcp.example/mcp", "X-API-Key", "$CTX_AGENT_KEY")],
    )  # fmt: skip
    def test_as_curl_placeholder_per_component(
        self, nb, mock_http, component, method, url, header, placeholder
    ):
        mock_http(json={})
        nb.http_call(component, method, url, headers={header: "sekret-fake-12345"})
        assert f"{header}: {placeholder}" in nb.as_curl(nb.HTTP_TRACE[-1])

    def test_wire_view_renders_every_payload_kind_without_secrets(self, nb, mock_http):
        mock_http(json={"data": []})
        nb.register_secret(ADMIN_KEY)
        nb.http_call("lc", "POST", f"{LC}/entries/search", json={"prompt": ADMIN_KEY})
        # A plain str payload is not redacted on the way in, so the last scrub in wire_view
        # is all that protects it. (The secret must be plain: TRICKY's & < > " never survive
        # HTML rendering verbatim, so asserting its absence would prove nothing.)
        payload = [nb.HTTP_TRACE[-1], "FT._LIST", f"AUTH {ADMIN_KEY}", {"k": ADMIN_KEY}]
        text = nb.wire_view("call", payload).text
        assert "FT._LIST" in text and "AUTH ****" in text and "call" in text
        assert ADMIN_KEY not in text


# ---- HTTP choke points --------------------------------------------------------------
class TestChokePoints:
    # --- any component
    def test_flush_is_refused_everywhere(self, nb, mock_http):
        seen = mock_http(status=204)
        for component, base in (("lc", LC), ("am", AM), ("cr", CR)):
            for method in ("POST", "PUT", "DELETE"):
                refused(nb, seen, component, method, f"{base}/flush", expect=(204,))
        refused(nb, seen, "lc", "POST", f"{LC}/flush/")
        refused(nb, seen, "lc", "POST", f"{LC}/FLUSH")

    @pytest.mark.parametrize(
        "tail",
        ["/flush/.", "/%66lush", "/%46LUSH", "/flush;a=b", "/flush%2F", "//flush", "/a%2Fflush",
         "/x/../flush", "/entries/./flush", "/%2e%2e/flush", "/flush?x=1", "/flush#frag"],
    )  # fmt: skip
    def test_flush_is_refused_however_it_is_spelled(self, nb, mock_http, tail):
        # httpx normalises these to a request for /flush (or a server will), so the check
        # has to read the path as httpx will send it, not as it was typed.
        seen = mock_http(status=204)
        refused(nb, seen, "lc", "POST", f"{LC}{tail}", expect=(204,))

    def test_paths_that_only_resemble_flush_pass(self, nb):
        for tail in ("/flushed", "/preflush", "/entries/flush-count", "/entries/search?next=/flush"):
            assert nb.http_refusal("lc", "GET", f"{LC}{tail}") is None, tail
        assert nb.http_refusal("lc", "POST", f"{LC}/entries/search?next=/flush") is None

    def test_include_all_is_refused_in_the_url_or_in_params(self, nb, mock_http):
        seen = mock_http(json={})
        refused(nb, seen, "am", "GET", f"{AM}/session-memory?includeAll=true")
        refused(nb, seen, "am", "GET", f"{AM}/session-memory", params={"includeAll": "true"})
        refused(nb, seen, "am", "GET", f"{AM}/session-memory", params={"INCLUDEALL": "false"})
        refused(nb, seen, "am", "POST", f"{AM}/long-term-memory/search?includeAll=1",
                json=scoped_search(nb))  # fmt: skip
        nb.http_call("am", "GET", f"{AM}/session-memory", params={"filterOwnerId": nb.AM_OWNER})
        assert len(seen) == 1

    def test_the_request_is_vetted_as_it_is_sent_not_as_it_was_typed(self, nb, mock_http):
        # params replace the URL's own query, so this reaches the wire without includeAll.
        seen = mock_http(json={})
        url = f"{AM}/session-memory?includeAll=true"
        nb.http_call("am", "GET", url, params={"filterOwnerId": nb.AM_OWNER})
        assert seen[0].url.query == f"filterOwnerId={nb.AM_OWNER}".encode()

    def test_verbs_outside_get_post_delete_are_refused(self, nb, mock_http):
        seen = mock_http(status=200, json={})
        refused(nb, seen, "lc", "PATCH", f"{LC}/entries/abc")
        refused(nb, seen, "am", "PATCH", f"{AM}/long-term-memory/m1")
        refused(nb, seen, "cr", "PATCH", f"{CR}/context-surfaces/S1")
        refused(nb, seen, "cr", "PUT", f"{CR}/context-surfaces/S1", json={"name": "renamed"})

    def test_deletes_and_posts_that_no_route_allows_are_refused(self, nb, mock_http):
        seen = mock_http(status=200, json={})
        refused(nb, seen, "am", "DELETE", f"{AM}/session-memory/s1/events/e1")
        refused(nb, seen, "am", "DELETE", f"{AM}/long-term-memory/m1")
        refused(nb, seen, "cr", "DELETE", f"{CR}/context-surfaces")
        refused(nb, seen, "lc", "POST", f"{LC}/conversations/search", json={"prompt": "p"})
        refused(nb, seen, "cr", "POST", f"{CR}/keys/validate", json={"key": "k"})
        refused(nb, seen, "am", "POST", f"{AM}/long-term-memory/move", json={"memoryIds": ["m1"]})
        refused(nb, seen, "am", "POST", f"{AM}/namespaces", json={"name": "n"})

    def test_a_rule_applies_only_to_its_own_component(self, nb, mock_http):
        # Every id below is ledgered, so only the component check can refuse the wrong pairing.
        seen = mock_http(status=204)
        session = f"{nb.AM_OWNER}-s1"
        nb.ledger_add("am", "session", session)
        nb.ledger_add("lc", "entry", "e1")
        nb.ledger_add("cr", "surface", "S1")
        refused(nb, seen, "lc", "DELETE", f"{AM}/session-memory/{session}")
        refused(nb, seen, "cr", "DELETE", f"{LC}/entries/e1")
        refused(nb, seen, "am", "DELETE", f"{CR}/context-surfaces/S1")
        nb.http_call("am", "DELETE", f"{AM}/session-memory/{session}")
        nb.http_call("lc", "DELETE", f"{LC}/entries/e1")
        nb.http_call("cr", "DELETE", f"{CR}/context-surfaces/S1")
        assert len(seen) == 3

    @pytest.mark.parametrize("verb", ["PATCH", "PURGE", "HEAD", "OPTIONS", "TRACE", "PUT", "brew"])
    def test_verbs_other_than_get_post_delete_are_never_sent(self, nb, mock_http, verb):
        seen = mock_http(json={})
        refused(nb, seen, "lc", verb, f"{LC}/entries/search")
        assert str(nb.HTTP_TRACE[-1].response).startswith(verb.upper())

    def test_reads_and_vetted_writes_pass(self, nb, mock_http):
        seen = mock_http(json={})
        nb.http_call("lc", "POST", f"{LC}/entries/search", json={"prompt": "p"})
        nb.http_call("lc", "POST", f"{LC}/entries", json={"prompt": "p", "response": "r",
                                                          "ttlMillis": 60_000})  # fmt: skip
        nb.http_call("am", "GET", f"{AM}/health")
        nb.http_call("cr", "GET", f"{CR}/context-surfaces")
        nb.http_call("cr", "POST", "https://mcp.example/mcp", json={"method": "tools/list"})
        assert len(seen) == 5

    # --- LangCache
    def test_lc_set_needs_a_ttl_within_the_limit(self, nb, mock_http):
        seen = mock_http(status=201, json={"entryId": "e" * 32})
        entry = {"prompt": "p", "response": "r"}
        for bad in ({}, {"ttlMillis": None}, {"ttlMillis": 0}, {"ttlMillis": -1},
                    {"ttlMillis": nb.LC_TTL_MS + 1}, {"ttlMillis": "60000"},
                    {"ttlMillis": 1.5}, {"ttlMillis": True}):  # fmt: skip
            refused(nb, seen, "lc", "POST", f"{LC}/entries", json=entry | bad)
        refused(nb, seen, "lc", "POST", f"{LC}/entries")
        for good in (1, nb.LC_TEACH_TTL_MS, nb.LC_TTL_MS):
            nb.http_call("lc", "POST", f"{LC}/entries", json=entry | {"ttlMillis": good})
        assert len(seen) == 3

    @pytest.mark.parametrize(
        "body",
        [None, {}, {"attributes": {}}, {"attributes": None}, {"attributes": {"scope": "someone-else"}},
         {"attributes": {"a": "iris-basics-00000000"}}, {"attributes": {"a": "x", "b": "y"}},
         {"prompt": "p"}, {"attributes": []}],
    )  # fmt: skip
    def test_lc_delete_by_attributes_refused_unless_it_is_exactly_our_tag(
        self, nb, mock_http, body
    ):
        nb.set_lc_scope("scope")
        seen = mock_http(json={})
        refused(nb, seen, "lc", "DELETE", f"{LC}/entries", json=body)

    def test_lc_delete_by_our_tag_is_allowed_only_under_the_scope_attribute(self, nb, mock_http):
        seen = mock_http(json={"deletedEntriesCount": 2})
        body = {"attributes": {"domain": nb.RUN_TAG}}
        # No scope attribute named yet: the cache may not define "domain", and the server's
        # answer to an attribute it does not know is undocumented.
        refused(nb, seen, "lc", "DELETE", f"{LC}/entries", json=body)
        nb.set_lc_scope("domain")
        nb.http_call("lc", "DELETE", f"{LC}/entries", json=body)
        refused(nb, seen, "lc", "DELETE", f"{LC}/entries", json={"attributes": {"other": nb.RUN_TAG}})
        nb.set_lc_scope("")
        refused(nb, seen, "lc", "DELETE", f"{LC}/entries", json=body)
        assert len(seen) == 1

    def test_lc_delete_by_attributes_refuses_a_tag_alongside_another_attribute(self, nb, mock_http):
        nb.set_lc_scope("scope")
        seen = mock_http(json={})
        refused(nb, seen, "lc", "DELETE", f"{LC}/entries",
                json={"attributes": {"scope": nb.RUN_TAG, "b": "wider"}})  # fmt: skip

    def test_set_lc_scope_takes_a_valid_attribute_name_or_nothing(self, nb):
        for good in ("domain", "iris-scope_1", "", None, "  padded  "):
            nb.set_lc_scope(good)
            assert nb.LC_SCOPE["attr"] == (good or "").strip()
        for bad in ("two words", "x" * 33, "bad!", "é", "a,b"):
            with pytest.raises(ValueError):
                nb.set_lc_scope(bad)

    def test_lc_delete_by_id_needs_the_ledger_or_an_approval(self, nb, mock_http):
        seen = mock_http(status=204)
        refused(nb, seen, "lc", "DELETE", f"{LC}/entries/abc123")
        nb.ledger_add("lc", "entry", "abc123")
        nb.http_call("lc", "DELETE", f"{LC}/entries/abc123")
        nb.ledger_add("lc", "entry", "abc123", "deleted")
        refused(nb, seen, "lc", "DELETE", f"{LC}/entries/abc123")
        nb.approve_lc_entry("sentinel1")
        nb.http_call("lc", "DELETE", f"{LC}/entries/sentinel1")
        assert len(seen) == 2

    # --- Agent Memory
    def test_am_create_needs_our_prefix_owner_and_namespace(self, nb, mock_http):
        seen = mock_http(status=201, json={"created": []})
        url = f"{AM}/long-term-memory"
        foreign = f"ib-{other_run(nb)}-m1"
        bad_bodies = [
            None, {}, {"memories": []}, {"memories": "m1"}, {"memories": ["m1"]},
            {"memories": [am_memory(nb, id="CUST-DEMO-001-m1")]},  # the live demo's record
            {"memories": [am_memory(nb, id=foreign)]},  # another run's
            {"memories": [am_memory(nb, id=5)]},
            {"memories": [am_memory(nb, ownerId="CUST-DEMO-001")]},  # planted in a customer's memory
            {"memories": [am_memory(nb, ownerId=f"ib-{other_run(nb)}")]},
            {"memories": [am_memory(nb, ownerId=None)]},
            {"memories": [am_memory(nb, namespace="prod")]},
            {"memories": [am_memory(nb, namespace=None)]},
            {"memories": [am_memory(nb, 1), am_memory(nb, 2, ownerId="CUST-DEMO-001")]},
        ]  # fmt: skip
        for body in bad_bodies:
            refused(nb, seen, "am", "POST", url, json=body)
        nb.http_call("am", "POST", url, json={"memories": [am_memory(nb, 1), am_memory(nb, 2)]})
        assert len(seen) == 1

    def test_am_events_need_our_session_and_actor(self, nb, mock_http):
        seen = mock_http(status=201, json={"event": {}})
        url = f"{AM}/session-memory/events"
        no_session = am_event(nb)
        del no_session["sessionId"]  # the server would mint one the ledger can never know
        no_actor = am_event(nb)
        del no_actor["actorId"]
        bad_bodies = [
            None, {}, no_session, no_actor,
            am_event(nb, sessionId="demo-session-123"),  # the live demo's session
            am_event(nb, sessionId=f"ib-{other_run(nb)}-s1"),
            am_event(nb, sessionId=f"{nb.AM_OWNER}-m1"),  # ours, but not a session id
            am_event(nb, sessionId=7),
            am_event(nb, actorId="CUST-DEMO-001"),  # extraction would fill that customer's memory
            am_event(nb, actorId="assistant"),
        ]  # fmt: skip
        for body in bad_bodies:
            refused(nb, seen, "am", "POST", url, json=body)
        nb.http_call("am", "POST", url, json=am_event(nb))
        nb.http_call("am", "POST", url, json=am_event(nb, sessionId=f"{nb.AM_OWNER}-s42"))
        assert len(seen) == 2

    def test_am_search_must_be_scoped_to_an_owner_or_a_namespace(self, nb, mock_http):
        seen = mock_http(json={"items": []})
        url = f"{AM}/long-term-memory/search"
        unscoped = [
            None, {}, {"text": "x"}, {"text": "x", "filter": {}}, {"filter": []},
            {"filter": {"topics": {"in": ["a"]}}},
            {"filter": {"ownerId": {}}}, {"filter": {"ownerId": {"eq": ""}}},
            {"filter": {"ownerId": {"eq": None}}}, {"filter": {"ownerId": "ib-x"}},
            {"filter": {"ownerId": {"in": [nb.AM_OWNER]}}}, {"filter": {"ownerId": {"ne": "x"}}},
            scoped_search(nb, filterOp="any"),  # any one clause would be enough
        ]  # fmt: skip
        for body in unscoped:
            refused(nb, seen, "am", "POST", url, json=body)
        scoped = [
            scoped_search(nb),
            scoped_search(nb, filter={"namespace": {"eq": nb.AM_NAMESPACE}}),
            scoped_search(nb, filter={"ownerId": {"eq": "ib-nobody"}, "namespace": {"eq": "elsewhere"}},
                          filterOp="all"),  # the scope lens asks after other owners and namespaces
        ]  # fmt: skip
        for body in scoped:
            nb.http_call("am", "POST", url, json=body)
        assert len(seen) == len(scoped)

    def test_am_bulk_delete_needs_an_explicit_ledgered_list(self, nb, mock_http):
        seen = mock_http(json={"deleted": []})
        url = f"{AM}/long-term-memory"
        ours = [f"{nb.AM_OWNER}-m{n}" for n in (1, 2, 3)]
        refused(nb, seen, "am", "DELETE", url)
        refused(nb, seen, "am", "DELETE", url, json={})
        refused(nb, seen, "am", "DELETE", url, json={"memoryIds": []})
        refused(nb, seen, "am", "DELETE", url, json={"memoryIds": "m1"})
        refused(nb, seen, "am", "DELETE", url, json={"memoryIds": [["m1"]]})
        refused(nb, seen, "am", "DELETE", url, json={"memoryIds": [ours[0]]})  # not ledgered
        nb.ledger_add("am", "memory", ours[0])
        nb.ledger_add("am", "memory", ours[1], "intent")  # a timed-out create still counts
        nb.ledger_add("am", "memory", ours[2])
        nb.ledger_add("am", "memory", ours[2], "deleted")  # already gone: not ours to send again
        refused(nb, seen, "am", "DELETE", url, json={"memoryIds": [ours[0], "someone-elses"]})
        refused(nb, seen, "am", "DELETE", url, json={"memoryIds": [ours[0], ours[2]]})
        nb.http_call("am", "DELETE", url, json={"memoryIds": ours[:2]})
        assert len(seen) == 1

    def test_am_ledgered_ids_must_carry_this_runs_prefix(self, nb, mock_http):
        # A ledger line is only a note. One that names the live demo's memory or session
        # (a bug, or a typo in the wrong column) must not turn into a delete.
        seen = mock_http(json={"deleted": []})
        nb.ledger_add("am", "memory", "CUST-DEMO-001-m1")
        nb.ledger_add("am", "memory", f"ib-{other_run(nb)}-m1")
        nb.ledger_add("am", "session", "demo-session-123")
        nb.ledger_add("am", "session", f"ib-{other_run(nb)}-s1")
        for ident in ("CUST-DEMO-001-m1", f"ib-{other_run(nb)}-m1"):
            refused(nb, seen, "am", "DELETE", f"{AM}/long-term-memory", json={"memoryIds": [ident]})
        for ident in ("demo-session-123", f"ib-{other_run(nb)}-s1"):
            refused(nb, seen, "am", "DELETE", f"{AM}/session-memory/{ident}")

    def test_am_bulk_delete_accepts_ownership_verified_ids(self, nb, mock_http):
        seen = mock_http(json={"deleted": []})
        url = f"{AM}/long-term-memory"
        refused(nb, seen, "am", "DELETE", url, json={"memoryIds": ["extracted-1"]})
        nb.approve_am_memories([{"id": "extracted-1", "ownerId": nb.AM_OWNER}])
        nb.http_call("am", "DELETE", url, json={"memoryIds": ["extracted-1"]})
        assert len(seen) == 1

    def test_am_session_delete_needs_the_ledger(self, nb, mock_http):
        seen = mock_http(status=204)
        session = f"{nb.AM_OWNER}-s1"
        refused(nb, seen, "am", "DELETE", f"{AM}/session-memory/{session}")
        nb.ledger_add("am", "session", session)
        nb.http_call("am", "DELETE", f"{AM}/session-memory/{session}")
        nb.ledger_add("am", "memory", f"{nb.AM_OWNER}-s2")  # a memory id is not a session id
        refused(nb, seen, "am", "DELETE", f"{AM}/session-memory/{nb.AM_OWNER}-s2")
        assert len(seen) == 1

    # --- Context Retriever
    def test_cr_create_surface_passes_only_with_everything_in_order(self, nb, mock_http):
        seen = mock_http(status=201, json={"id": "S1"})
        url = f"{CR}/context-surfaces"
        nb.http_call("cr", "POST", url, json=create_surface_body(nb), timeout=nb.T_WRITE)
        assert len(seen) == 1

        def templates(*names):
            entities = [{"name": "E", "redis_key_template": name} for name in names]
            return {"data_model": {"entities": entities}}

        def pool(**config):
            base = create_surface_body(nb)["data_source"]["connection_config"]
            return {"data_source": {"type": "redis", "connection_config": base | config}}

        spoiled = {
            "a foreign name": {"name": "radish-bank"},
            "another run's name": {"name": f"iris-basics-{other_run(nb)}"},
            "no metadata": {"metadata": None},
            "another run's metadata": {"metadata": {"created_by": nb.CR_CREATED_BY, "run": other_run(nb)}},
            "no created_by": {"metadata": {"run": nb.RUN}},
            "no data model": {"data_model": None},
            "no entities": {"data_model": {"entities": []}},
            "the demo's keys": templates("radish_bank_customer:{customer_id}"),
            "a bare key template": templates("customer:{customer_id}"),
            "under the run prefix but not the cr one": templates(f"{nb.PREFIX}customer:{{id}}"),
            "another run's keys": templates(f"irisnb_{other_run(nb)}_cr:customer:{{id}}"),
            "one good and one bad": templates(f"{nb.CR_PREFIX}a:{{id}}", "b:{id}"),
            "a template that is not text": templates(5),
            "a pool of 10": pool(pool_size=10),
            "a pool of 0": pool(pool_size=0),
            "a pool that is a bool": pool(pool_size=True),
            "idle connections": pool(min_idle_conns=2),
            "no pool size": {"data_source": {"connection_config": {"addr": "h:1", "min_idle_conns": 0}}},
            "no idle setting": {"data_source": {"connection_config": {"addr": "h:1", "pool_size": 2}}},
            "no connection config": {"data_source": {"type": "redis"}},
            "no data source": {"data_source": None},
        }  # fmt: skip
        for label, changes in spoiled.items():
            error = refused(nb, seen, "cr", "POST", url, json=create_surface_body(nb, **changes))
            assert error.kind == "refused", label
        refused(nb, seen, "cr", "POST", url)

    def test_cr_data_and_agent_keys_need_a_surface_this_run_created(self, nb, mock_http):
        seen = mock_http(json={"imported": 1, "failed": 0, "errors": []})
        data = f"{CR}/context-surfaces/DEMO_SURFACE/data"
        keys = f"{CR}/context-surfaces/DEMO_SURFACE/agent-keys"
        records = {"entity": "Customer", "records": [{"customer_id": "C001"}],
                   "options": {"on_conflict": "overwrite"}}  # fmt: skip
        key_request = {"name": "k", "expires_at": iso_in(3600)}

        def both_refused():
            refused(nb, seen, "cr", "POST", data, json=records)
            refused(nb, seen, "cr", "POST", keys, json=key_request)

        both_refused()  # the live demo's surface, found by listing
        nb.ledger_add("cr", "surface", "DEMO_SURFACE", "intent")
        both_refused()  # an intent line is a note that a create was tried
        nb.approve_cr_orphan(foreign_surface(nb, id="DEMO_SURFACE"), nb.ORPHAN_CONFIRM)
        both_refused()  # an orphan may be deleted, never written to
        nb.ledger_add("cr", "surface", "DEMO_SURFACE")
        nb.http_call("cr", "POST", data, json=records)
        nb.http_call("cr", "POST", keys, json=key_request)
        nb.ledger_add("cr", "surface", "DEMO_SURFACE", "deleted")
        both_refused()

    def test_cr_agent_keys_need_a_near_expiry(self, nb, mock_http):
        nb.ledger_add("cr", "surface", "S1")
        seen = mock_http(status=201, json={"id": "k", "key": AGENT_KEY, "key_type": "agent"})
        url = f"{CR}/context-surfaces/S1/agent-keys"
        too_far = iso_in(nb.CR_KEY_TTL_S + 3600)
        for bad in ({}, {"expires_at": None}, {"expires_at": ""}, {"expires_at": "tomorrow"},
                    {"expires_at": iso_in(-60)}, {"expires_at": too_far},
                    {"expires_at": "2099-01-01T00:00:00Z"}, {"expires_at": 1893456000}):  # fmt: skip
            refused(nb, seen, "cr", "POST", url, json={"name": "k"} | bad)
        refused(nb, seen, "cr", "POST", url)
        soon = datetime.now(timezone.utc) + timedelta(hours=1)
        for good in (iso_in(60), iso_in(nb.CR_KEY_TTL_S), soon.isoformat().replace("+00:00", "Z"),
                     soon.replace(tzinfo=None).isoformat()):  # fmt: skip
            nb.http_call("cr", "POST", url, json={"name": "k", "expires_at": good})
        assert len(seen) == 4

    def test_cr_surface_delete_needs_the_ledger_or_a_confirmed_orphan(self, nb, mock_http):
        seen = mock_http(json={"message": "deleted"})
        url = f"{CR}/context-surfaces/SURF1"
        refused(nb, seen, "cr", "DELETE", url)
        refused(nb, seen, "cr", "DELETE", f"{url}/agent-keys/K1")
        nb.ledger_add("cr", "surface", "SURF1")
        nb.http_call("cr", "DELETE", url)
        nb.http_call("cr", "DELETE", f"{url}/agent-keys/K1")
        assert len(seen) == 2
        refused(nb, seen, "cr", "DELETE", f"{CR}/context-surfaces/ORPHAN1")
        nb.approve_cr_orphan(foreign_surface(nb), nb.ORPHAN_CONFIRM)
        nb.http_call("cr", "DELETE", f"{CR}/context-surfaces/ORPHAN1")
        assert len(seen) == 3

    def test_cr_never_puts(self, nb, mock_http):
        # A PUT can retarget a surface's key templates, and no column needs one.
        seen = mock_http(json={})
        nb.ledger_add("cr", "surface", "SURF1")
        nb.approve_cr_orphan(foreign_surface(nb), nb.ORPHAN_CONFIRM)
        retarget = {"data_model": {"entities": [{"name": "Customer",
                                                 "redis_key_template": "customer:{customer_id}"}]}}  # fmt: skip
        refused(nb, seen, "cr", "PUT", f"{CR}/context-surfaces/SURF1", json=retarget)
        refused(nb, seen, "cr", "PUT", f"{CR}/context-surfaces/ORPHAN1", json={"name": "renamed"})

    def test_cr_agent_key_ledger_entry_does_not_authorise_a_surface(self, nb, mock_http):
        seen = mock_http(json={})
        nb.ledger_add("cr", "agent_key", "SURF1")
        refused(nb, seen, "cr", "DELETE", f"{CR}/context-surfaces/SURF1")

    def test_a_surface_in_another_runs_ledger_is_not_ours(self, nb, mock_http, monkeypatch):
        seen = mock_http(json={})
        other = nb.RUN[::-1] if nb.RUN[::-1] != nb.RUN else "0" * 8
        monkeypatch.setattr(nb, "RUN", other)
        nb.ledger_add("cr", "surface", "SURF9")
        monkeypatch.setattr(nb, "RUN", "f" * 8 if other != "f" * 8 else "e" * 8)
        refused(nb, seen, "cr", "DELETE", f"{CR}/context-surfaces/SURF9")

    # --- extension point
    def test_extra_rules_can_only_add_refusals(self, nb, mock_http):
        seen = mock_http(json={})
        nb.http_call("lc", "POST", f"{LC}/entries/search")
        nb.HTTP_RULES["no_search"] = (
            lambda component, method, url, body, header_names: "no searches today" if "search" in url else None
        )  # fmt: skip
        assert "no searches today" in str(refused(nb, seen, "lc", "POST", f"{LC}/entries/search"))
        nb.HTTP_RULES["abstains"] = lambda *args: None  # abstaining lifts nothing
        refused(nb, seen, "lc", "DELETE", f"{LC}/entries", json={})

    def test_a_rule_sees_the_address_as_sent_and_the_header_names(self, nb, mock_http):
        mock_http(json={})
        looked = []
        nb.HTTP_RULES["spy"] = lambda *args: looked.append(args)
        nb.http_call("am", "GET", f"{AM}/session-memory", params={"filterOwnerId": nb.AM_OWNER},
                     headers={"Authorization": "Bearer key-fake-12345"})  # fmt: skip
        ((component, method, url, body, header_names),) = looked
        assert (component, method, body) == ("am", "GET", None)
        assert url == f"{AM}/session-memory?filterOwnerId={nb.AM_OWNER}"
        assert "authorization" in header_names and "key-fake-12345" not in str(header_names)

    def test_naming_a_rule_again_replaces_it(self, nb, mock_http):
        # A cell that installs a rule runs again whenever its inputs change.
        seen = mock_http(json={})
        for session in ("s1", "s2", "s3"):
            nb.HTTP_RULES["session"] = (
                lambda component, method, url, body, header_names, session=session: (
                    None if url.endswith(session) else f"only {session}"
                )
            )  # fmt: skip
        assert len(nb.HTTP_RULES) == 1
        refused(nb, seen, "am", "GET", f"{AM}/session-memory/s1")
        nb.http_call("am", "GET", f"{AM}/session-memory/s3")

    def test_a_refusal_is_traced_with_zero_status(self, nb, mock_http):
        seen = mock_http(json={})
        refused(nb, seen, "lc", "POST", f"{LC}/flush")
        call = nb.HTTP_TRACE[-1]
        assert call.status == 0 and "flush" in call.response

    def test_http_refusal_can_be_asked_directly(self, nb):
        assert nb.http_refusal("lc", "post", f"{LC}/flush")
        assert nb.http_refusal("lc", "POST", f"{LC}/entries/search") is None

    def test_a_path_prefix_on_the_service_url_changes_nothing(self, nb):
        for component, base in (
            ("lc", "https://gw.example/team/v1/caches/C"),
            ("am", "https://gw.example/x/v1/stores/S"),
        ):
            path = "entries" if component == "lc" else "long-term-memory"
            assert nb.http_refusal(component, "DELETE", f"{base}/{path}", {})
        assert nb.http_refusal("cr", "DELETE", "https://gw.example/ctx/api/v1/context-surfaces/S")
        nb.ledger_add("cr", "surface", "S")
        assert (
            nb.http_refusal("cr", "DELETE", "https://gw.example/ctx/api/v1/context-surfaces/S")
            is None
        )


# ---- Redis guard ----------------------------------------------------------------------
class TestRedisGuard:
    def denied(self, nb, rec, *argv):
        before = len(rec.sent)
        with pytest.raises(nb.Denied):
            rec.execute_command(*argv)
        assert len(rec.sent) == before, "a denied command must never reach the server"

    def test_reads_are_allowed_on_any_key(self, nb, rec):
        reads = (
            ("PING",), ("INFO", "server"), ("DBSIZE",), ("SCAN", 0, "MATCH", "*", "COUNT", 1000),
            ("TYPE", "prod:cust:1"), ("TTL", "prod:cust:1"), ("EXISTS", "a", "b"),
            ("HGETALL", "prod:cust:1"), ("HGET", "prod:cust:1", "f"), ("HMGET", "prod:cust:1", "f", "g"),
            ("HSTRLEN", "prod:cust:1", "f"), ("MEMORY", "USAGE", "prod:cust:1"),
            ("OBJECT", "ENCODING", "prod:cust:1"), ("JSON.GET", "prod:cust:1", "$"),
            ("FT._LIST",), ("FT.INFO", "memory:store:ltm"), ("FT.SEARCH", "someones_idx", "*"),
            ("FT.AGGREGATE", "someones_idx", "*"), ("FT.HYBRID", "someones_idx"),
            ("FT.PROFILE", "someones_idx", "SEARCH", "QUERY", "*"), ("FT.EXPLAINCLI", "i", "*"),
        )  # fmt: skip
        for argv in reads:
            rec.execute_command(*argv)
        assert len(rec.sent) == len(reads)
        assert {a[0] for a in reads} == set(nb.REDIS_READS)  # the probe list covers every read

    def test_command_names_are_case_insensitive_and_bytes_safe(self, nb, rec):
        rec.execute_command("ping")
        rec.execute_command(b"FT.SEARCH", b"any", b"*")
        self.denied(nb, rec, b"hset", b"prod:x", "f", "v")
        self.denied(nb, rec, "flushall")

    @pytest.mark.parametrize(
        "argv",
        [
            ("FLUSHDB",), ("FLUSHALL",), ("CONFIG", "SET", "maxmemory", "0"), ("SCRIPT", "FLUSH"),
            ("FT.DROP", "i"), ("KEYS", "*"), ("MONITOR",), ("EVAL", "return 1", 0),
            ("ACL", "SETUSER", "x"),
            # Not on the allowlist, so denied even against keys and indexes under PREFIX: only
            # then is it the allowlist, and not the prefix check, that decides. (<P> stands
            # for the run's PREFIX, which is not known when the parameters are collected.)
            ("HDEL", "<P>k", "f"), ("SET", "<P>k", "v"), ("APPEND", "<P>k", "v"),
            ("JSON.DEL", "<P>k"), ("RENAME", "<P>a", "<P>b"), ("COPY", "<P>a", "<P>b"),
            ("FT.ALTER", "<P>i", "SCHEMA", "ADD", "f", "TEXT"), ("FT.SUGADD", "<P>s", "x", 1),
        ],
    )  # fmt: skip
    def test_everything_not_listed_is_denied(self, nb, rec, argv):
        self.denied(
            nb, rec, *[a.replace("<P>", nb.PREFIX) if isinstance(a, str) else a for a in argv]
        )

    def test_the_write_allowlist_is_exactly_the_contract(self, nb):
        assert nb.REDIS_WRITES == {
            "HSET", "EXPIRE", "UNLINK", "DEL", "JSON.SET", "FT.CREATE", "FT.DROPINDEX",
        }  # fmt: skip
        assert not nb.REDIS_WRITES & nb.REDIS_READS

    def test_writes_inside_the_prefix_are_allowed(self, nb, rec):
        p = nb.PREFIX
        rec.execute_command("HSET", f"{p}rs:doc:1", "title", "t")
        rec.execute_command("EXPIRE", f"{p}rs:doc:1", 3600)
        rec.execute_command("UNLINK", f"{p}a", f"{p}b", f"{p}c")
        rec.execute_command("DEL", f"{p}a")
        rec.execute_command("JSON.SET", f"{p}cr:customer:C1", "$", "{}")
        rec.execute_command("FT.DROPINDEX", f"{p}rs:idx")
        assert len(rec.sent) == 6

    def test_high_level_redis_py_methods_go_through_the_guard(self, nb, rec):
        rec.hset(f"{nb.PREFIX}k", mapping={"a": "1"})
        assert rec.sent[-1][:2] == ("HSET", f"{nb.PREFIX}k")
        with pytest.raises(nb.Denied):
            rec.hset("prod:k", mapping={"a": "1"})
        with pytest.raises(nb.Denied):
            rec.delete("prod:k")
        with pytest.raises(nb.Denied):
            rec.flushdb()

    @pytest.mark.parametrize(
        "argv",
        [("HSET", "prod:customer:1", "name", "x"), ("EXPIRE", "prod:k", 10), ("JSON.SET", "prod:k", "$", "{}"),
         ("DEL", "prod:k"), ("UNLINK", "prod:k"), ("HSET", "irisnb_deadbeef_x", "f", "v"),  # another run
         ("HSET", "irisnb_", "f", "v"), ("HSET", "", "f", "v")],
    )  # fmt: skip
    def test_writes_outside_the_prefix_are_denied(self, nb, rec, argv):
        self.denied(nb, rec, *argv)

    def test_multi_key_del_and_unlink_check_every_key(self, nb, rec):
        p = nb.PREFIX
        for command in ("DEL", "UNLINK"):
            self.denied(nb, rec, command, f"{p}a", "prod:b")
            self.denied(nb, rec, command, "prod:a", f"{p}b")
            self.denied(nb, rec, command, f"{p}a", f"{p}b", f"{p}c", "prod:d")
            self.denied(nb, rec, command)

    def test_object_and_memory_subcommands_are_reads(self, nb, rec):
        rec.execute_command("OBJECT", "ENCODING", "someone-elses-key")
        rec.execute_command("MEMORY", "USAGE", "someone-elses-key")

    # --- FT.CREATE
    def create(self, *middle, name=None):
        return ("FT.CREATE", name or f"{self.prefix}rs:idx", *middle, "SCHEMA", "t", "TEXT")

    @pytest.fixture(autouse=True)
    def _prefix(self, nb):
        self.prefix = nb.PREFIX

    def test_ft_create_with_a_proper_prefix_is_allowed(self, nb, rec):
        rec.execute_command(*self.create("ON", "HASH", "PREFIX", 1, f"{nb.RS_PREFIX}doc:"))
        rec.execute_command(
            *self.create("on", "hash", "prefix", "2", f"{nb.PREFIX}a", f"{nb.PREFIX}b")
        )
        # After each create the guard asks the server what it built.
        assert [a[0] for a in rec.sent] == ["FT.CREATE", "FT.INFO", "FT.CREATE", "FT.INFO"]

    def test_ft_create_without_prefix_is_denied(self, nb, rec):
        self.denied(nb, rec, *self.create("ON", "HASH"))
        self.denied(nb, rec, *self.create())

    def test_ft_create_with_empty_or_foreign_prefix_is_denied(self, nb, rec):
        self.denied(nb, rec, *self.create("ON", "HASH", "PREFIX", 1, ""))
        self.denied(nb, rec, *self.create("ON", "HASH", "PREFIX", 1, "prod:"))
        self.denied(
            nb, rec, *self.create("ON", "HASH", "PREFIX", 1, "irisnb_")
        )  # shorter than ours
        self.denied(nb, rec, *self.create("ON", "HASH", "PREFIX", 1, "irisnb_deadbeef_x:"))
        self.denied(nb, rec, *self.create("ON", "HASH", "PREFIX", 2, f"{nb.PREFIX}a", "prod:"))
        self.denied(nb, rec, *self.create("ON", "HASH", "PREFIX", 2, f"{nb.PREFIX}a", ""))

    def test_ft_create_with_malformed_prefix_clause_is_denied(self, nb, rec):
        self.denied(nb, rec, *self.create("ON", "HASH", "PREFIX"))
        self.denied(nb, rec, *self.create("ON", "HASH", "PREFIX", "x", nb.PREFIX))
        self.denied(nb, rec, *self.create("ON", "HASH", "PREFIX", 0))
        self.denied(
            nb, rec, *self.create("ON", "HASH", "PREFIX", 3, f"{nb.PREFIX}a")
        )  # count > given
        self.denied(nb, rec, *self.create("PREFIX", 1, nb.PREFIX, "PREFIX", 1, "prod:"))  # twice

    def test_a_field_called_prefix_does_not_hide_the_real_prefix_clause(self, nb, rec):
        # The clause search stops at SCHEMA, so a field named PREFIX is not a second clause.
        p = nb.PREFIX
        clause = ("ON", "HASH", "PREFIX", 1, f"{p}rs:doc:")
        rec.execute_command("FT.CREATE", f"{p}i", *clause, "SCHEMA", "PREFIX", "TEXT")
        assert [a[0] for a in rec.sent] == ["FT.CREATE", "FT.INFO"]

    def test_a_field_called_prefix_does_not_count_as_a_prefix_clause(self, nb, rec):
        self.denied(
            nb,
            rec,
            "FT.CREATE",
            f"{nb.PREFIX}i",
            "ON",
            "HASH",
            "SCHEMA",
            "PREFIX",
            "TEXT",
            "1",
            nb.PREFIX,
        )

    def test_ft_create_index_name_must_be_ours(self, nb, rec):
        self.denied(
            nb, rec, *self.create("ON", "HASH", "PREFIX", 1, nb.PREFIX, name="someone_elses_idx")
        )
        self.denied(
            nb, rec, *self.create("ON", "HASH", "PREFIX", 1, nb.PREFIX, name="memory:store:ltm")
        )

    @pytest.mark.parametrize(
        "hiding",
        [("LANGUAGE_FIELD",), ("SCORE_FIELD",), ("PAYLOAD_FIELD",), ("FILTER",),
         ("filter",), ("STOPWORDS", "1"), ("STOPWORDS", "2", "a"), ("STOPWORDS", "x"),
         ("STOPWORDS",)],
    )  # fmt: skip
    def test_an_option_value_spelt_schema_cannot_hide_a_second_prefix(self, nb, rec, hiding):
        # The server reads that SCHEMA as the option's value and carries on with options, so
        # the PREFIX after it is real (and, as RediSearch reads a repeat, the one that counts).
        for second in (("memory:",), ("",)):
            tokens = ("ON", "HASH", "PREFIX", 1, f"{nb.RS_PREFIX}doc:", *hiding,
                      "SCHEMA", "PREFIX", 1, *second, "SCHEMA", "t", "TEXT")  # fmt: skip
            self.denied(nb, rec, "FT.CREATE", f"{nb.PREFIX}rs:idx", *tokens)

    def test_options_that_merely_sit_before_schema_are_fine(self, nb, rec):
        head = ("ON", "HASH", "PREFIX", 1, f"{nb.RS_PREFIX}doc:")
        rec.execute_command("FT.CREATE", f"{nb.PREFIX}a", *head, "FILTER", "@year>2020",
                            "LANGUAGE_FIELD", "lang", "STOPWORDS", 2, "a", "the",
                            "SCHEMA", "t", "TEXT")  # fmt: skip
        rec.execute_command("FT.CREATE", f"{nb.PREFIX}b", *head, "STOPWORDS", 0, "SCHEMA", "t", "TEXT")
        assert [a[0] for a in rec.sent].count("FT.CREATE") == 2

    def test_the_index_the_server_built_is_checked_and_a_stray_one_is_dropped(self, nb, rec):
        # However the guard read the command, the server has the last word. Here it built
        # something the guard did not expect: ask, undo it (without DD), and say so.
        name = f"{nb.PREFIX}rs:idx"
        for built in (ft_info(name, ["memory:"]), ft_info(name, [""]), ft_info(name, []),
                      ft_info(name, [f"{nb.RS_PREFIX}doc:", "memory:"]), None,
                      nb.redis.ResponseError("Unknown index name")):  # fmt: skip
            rec.sent.clear()
            rec.replies["FT.INFO"] = built
            with pytest.raises(nb.Denied, match="dropped"):
                rec.execute_command(*self.create("ON", "HASH", "PREFIX", 1, f"{nb.RS_PREFIX}doc:"))
            assert rec.sent == [
                ("FT.CREATE", name, "ON", "HASH", "PREFIX", 1, f"{nb.RS_PREFIX}doc:",
                 "SCHEMA", "t", "TEXT"),
                ("FT.INFO", name),
                ("FT.DROPINDEX", name),
            ]  # fmt: skip

    def test_an_index_that_is_what_it_seems_is_left_alone(self, nb, rec):
        rec.execute_command(*self.create("ON", "HASH", "PREFIX", 1, f"{nb.RS_PREFIX}doc:"))
        assert [a[0] for a in rec.sent] == ["FT.CREATE", "FT.INFO"]
        assert [c.status for c in nb.REDIS_TRACE] == ["ok", "ok"]

    # --- FT.DROPINDEX
    def test_dropindex_dd_is_always_denied(self, nb, rec):
        self.denied(nb, rec, "FT.DROPINDEX", f"{nb.PREFIX}rs:idx", "DD")
        self.denied(nb, rec, "FT.DROPINDEX", f"{nb.PREFIX}rs:idx", "dd")
        nb.OWNED_INDEXES.add("irisnb_other_idx")
        self.denied(nb, rec, "FT.DROPINDEX", "irisnb_other_idx", "DD")

    def test_dropindex_outside_the_prefix_only_for_owned_indexes(self, nb, rec):
        self.denied(nb, rec, "FT.DROPINDEX", "irisnb_deadbeef_rs:idx")
        self.denied(nb, rec, "FT.DROPINDEX", "memory:store:ltm")
        nb.OWNED_INDEXES.add("irisnb_deadbeef_rs:idx")
        rec.execute_command("FT.DROPINDEX", "irisnb_deadbeef_rs:idx")
        self.denied(nb, rec, "FT.DROPINDEX", "memory:store:ltm")
        # ownership of an index never widens what keys or other commands may touch
        self.denied(nb, rec, "HSET", "irisnb_deadbeef_rs:idx", "f", "v")

    # --- pipelines and friends
    def test_pipeline_transaction_pubsub_and_monitor_are_denied(self, nb, rec):
        with pytest.raises(nb.Denied):
            rec.pipeline()
        with pytest.raises(nb.Denied):
            rec.pipeline(transaction=False)
        with pytest.raises(nb.Denied):
            rec.transaction(lambda pipe: None, "key")
        with pytest.raises(nb.Denied):
            rec.pubsub()
        with pytest.raises(nb.Denied):
            rec.monitor()
        assert rec.sent == []

    def test_module_clients_cannot_open_pipelines_either(self, nb, rec):
        # redis-py's ft(), json() and ts() build a Pipeline straight from the connection
        # pool, without touching Redis.pipeline, so denying that alone leaves them open.
        with pytest.raises(nb.Denied):
            rec.ft("idx").pipeline()
        with pytest.raises(nb.Denied):
            rec.json().pipeline()
        with pytest.raises(nb.Denied):
            rec.ts().pipeline()
        assert rec.sent == []

    def test_batch_indexer_is_denied_before_a_half_built_one_exists(self, nb, rec, monkeypatch):
        # Denied only through its pipeline, a BatchIndexer fails inside __init__ and then
        # raises again, unseen, from __del__. So it must never be constructed at all.
        def never_built(*args, **kwargs):
            raise AssertionError("a BatchIndexer was constructed")

        monkeypatch.setattr(nb.redis.commands.search.Search, "BatchIndexer", never_built)
        with pytest.raises(nb.Denied):
            rec.ft("idx").batch_indexer()
        with pytest.raises(nb.Denied):
            rec.ft("idx").batch_indexer(chunk_size=10)

    def test_no_module_client_offers_a_pipeline_that_works(self, nb, rec):
        # Every module accessor redis-py has, so an upgrade that gives another module a
        # pipeline() fails here instead of quietly reopening the hole.
        from redis.commands.redismodules import RedisModuleCommands

        accessors = [
            name for name, member in vars(RedisModuleCommands).items()
            if callable(member) and not name.startswith("_")
        ]  # fmt: skip
        assert {"ft", "json", "ts"} <= set(accessors)
        for name in accessors:
            module = getattr(rec, name)()
            if hasattr(module, "pipeline"):
                with pytest.raises(nb.Denied):
                    module.pipeline()

    def test_module_clients_still_send_their_commands_through_the_guard(self, nb, rec):
        from redis.commands.search.field import TextField
        from redis.commands.search.index_definition import IndexDefinition

        p = nb.PREFIX
        rec.json().set(f"{p}cr:customer:C1", "$", {"name": "x"})
        assert rec.sent[-1][:3] == ("JSON.SET", f"{p}cr:customer:C1", "$")
        rec.ft(f"{p}i").create_index(
            [TextField("t")], definition=IndexDefinition(prefix=[f"{p}rs:doc:"])
        )
        assert [a for a in rec.sent if a[0] == "FT.CREATE"][-1][:2] == ("FT.CREATE", f"{p}i")
        sent = len(rec.sent)
        with pytest.raises(nb.Denied):
            rec.json().set("prod:customer:1", "$", {})
        with pytest.raises(nb.Denied):
            rec.ft(f"{p}i").create_index([TextField("t")])  # no PREFIX clause
        with pytest.raises(nb.Denied):
            rec.ft("someones_idx").dropindex()
        assert len(rec.sent) == sent

    # --- tracing
    def test_every_call_is_traced_and_denials_show_about_zero_ms(self, nb, rec):
        rec.replies["PING"] = True
        rec.execute_command("PING")
        with pytest.raises(nb.Denied):
            rec.execute_command("FLUSHDB")
        ok, denied = nb.REDIS_TRACE
        assert (ok.cli, ok.status, ok.reply) == ("PING", "ok", "True")
        assert denied.status == "denied" and denied.cli == "FLUSHDB" and denied.ms < 5
        assert "FLUSHDB" in denied.reply

    def test_server_errors_are_traced_as_errors_and_reraised(self, nb, monkeypatch):
        def failing(self, *args, **options):
            raise nb.redis.ResponseError(f"Unknown index name (p={TRICKY})")

        monkeypatch.setattr(nb.redis.Redis, "execute_command", failing)
        nb.register_secret(TRICKY)

        class Bare(nb.GuardedTracedRedis):
            def __init__(self):
                pass

        with pytest.raises(nb.redis.ResponseError):
            Bare().execute_command("FT.INFO", "nope")
        call = nb.REDIS_TRACE[-1]
        assert call.status == "error" and "Unknown index" in call.reply and TRICKY not in call.reply

    def test_writes_are_paced_and_reads_are_not(self, nb, rec, monkeypatch):
        paced = []
        monkeypatch.setattr(nb, "pace", lambda: paced.append(1))
        rec.execute_command("PING")
        rec.execute_command("FT.SEARCH", "i", "*")
        rec.execute_command("HSET", f"{nb.PREFIX}k", "f", "v")
        with pytest.raises(nb.Denied):
            rec.execute_command("HSET", "prod:k", "f", "v")
        assert paced == [1]

    def test_pace_holds_writes_to_the_configured_rate(self, nb, monkeypatch):
        slept = []
        monkeypatch.setattr(nb.time, "sleep", slept.append)
        monkeypatch.setattr(nb, "WRITE_RATE", 10)
        nb._last_write[0] = 0.0
        nb.pace()
        assert slept == []
        nb.pace()
        assert (
            len(slept) == 1 and 0.05 < slept[0] < 0.11
        )  # 1 / 10 per second, give or take float error

    def test_redis_cli_renders_pasteable_lines_and_elides_vectors(self, nb):
        vector = b"\x00\x01" * 512
        line = nb.redis_cli(
            ["FT.SEARCH", "idx", "*=>[KNN 3 @v $vec AS dist]", "PARAMS", 2, "vec", vector]
        )
        assert line == 'FT.SEARCH idx "*=>[KNN 3 @v $vec AS dist]" PARAMS 2 vec <1024 bytes>'
        assert "\\x" not in line
        assert nb.redis_cli(["HSET", "k", "f", ""]) == 'HSET k f ""'
        assert nb.redis_cli(["SET", "k", 'say "hi"']) == 'SET k "say \\"hi\\""'
        assert nb.redis_cli([b"GET", b"k"]) == "GET k"
        assert "<300 chars>" in nb.redis_cli(["HSET", "k", "f", "x" * 300])

    def test_secrets_are_scrubbed_from_the_command_and_the_reply(self, nb, rec):
        nb.register_secret(ADMIN_KEY)  # plain, so it would show verbatim if not scrubbed
        assert nb.redis_cli(["AUTH", "default", ADMIN_KEY]) == "AUTH default ****"
        rec.replies["HGET"] = ADMIN_KEY.encode()
        rec.execute_command("HGET", "prod:k", "token")
        with pytest.raises(nb.Denied):
            rec.execute_command("AUTH", ADMIN_KEY)  # a denied command is traced too
        read, denied = nb.REDIS_TRACE
        assert (read.cli, read.reply) == ("HGET prod:k token", "'****'")
        assert (denied.cli, denied.status) == ("AUTH ****", "denied")
        assert ADMIN_KEY not in repr(nb.REDIS_TRACE)

    def test_a_long_secret_is_masked_before_the_reply_is_cut_short(self, nb, rec):
        # Cut by reprlib, 40 characters would leave 24 of them, and no longer match themselves.
        secret = "Zq8vN3pL0xW7kR5tM2bH9cD4fJ1yA6gE3sU7nP0q"
        assert len(secret) == 40
        nb.register_secret(secret)
        rec.replies["HGET"] = secret.encode()
        rec.replies["HGETALL"] = [b"password", secret.encode(), b"user", b"default"]
        rec.replies["JSON.GET"] = {"k": [secret, {"nested": secret.encode()}]}
        for argv in (("HGET", "prod:k", "f"), ("HGETALL", "prod:k"), ("JSON.GET", "prod:k")):
            rec.execute_command(*argv)
        replies = [call.reply for call in nb.REDIS_TRACE]
        assert replies[0] == "'****'"
        assert "b'user'" not in replies[1] and "'user'" in replies[1]  # decoded, and still readable
        for shown in (*replies, nb.render_redis_trace().text):
            assert secret[:10] not in shown and secret[-10:] not in shown

    def test_a_secret_with_both_kinds_of_quote_is_masked_in_a_reply(self, nb, rec):
        # repr() re-spells such a string, so the raw form is no longer in the finished text.
        secret = "ab'c\"d-9x-fake"
        nb.register_secret(secret)
        rec.replies["HGET"] = secret.encode()
        rec.execute_command("HGET", "prod:k", "f")
        assert nb.REDIS_TRACE[-1].reply == "'****'"

    def test_an_odd_reply_shape_never_breaks_a_command(self, nb, rec):
        rec.replies["HGETALL"] = {(1, 2): b"x", 3: [b"y", None, 1.5], b"k": {"nested": (b"z",)}}
        assert rec.execute_command("HGETALL", "prod:k") is rec.replies["HGETALL"]
        assert nb.REDIS_TRACE[-1].status == "ok"

    def test_long_values_are_elided_and_short_ones_kept(self, nb, rec):
        rec.replies["HGET"] = b"x" * 100
        rec.execute_command("HGET", "prod:k", "f")
        rec.replies["HGET"] = b"OK"
        rec.execute_command("HGET", "prod:k", "f")
        assert [c.reply for c in nb.REDIS_TRACE] == ["'<100 bytes>'", "'OK'"]

    def test_a_secret_inside_a_long_argument_is_masked_before_the_argument_is_cut(self, nb):
        secret = "Zq8vN3pL0xW7kR5tM2bH9cD4fJ1yA6gE"
        nb.register_secret(secret)
        line = nb.redis_cli(["HSET", "k", "f", "x" * 50 + secret + "y" * 100])
        assert secret[:10] not in line and secret[-10:] not in line and "****" in line

    def test_the_trace_view_scrubs_again_when_it_renders(self, nb):
        nb.REDIS_TRACE.append(nb.RedisCall("AUTH default late-secret-fake-9999", 1.0, "'late-secret-fake-9999'", "ok"))
        assert "late-secret-fake-9999" in nb.render_redis_trace().text
        nb.register_secret("late-secret-fake-9999")
        assert "late-secret-fake-9999" not in nb.render_redis_trace().text

    def test_redis_trace_renders(self, nb, rec):
        assert "no commands" in nb.render_redis_trace().text
        rec.execute_command("PING")
        with pytest.raises(nb.Denied):
            rec.execute_command("FLUSHALL")
        text = nb.render_redis_trace().text
        assert "PING" in text and "denied" in text

    def test_scan_keys_pages_with_count_1000_and_decodes(self, nb, rec):
        pages = iter([(7, [b"irisnb_a", b"irisnb_b"]), (0, [b"irisnb_c"])])
        rec.scan = lambda **kw: (rec.sent.append(("SCAN", kw)), next(pages))[1]
        assert list(nb.scan_keys(rec, "irisnb_*")) == ["irisnb_a", "irisnb_b", "irisnb_c"]
        assert rec.sent[0][1] == {"cursor": 0, "match": "irisnb_*", "count": 1000}
        assert rec.sent[1][1]["cursor"] == 7

    # --- connect_redis
    def test_connect_redis_pins_resp2_bytes_and_timeouts(self, nb, monkeypatch):
        captured = {}

        class Pool:
            connection_kwargs = {"protocol": 2}

        class Client:
            connection_pool = Pool()

            def ping(self):
                return True

        def from_url(cls, url, **kwargs):
            captured.update(url=url, **kwargs)
            return Client()

        monkeypatch.setattr(nb.GuardedTracedRedis, "from_url", classmethod(from_url))
        client = nb.connect_redis(f"redis://default:{quote(TRICKY, safe='')}@h:6379")
        assert isinstance(client, Client)
        assert captured["protocol"] == 2 and captured["decode_responses"] is False
        assert captured["socket_timeout"] == 10.0
        assert nb.scrub(TRICKY) == "****"  # the URL password is registered

    def test_connect_redis_errors_are_scrubbed_and_unchained(self, nb, monkeypatch):
        def from_url(cls, url, **kwargs):
            raise nb.redis.ConnectionError(f"Error connecting to {url}")

        monkeypatch.setattr(nb.GuardedTracedRedis, "from_url", classmethod(from_url))
        with pytest.raises(nb.ServiceError) as caught:
            nb.connect_redis("redis://default:hunter2-fake@h:6379")
        assert caught.value.kind == "connect" and "hunter2-fake" not in str(caught.value)
        assert caught.value.__context__ is None and caught.value.__cause__ is None

    def test_connect_redis_rejects_a_url_that_forces_resp3(self, nb, monkeypatch):
        class Pool:
            connection_kwargs = {"protocol": 3}

        class Client:
            connection_pool = Pool()

            def ping(self):
                return True

        monkeypatch.setattr(
            nb.GuardedTracedRedis, "from_url", classmethod(lambda cls, url, **kw: Client())
        )
        with pytest.raises(nb.ServiceError, match="RESP2"):
            nb.connect_redis("redis://h:6379?protocol=3")

    def test_connect_redis_without_a_url_is_a_config_error(self, nb):
        with pytest.raises(nb.ServiceError) as caught:
            nb.connect_redis("")
        assert caught.value.kind == "config"


# ---- owned_index ----------------------------------------------------------------------
def ft_info(name, prefixes, *, as_bytes=True, extra=()):
    """A RESP2-shaped FT.INFO reply: flat lists, bytes everywhere."""
    enc = (lambda s: s.encode()) if as_bytes else (lambda s: s)
    definition = [enc("key_type"), enc("HASH")]
    if prefixes is not None:
        definition += [enc("prefixes"), [enc(p) for p in prefixes]]
    definition += [enc("default_score"), enc("1")]
    info = [
        enc("index_name"),
        enc(name),
        enc("index_options"),
        [],
        enc("index_definition"),
        definition,
    ]
    return info + list(extra)


class TestOwnedIndex:
    def test_ours_is_true_and_grants_nothing(self, nb):
        info = ft_info(f"{nb.CR_PREFIX}idx", [f"{nb.CR_PREFIX}customer:", f"{nb.CR_PREFIX}order:"])
        assert nb.owned_index(info, nb.CR_PREFIX) is True
        assert nb.OWNED_INDEXES == set() and nb.ORPHANS["index"] == set()

    def test_strings_and_dict_replies_work_too(self, nb):
        assert nb.owned_index(ft_info("i1", [nb.PREFIX + "a"], as_bytes=False), nb.PREFIX) is True
        as_dict = {"index_name": "i2", "index_definition": {"prefixes": [nb.PREFIX + "a"]}}
        assert nb.owned_index(as_dict, nb.PREFIX) is True
        as_bytes_dict = {
            b"index_name": b"i3",
            b"index_definition": [b"prefixes", [nb.PREFIX.encode()]],
        }
        assert nb.owned_index(as_bytes_dict, nb.PREFIX) is True

    def test_other_runs_pass_under_the_shared_irisnb_prefix(self, nb):
        assert (
            nb.owned_index(
                ft_info("irisnb_deadbeef_rs:idx", ["irisnb_deadbeef_rs:doc:"]), "irisnb_"
            )
            is True
        )
        assert (
            nb.owned_index(
                ft_info("irisnb_deadbeef_rs:idx", ["irisnb_deadbeef_rs:doc:"]), nb.PREFIX
            )
            is False
        )

    def test_the_demos_memory_index_is_never_owned(self, nb):
        info = ft_info("memory:store123:ltm", ["memory:store123:ltm:"])
        for sub_prefix in (nb.PREFIX, nb.CR_PREFIX, "irisnb_", "", "memory:", "memory:store123:"):
            assert nb.owned_index(info, sub_prefix) is False
        assert nb.OWNED_INDEXES == set()

    def test_empty_missing_and_malformed_prefixes_fail_closed(self, nb):
        cases = {
            "empty list": ft_info("i", []),
            "missing": ft_info("i", None),
            "empty string prefix": ft_info("i", [""]),
            "one foreign": ft_info("i", [nb.PREFIX + "a", "prod:"]),
            "one empty": ft_info("i", [nb.PREFIX + "a", ""]),
            "no definition": [b"index_name", b"i"],
            "odd length": [b"index_name", b"i", b"index_definition"],
            "not a reply": None,
            "a string": "index_definition",
            "an int": 7,
            "unhashable keys": [[b"a"], b"x", b"index_definition", []],
            "definition not a list": [b"index_definition", b"prefixes"],
            "prefix not a string": {"index_definition": {"prefixes": [7]}},
        }
        for label, info in cases.items():
            assert nb.owned_index(info, nb.PREFIX) is False, label
        assert nb.OWNED_INDEXES == set()

    def test_sub_prefix_must_be_in_our_namespace(self, nb):
        info = ft_info("i", ["demo:x:"])
        assert nb.owned_index(info, "demo:") is False
        assert nb.owned_index(info, "") is False
        assert nb.owned_index(info, None) is False


# ---- grants ---------------------------------------------------------------------------
GRANT_NAMES = {"OWNED_INDEXES", "APPROVED", "ORPHANS"}
MUTATORS = {"add", "update", "discard", "remove", "pop", "clear", "setdefault",
            "difference_update", "intersection_update", "symmetric_difference_update"}  # fmt: skip
# The only functions that may write a grant, and the set methods each may call.
GRANT_WRITERS = {
    "claim_index": {"add"}, "approve_orphan_index": {"add"}, "approve_am_memories": {"update"},
    "approve_am_orphans": {"update"}, "approve_am_session_orphans": {"update"},
    "approve_cr_orphan": {"add"}, "approve_lc_entry": {"add"},
    "_authorise": {"discard"},  # a confirmed orphan index is good for one drop
}  # fmt: skip


def grant_writes(source):
    """Every write to a grant set that is not made by its own helper. Empty means the rule holds."""

    def named(node):
        node = node.value if isinstance(node, ast.Subscript) else node
        return isinstance(node, ast.Name) and node.id in GRANT_NAMES

    found = []

    def walk(node, function):
        if isinstance(node, ast.FunctionDef):
            function = function or node.name  # a nested function belongs to its outer one
        where = function or "module level"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in MUTATORS
            and named(node.func.value)
            and node.func.attr not in GRANT_WRITERS.get(function, set())
        ):
            found.append(f"{where}: .{node.func.attr}()")
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        for target in targets:
            # Inside a def a bare name is a rebinding; at module level it is the definition.
            rebinding = function and isinstance(target, ast.Name)
            if named(target) and (
                rebinding or isinstance(target, ast.Subscript) or isinstance(node, ast.AugAssign)
            ):
                found.append(f"{where}: assignment")
        for child in ast.iter_child_nodes(node):
            walk(child, function)

    walk(ast.parse(source), None)
    return found


def other_index(nb, name="idx:cust", run=None, prefixes=None):
    """FT.INFO for an index some other run of the notebook left behind."""
    run = run or other_run(nb)
    return ft_info(name, prefixes or [f"irisnb_{run}_cr:customer:", f"irisnb_{run}_cr:order:"])


class TestGrants:
    """The choke points accept only what a helper granted, and the helpers check first."""

    def denied(self, nb, rec, *argv):
        with pytest.raises(nb.Denied):
            rec.execute_command(*argv)

    # --- indexes
    def test_a_listing_alone_grants_nothing(self, nb, rec, mock_http):
        # The orphans panel reads every FT.INFO under irisnb_ to count other runs' indexes.
        info = other_index(nb)
        assert nb.owned_index(info, "irisnb_") is True
        self.denied(nb, rec, "FT.DROPINDEX", "idx:cust")
        mock_http(json={"context_surfaces": [foreign_surface(nb)]})
        nb.http_call("cr", "GET", f"{CR}/context-surfaces")
        memories = [{"id": "m1", "ownerId": f"ib-{other_run(nb)}", "createdAt": iso_in(-99999)}]
        mock_http(json={"items": memories})
        nb.http_call("am", "POST", f"{AM}/long-term-memory/search", json=scoped_search(nb))
        assert nb.OWNED_INDEXES == set()
        assert all(not grants for grants in (*nb.APPROVED.values(), *nb.ORPHANS.values()))

    def test_claim_index_lets_the_guard_drop_this_runs_service_named_index(self, nb, rec):
        info = ft_info("idx:customer_7f3a", [f"{nb.CR_PREFIX}customer:", f"{nb.CR_PREFIX}order:"])
        self.denied(nb, rec, "FT.DROPINDEX", "idx:customer_7f3a")
        assert nb.claim_index(info) is True
        rec.execute_command("FT.DROPINDEX", "idx:customer_7f3a")
        self.denied(nb, rec, "FT.DROPINDEX", "idx:customer_7f3a", "DD")
        assert nb.claim_index(ft_info("idx:docs", [f"{nb.RS_PREFIX}doc:"])) is True

    def test_claim_index_refuses_anything_not_confined_to_this_run(self, nb):
        cases = {
            "another run's": other_index(nb),
            "the demo's memory index": ft_info("memory:store:ltm", ["memory:store:ltm:"]),
            "half ours": ft_info("idx:mixed", [f"{nb.CR_PREFIX}customer:", "customer:"]),
            "the whole keyspace": ft_info("idx:all", [""]),
            "no prefixes": ft_info("idx:none", None),
            "unreadable": None,
            "nameless": [b"index_definition", [b"prefixes", [nb.CR_PREFIX.encode()]]],
        }
        for label, info in cases.items():
            assert nb.claim_index(info) is False, label
        assert nb.OWNED_INDEXES == set()

    def test_an_orphan_index_needs_the_typed_confirmation_and_is_good_for_one_drop(self, nb, rec):
        info = other_index(nb)
        for wrong in ("", "delete", "yes", "delete orphans, please", None):
            with pytest.raises(ValueError):
                nb.approve_orphan_index(info, wrong)
        assert nb.ORPHANS["index"] == set()
        self.denied(nb, rec, "FT.DROPINDEX", "idx:cust")
        nb.approve_orphan_index(info, f"  {nb.ORPHAN_CONFIRM}  ")
        self.denied(nb, rec, "FT.DROPINDEX", "idx:cust", "DD")  # never, and it costs nothing
        rec.execute_command("FT.DROPINDEX", "idx:cust")
        self.denied(nb, rec, "FT.DROPINDEX", "idx:cust")  # used up
        assert nb.ORPHANS["index"] == set()

    def test_an_orphan_index_grant_reaches_no_further_than_the_drop(self, nb, rec):
        nb.approve_orphan_index(other_index(nb), nb.ORPHAN_CONFIRM)
        self.denied(nb, rec, "HSET", "idx:cust", "f", "v")
        self.denied(nb, rec, "UNLINK", "idx:cust")
        self.denied(nb, rec, "UNLINK", f"irisnb_{other_run(nb)}_cr:customer:C1")
        self.denied(nb, rec, "FT.DROPINDEX", "idx:another")

    def test_approve_orphan_index_refuses_what_is_not_another_runs(self, nb):
        run, third = other_run(nb), "0123abcd"
        cases = {
            "this run's own": ft_info(f"{nb.PREFIX}idx", [f"{nb.CR_PREFIX}customer:"]),
            "the demo's memory index": ft_info("memory:store:ltm", ["memory:store:ltm:"]),
            "half another run's": other_index(nb, prefixes=[f"irisnb_{run}_cr:a:", "customer:"]),
            "two other runs'": other_index(nb, prefixes=[f"irisnb_{run}_cr:a:", f"irisnb_{third}_cr:b:"]),
            "a run id that is not hex": other_index(nb, prefixes=["irisnb_deadbeeg_cr:a:"]),
            "the whole keyspace": ft_info("idx:all", [""]),
            "nameless": [b"index_definition", [b"prefixes", [f"irisnb_{run}_cr:a:".encode()]]],
            "unreadable": None,
        }  # fmt: skip
        for label, info in cases.items():
            with pytest.raises(ValueError):
                nb.approve_orphan_index(info, nb.ORPHAN_CONFIRM)
            assert nb.ORPHANS["index"] == set(), label

    # --- Agent Memory
    def test_approve_am_memories_checks_every_owner_and_grants_all_or_nothing(self, nb):
        good = [{"id": "ext-1", "ownerId": nb.AM_OWNER}, {"id": "ext-2", "ownerId": nb.AM_OWNER}]
        spoiled = [
            {"id": "x", "ownerId": "CUST-DEMO-001"},
            {"id": "x"},  # no ownerId key at all
            {"id": "x", "owner_id": nb.AM_OWNER},  # the field misspelt
            {"id": "x", "ownerId": f"ib-{other_run(nb)}"},
            {"ownerId": nb.AM_OWNER},  # no id
            {"id": 5, "ownerId": nb.AM_OWNER},
            "ext-3",
        ]
        for item in spoiled:
            with pytest.raises(ValueError):
                nb.approve_am_memories([*good, item])
            assert nb.APPROVED["am_memory"] == set()
        nb.approve_am_memories(good)
        assert nb.APPROVED["am_memory"] == {"ext-1", "ext-2"}
        assert nb.ORPHANS["am_memory"] == set()

    def test_approve_am_orphans_wants_confirmation_a_stale_ib_owner_and_not_this_run(self, nb):
        def orphan(**changes):
            base = {"id": "orph-1", "ownerId": f"ib-{other_run(nb)}", "createdAt": iso_in(-3 * 3600)}
            return base | changes

        with pytest.raises(ValueError):
            nb.approve_am_orphans([orphan()], "yes")
        spoiled = {
            "this run's": orphan(ownerId=nb.AM_OWNER),
            "a customer's": orphan(ownerId="CUST-DEMO-001"),
            "ib- but not a run id": orphan(ownerId="ib-orphan1"),
            "no owner": orphan(ownerId=None),
            "too young": orphan(createdAt=iso_in(-3600)),
            "no age": orphan(createdAt=None),
            "an unreadable age": orphan(createdAt="last tuesday"),
            "no id": orphan(id=None),
            "not a memory": "orph-1",
        }
        for label, item in spoiled.items():
            with pytest.raises(ValueError):
                nb.approve_am_orphans([orphan(id="fine-1"), item], nb.ORPHAN_CONFIRM)
            assert nb.ORPHANS["am_memory"] == set(), label
        nb.approve_am_orphans([orphan(), orphan(id="orph-2", createdAt=iso_in(-3 * 3600))], nb.ORPHAN_CONFIRM)
        assert nb.ORPHANS["am_memory"] == {"orph-1", "orph-2"}
        assert nb.APPROVED["am_memory"] == set()

    def test_approve_am_session_orphans_wants_confirmation_and_another_runs_session(self, nb):
        theirs = f"ib-{other_run(nb)}-s3"
        with pytest.raises(ValueError):
            nb.approve_am_session_orphans([theirs], "")
        for bad in (f"{nb.AM_OWNER}-s1", "demo-session-123", f"ib-{other_run(nb)}-m1", "ib-orphan1-s1", 7):
            with pytest.raises(ValueError):
                nb.approve_am_session_orphans([theirs, bad], nb.ORPHAN_CONFIRM)
            assert nb.ORPHANS["am_session"] == set()
        nb.approve_am_session_orphans(iter([theirs]), nb.ORPHAN_CONFIRM)  # any iterable will do
        assert nb.ORPHANS["am_session"] == {theirs}

    def test_approve_cr_orphan_wants_confirmation_and_a_stale_surface_stamped_by_this_notebook(self, nb):
        with pytest.raises(ValueError):
            nb.approve_cr_orphan(foreign_surface(nb), "no")
        run = other_run(nb)
        spoiled = {
            "this run's": foreign_surface(nb, name=nb.RUN_TAG, metadata={"created_by": nb.CR_CREATED_BY, "run": nb.RUN}),
            "the demo's (no metadata)": foreign_surface(nb, name="radish-bank", metadata=None),
            "unstamped": foreign_surface(nb, metadata={"run": run}),
            "someone else's stamp": foreign_surface(nb, metadata={"created_by": "make-setup", "run": run}),
            "a run that is not hex": foreign_surface(nb, name="iris-basics-orphan1", metadata={"created_by": nb.CR_CREATED_BY, "run": "orphan1"}),
            "a name that disagrees": foreign_surface(nb, name="radish-bank"),
            "too young": foreign_surface(nb, created_at=iso_in(-3600)),
            "no age": foreign_surface(nb, created_at=None),
            "no id": foreign_surface(nb, id=None),
            "not a surface": "ORPHAN1",
        }  # fmt: skip
        for label, surface in spoiled.items():
            with pytest.raises(ValueError):
                nb.approve_cr_orphan(surface, nb.ORPHAN_CONFIRM)
            assert nb.ORPHANS["cr_surface"] == set(), label
        nb.approve_cr_orphan(foreign_surface(nb), nb.ORPHAN_CONFIRM)
        assert nb.ORPHANS["cr_surface"] == {"ORPHAN1"}

    def test_approve_lc_entry_takes_one_plain_id(self, nb):
        for bad in ("", "a/b", "two words", "e1?x=1", None, 5):
            with pytest.raises(ValueError):
                nb.approve_lc_entry(bad)
        assert nb.APPROVED["lc_entry"] == set()
        with pytest.raises(ValueError):
            nb.approve_lc_entry("entrée")
        nb.approve_lc_entry("0123abcd" * 4)
        assert nb.APPROVED["lc_entry"] == {"0123abcd" * 4}

    # --- who may write
    def test_only_the_grant_helpers_write_grants(self, nb_file):
        assert grant_writes(nb_file.read_text(encoding="utf-8")) == []

    def test_the_rule_catches_a_write_from_anywhere_else(self):
        cases = {
            "a cell": '@app.cell\ndef _():\n    APPROVED["am_memory"].add("x")\n    return\n',
            "another function": 'def helper():\n    OWNED_INDEXES.add("i")\n',
            "a rebinding": "def helper():\n    global OWNED_INDEXES\n    OWNED_INDEXES = set()\n",
            "item assignment": 'def helper():\n    ORPHANS["index"] = {"i"}\n',
            "augmented assignment": 'OWNED_INDEXES |= {"i"}\n',
            "a delete": 'def helper():\n    del APPROVED["lc_entry"]\n',
            "module level": 'ORPHANS["index"].add("i")\n',
            "a helper calling the wrong method": "def claim_index(info):\n    OWNED_INDEXES.clear()\n",
        }
        for label, source in cases.items():
            assert grant_writes(source), label

    def test_the_rule_lets_the_helpers_and_readers_be(self):
        source = textwrap.dedent('''
            OWNED_INDEXES = set()
            APPROVED = {"lc_entry": set()}

            def claim_index(info):
                OWNED_INDEXES.add("i")
                def inner():
                    OWNED_INDEXES.add("j")

            def _authorise(argv):
                ORPHANS["index"].discard("i")

            def reader(x):
                return x in APPROVED["lc_entry"] or x in OWNED_INDEXES or len(ORPHANS["index"])
        ''')
        assert grant_writes(source) == []


# ---- ledger ---------------------------------------------------------------------------
class TestLedger:
    def test_round_trip_through_intent_created_deleted(self, nb):
        assert nb.ledger_live("am", "memory") == []
        nb.ledger_add("am", "memory", "ib-1-m1", "intent")
        assert nb.ledger_live("am", "memory") == []
        assert nb.ledger_live("am", "memory", pending=True) == ["ib-1-m1"]
        nb.ledger_add("am", "memory", "ib-1-m1", "created")
        nb.ledger_add("am", "memory", "ib-1-m2")
        assert nb.ledger_live("am", "memory") == ["ib-1-m1", "ib-1-m2"]
        nb.ledger_add("am", "memory", "ib-1-m1", "deleted")
        assert nb.ledger_live("am", "memory") == ["ib-1-m2"]
        assert nb.ledger_live("am", "memory", pending=True) == ["ib-1-m2"]
        assert nb.ledger_state("am", "memory") == {"ib-1-m1": "deleted", "ib-1-m2": "created"}

    def test_columns_and_kinds_are_separate(self, nb):
        nb.ledger_add("am", "memory", "x")
        nb.ledger_add("am", "session", "y")
        nb.ledger_add("lc", "entry", "z")
        assert nb.ledger_live("am", "memory") == ["x"]
        assert nb.ledger_live("am", "session") == ["y"]
        assert nb.ledger_live("lc", "entry") == ["z"]
        assert nb.ledger_live("cr", "surface") == []

    def test_the_same_kind_in_two_columns_stays_apart(self, nb):
        # rs and cr both record kinds "key" and "index"; only the column tells them apart, and
        # cr's cleanup must never treat rs's keys as its own.
        nb.ledger_add("rs", "key", f"{nb.RS_PREFIX}doc:1")
        nb.ledger_add("cr", "key", f"{nb.CR_PREFIX}customer:C1")
        nb.ledger_add("cr", "index", f"{nb.CR_PREFIX}idx")
        assert nb.ledger_live("rs", "key") == [f"{nb.RS_PREFIX}doc:1"]
        assert nb.ledger_live("cr", "key") == [f"{nb.CR_PREFIX}customer:C1"]
        assert nb.ledger_live("rs", "index") == []
        nb.ledger_add("cr", "key", f"{nb.CR_PREFIX}customer:C1", "deleted")
        assert nb.ledger_live("cr", "key") == []
        assert nb.ledger_live("rs", "key") == [f"{nb.RS_PREFIX}doc:1"]

    def test_survives_a_new_process_and_reads_other_runs(self, nb, load_fresh, tmp_path):
        nb.ledger_add("rs", "key", f"{nb.RS_PREFIX}doc:1")
        again = load_fresh(IRIS_BASICS_RUN=nb.RUN)  # a kernel restart: same RUN, new module
        assert again.RUN == nb.RUN
        assert again.ledger_live("rs", "key") == [f"{nb.RS_PREFIX}doc:1"]
        stranger = load_fresh(IRIS_BASICS_RUN="0123abcd")
        assert stranger.ledger_live("rs", "key") == []
        assert stranger.ledger_live("rs", "key", run=nb.RUN) == [f"{nb.RS_PREFIX}doc:1"]

    def test_runs_are_listed_oldest_first_and_only_real_ones(self, nb, load_fresh):
        assert nb.ledger_runs() == []
        nb.ledger_add("rs", "key", "a")
        second = load_fresh(IRIS_BASICS_RUN="0123abcd")
        second.ledger_add("rs", "key", "b")
        (nb.ledger_dir() / "notes.jsonl").write_text("x")  # not a run file
        (nb.ledger_dir() / "ABCDEF12.jsonl").write_text("x")  # wrong case
        os.utime(nb.ledger_dir() / f"{nb.RUN}.jsonl", (1_000_000, 1_000_000))
        assert nb.ledger_runs() == [nb.RUN, "0123abcd"]

    def test_the_file_is_jsonl_with_timestamps_and_no_secrets(self, nb):
        nb.ledger_add("cr", "surface", "SURF1")
        (line,) = (nb.ledger_dir() / f"{nb.RUN}.jsonl").read_text(encoding="utf-8").splitlines()
        entry = json.loads(line)
        assert {k: entry[k] for k in ("column", "kind", "ident", "state")} == {
            "column": "cr", "kind": "surface", "ident": "SURF1", "state": "created",
        }  # fmt: skip
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", entry["t"])

    def test_secrets_are_refused(self, nb):
        nb.register_secret(AGENT_KEY)
        with pytest.raises(ValueError):
            nb.ledger_add("cr", "agent_key", AGENT_KEY)
        with pytest.raises(ValueError):
            nb.ledger_add("cr", "agent_key", "cs_agent_looksLikeAKey")
        assert nb.ledger_live("cr", "agent_key") == []

    def test_bad_columns_and_states_are_refused(self, nb):
        with pytest.raises(ValueError):
            nb.ledger_add("xx", "key", "a")
        with pytest.raises(ValueError):
            nb.ledger_add("rs", "key", "a", "gone")

    def test_the_kinds_are_the_documented_ones(self, nb):
        assert nb.LEDGER_KINDS == {
            "rs": {"key", "index"},
            "lc": {"entry"},
            "cr": {"surface", "agent_key", "key", "index"},
            "am": {"session", "memory"},
        }
        for column, kinds in nb.LEDGER_KINDS.items():
            for kind in kinds:
                nb.ledger_add(column, kind, f"{column}-{kind}")
                assert nb.ledger_live(column, kind) == [f"{column}-{kind}"]

    def test_a_kind_that_is_not_documented_is_refused_on_write_and_on_read(self, nb):
        # A typo would otherwise store an item cleanup can never find: for Agent Memory,
        # which has no server-side expiry, that is a leak with no end.
        wrong = [("am", "memories"), ("am", "entry"), ("lc", "session"), ("rs", "surface"),
                 ("cr", "memory"), ("xx", "key")]  # fmt: skip
        for column, kind in wrong:
            with pytest.raises(ValueError):
                nb.ledger_add(column, kind, "a")
            with pytest.raises(ValueError):
                nb.ledger_state(column, kind)
            with pytest.raises(ValueError):
                nb.ledger_live(column, kind, pending=True)
        assert not (nb.ledger_dir() / f"{nb.RUN}.jsonl").exists()

    def test_a_line_cut_short_by_a_crash_is_skipped_and_costs_nothing_else(self, nb):
        path = nb.ledger_dir() / f"{nb.RUN}.jsonl"
        nb.ledger_add("rs", "key", "a")
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"column": "rs", "kind": "key", "ident": "b", "sta')  # no newline
        assert nb.ledger_live("rs", "key") == ["a"]
        # Appended straight after the torn text, "c" would fuse with it and be lost.
        nb.ledger_add("rs", "key", "c")
        nb.ledger_add("rs", "key", "d")
        assert nb.ledger_live("rs", "key") == ["a", "c", "d"]
        assert len(path.read_text(encoding="utf-8").splitlines()) == 4  # a, the torn one, c, d

    def test_directory_resolution_order(self, nb, monkeypatch, tmp_path):
        monkeypatch.setenv("IRIS_BASICS_LEDGER_DIR", str(tmp_path / "explicit"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        assert nb.ledger_dir() == tmp_path / "explicit"
        monkeypatch.delenv("IRIS_BASICS_LEDGER_DIR")
        assert nb.ledger_dir() == tmp_path / "local" / "iris-basics"
        monkeypatch.delenv("LOCALAPPDATA")
        monkeypatch.setattr(nb.Path, "home", classmethod(lambda cls: tmp_path / "home"))
        assert nb.ledger_dir() == tmp_path / "home" / ".iris-basics"


# ---- identity, pressed, AUTORUN ---------------------------------------------------------
class TestIdentity:
    def test_prefixes_and_ttls(self, nb):
        assert re.fullmatch(r"[0-9a-f]{8}", nb.RUN)
        assert nb.PREFIX == f"irisnb_{nb.RUN}_"
        assert nb.RS_PREFIX == nb.PREFIX + "rs:" and nb.CR_PREFIX == nb.PREFIX + "cr:"
        assert nb.RUN_TAG == f"iris-basics-{nb.RUN}" and nb.AM_OWNER == f"ib-{nb.RUN}"
        assert nb.AM_NAMESPACE == "iris-basics-notebook"
        assert nb.CR_CREATED_BY == "iris-basics-notebook"
        assert (nb.KEY_TTL_S, nb.LC_TTL_MS, nb.LC_TEACH_TTL_MS) == (3600, 900_000, 120_000)
        assert nb.CR_KEY_TTL_S == 7200
        assert nb.RS_PREFIX.startswith(nb.PREFIX) and nb.CR_PREFIX.startswith(nb.PREFIX)

    def test_run_is_random_unless_pinned_and_valid(self, load_fresh):
        first, second = load_fresh(), load_fresh()
        assert re.fullmatch(r"[0-9a-f]{8}", first.RUN) and first.RUN != second.RUN
        assert first.RUN_PINNED is False
        pinned = load_fresh(IRIS_BASICS_RUN="0123abcd")
        assert pinned.RUN == "0123abcd" and pinned.RUN_PINNED is True
        for bad in ("0123ABCD", "0123abc", "0123abcde", "0123abcg", " 0123abcd", "0123abcd\n"):
            fresh = load_fresh(IRIS_BASICS_RUN=bad)
            assert fresh.RUN != bad and fresh.RUN_PINNED is False, bad

    def test_prefix_is_a_function_of_run(self, load_fresh):
        fresh = load_fresh(IRIS_BASICS_RUN="0123abcd")
        assert fresh.PREFIX == "irisnb_0123abcd_" and fresh.RUN_TAG == "iris-basics-0123abcd"
        assert fresh.AM_OWNER == "ib-0123abcd" and fresh.CR_PREFIX == "irisnb_0123abcd_cr:"


class TestAutorun:
    def test_off_by_default(self, nb, load_fresh):
        assert nb.AUTORUN == ""
        assert load_fresh().AUTORUN == ""

    @pytest.mark.parametrize("stage", ["demo", "cleanup"])
    def test_honoured_with_a_valid_pinned_run(self, load_fresh, stage):
        fresh = load_fresh(IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN=stage)
        assert fresh.AUTORUN == stage

    @pytest.mark.parametrize("stage", ["demo", "cleanup"])
    def test_ignored_without_a_run(self, load_fresh, stage):
        assert load_fresh(IRIS_BASICS_AUTORUN=stage).AUTORUN == ""

    @pytest.mark.parametrize("bad_run", ["", "not-a-run", "0123ABCD", "0123abc", "0123abcdef"])
    def test_ignored_with_an_invalid_run(self, load_fresh, bad_run):
        assert load_fresh(IRIS_BASICS_RUN=bad_run, IRIS_BASICS_AUTORUN="demo").AUTORUN == ""

    @pytest.mark.parametrize("stage", ["", "DEMO", "all", "true", "1", "demo ", "cleanup;demo"])
    def test_ignored_for_any_other_value(self, load_fresh, stage):
        assert load_fresh(IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN=stage).AUTORUN == ""

    # marimo copies a .env beside the notebook into os.environ before the notebook starts,
    # so a value that came from that file looks just like one set on purpose. These tests set
    # the variables in the environment, as marimo would have, and name them in the file.
    @pytest.mark.parametrize(
        "line",
        ["IRIS_BASICS_AUTORUN=demo", "export IRIS_BASICS_AUTORUN=demo", "  IRIS_BASICS_AUTORUN = demo",
         'IRIS_BASICS_AUTORUN="demo"', "IRIS_BASICS_AUTORUN="],
    )
    def test_ignored_when_a_dotenv_names_it(self, load_fresh, tmp_path, line):
        (tmp_path / ".env").write_text(f"LANGCACHE_HOST=x\r\n{line}\r\n", encoding="utf-8")
        fresh = load_fresh(IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN="demo")
        assert fresh.AUTORUN == ""
        assert fresh.RUN == "0123abcd"  # the pin is not the file's doing here

    def test_a_pin_from_a_dotenv_is_ignored_too(self, load_fresh, tmp_path):
        (tmp_path / ".env").write_bytes(b"\xef\xbb\xbfIRIS_BASICS_RUN=0123abcd\n")
        fresh = load_fresh(IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN="demo")
        assert fresh.RUN_PINNED is False and fresh.RUN != "0123abcd" and fresh.AUTORUN == ""

    def test_a_dotenv_that_does_not_name_them_changes_nothing(self, load_fresh, tmp_path):
        (tmp_path / ".env").write_text(
            "# IRIS_BASICS_AUTORUN=demo\nLANGCACHE_HOST=IRIS_BASICS_AUTORUN=x\nREDIS_HOST=h\n",
            encoding="utf-8",
        )
        fresh = load_fresh(IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN="demo")
        assert (fresh.RUN, fresh.AUTORUN) == ("0123abcd", "demo")

    def test_the_dotenv_is_looked_for_beside_the_notebook_even_when_told_not_to_read_it(
        self, load_fresh, tmp_path, monkeypatch
    ):
        # IRIS_BASICS_NO_DOTENV only stops this notebook reading settings from the file;
        # marimo has already loaded it, so the check on RUN and AUTORUN must not be skipped.
        assert os.environ["IRIS_BASICS_NO_DOTENV"] == "1"
        elsewhere = tmp_path / "nb"
        elsewhere.mkdir()
        (elsewhere / ".env").write_text("IRIS_BASICS_AUTORUN=demo\n", encoding="utf-8")
        monkeypatch.setattr("marimo.notebook_dir", lambda: elsewhere)
        fresh = load_fresh(IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN="demo")
        assert fresh.AUTORUN == ""

    # marimo looks for the .env beside the nearest pyproject.toml above the notebook, and
    # beside the notebook only when there is none. A name in either file must be caught.
    @pytest.mark.parametrize("where", ["project root", "notebook folder"])
    def test_ignored_when_a_dotenv_in_a_project_names_it(
        self, load_fresh, tmp_path, monkeypatch, where
    ):
        notebook = tmp_path / "project" / "notebooks"
        notebook.mkdir(parents=True)
        (tmp_path / "project" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        folder = tmp_path / "project" if where == "project root" else notebook
        (folder / ".env").write_text("IRIS_BASICS_AUTORUN=demo\n", encoding="utf-8")
        monkeypatch.setattr("marimo.notebook_dir", lambda: notebook)
        fresh = load_fresh(IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN="demo")
        assert fresh.AUTORUN == ""

    def test_a_pyproject_root_dotenv_that_does_not_name_them_changes_nothing(
        self, load_fresh, tmp_path, monkeypatch
    ):
        notebook = tmp_path / "project" / "notebooks"
        notebook.mkdir(parents=True)
        (tmp_path / "project" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (tmp_path / "project" / ".env").write_text("REDIS_HOST=h\n", encoding="utf-8")
        monkeypatch.setattr("marimo.notebook_dir", lambda: notebook)
        fresh = load_fresh(IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN="demo")
        assert (fresh.RUN, fresh.AUTORUN) == ("0123abcd", "demo")

    # marimo reports "edit" for `marimo export` as well as for `marimo edit`, so only the
    # explicit IRIS_BASICS_HEADLESS=1 lets an export autorun. TestExportedBanner runs this
    # for real; here mo.app_meta() is faked to cover every mode and every spelling.
    @pytest.mark.parametrize("mode", ["run", "script", "test", None])
    def test_every_mode_but_edit_honours_it_headless_or_not(
        self, nb, load_fresh, monkeypatch, mode
    ):
        monkeypatch.setattr(nb.mo, "app_meta", lambda: SimpleNamespace(mode=mode))
        for headless in ({}, {"IRIS_BASICS_HEADLESS": "1"}):
            fresh = load_fresh(IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN="demo", **headless)
            assert fresh.AUTORUN == "demo"

    @pytest.mark.parametrize("stage", ["demo", "cleanup"])
    def test_refused_in_marimo_edit_unless_the_run_says_it_is_headless(
        self, nb, load_fresh, monkeypatch, stage
    ):
        monkeypatch.setattr(nb.mo, "app_meta", lambda: SimpleNamespace(mode="edit"))
        env = {"IRIS_BASICS_RUN": "0123abcd", "IRIS_BASICS_AUTORUN": stage}
        assert load_fresh(**env).AUTORUN == ""
        assert load_fresh(**env, IRIS_BASICS_HEADLESS="1").AUTORUN == stage

    @pytest.mark.parametrize("headless", ["", "0", "true", "yes", "on", "1 ", "11"])
    def test_only_a_literal_1_means_headless(self, nb, load_fresh, monkeypatch, headless):
        monkeypatch.setattr(nb.mo, "app_meta", lambda: SimpleNamespace(mode="edit"))
        fresh = load_fresh(
            IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN="demo", IRIS_BASICS_HEADLESS=headless
        )
        assert fresh.AUTORUN == ""

    def test_headless_alone_turns_nothing_on(self, nb, load_fresh, monkeypatch):
        monkeypatch.setattr(nb.mo, "app_meta", lambda: SimpleNamespace(mode="edit"))
        for env in ({}, {"IRIS_BASICS_RUN": "0123abcd"}, {"IRIS_BASICS_AUTORUN": "demo"}):
            assert load_fresh(IRIS_BASICS_HEADLESS="1", **env).AUTORUN == ""

    def test_a_headless_flag_from_a_dotenv_is_ignored_too(
        self, nb, load_fresh, tmp_path, monkeypatch
    ):
        (tmp_path / ".env").write_text("IRIS_BASICS_HEADLESS=1\n", encoding="utf-8")
        monkeypatch.setattr(nb.mo, "app_meta", lambda: SimpleNamespace(mode="edit"))
        fresh = load_fresh(
            IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN="demo", IRIS_BASICS_HEADLESS="1"
        )
        assert fresh.AUTORUN == ""


class TestPressed:
    def test_a_click_always_counts(self, nb, monkeypatch):
        for autorun in ("", "demo", "cleanup"):
            monkeypatch.setattr(nb, "AUTORUN", autorun)
            for stage in ("demo", "cleanup"):
                assert nb.pressed(Btn(True), stage) is True

    def test_stage_semantics(self, nb, monkeypatch):
        idle = Btn(False)
        monkeypatch.setattr(nb, "AUTORUN", "")
        assert nb.pressed(idle) is False and nb.pressed(idle, "cleanup") is False
        monkeypatch.setattr(nb, "AUTORUN", "demo")
        assert nb.pressed(idle) is True
        assert nb.pressed(idle, stage="demo") is True
        assert nb.pressed(idle, stage="cleanup") is False
        monkeypatch.setattr(nb, "AUTORUN", "cleanup")
        assert nb.pressed(idle) is False
        assert nb.pressed(idle, stage="cleanup") is True

    def test_returns_a_real_bool_and_rejects_unknown_stages(self, nb):
        assert nb.pressed(Btn(1)) is True and nb.pressed(Btn(None)) is False
        with pytest.raises(ValueError):
            nb.pressed(Btn(False), "everything")

    # The plan also requires that orphan delete, reclaim, extraction kick-off and New session
    # read `btn.value` and never call pressed(). Those buttons do not exist yet: each column's
    # test file asserts it once they do.

    def test_every_pressed_call_in_the_notebook_names_a_literal_stage(self, nb_file):
        for node in ast.walk(ast.parse(nb_file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "pressed":
                stages = [k.value for k in node.keywords if k.arg == "stage"] + node.args[1:]
                assert all(
                    isinstance(s, ast.Constant) and s.value in ("demo", "cleanup") for s in stages
                )


# ---- the @app.function rule --------------------------------------------------------------
def _is_app_call(node, attribute):
    target = node.func if isinstance(node, ast.Call) else node
    return (
        isinstance(target, ast.Attribute)
        and target.attr == attribute
        and isinstance(target.value, ast.Name)
        and target.value.id == "app"
    )


def _stored_names(node):
    """Names bound at this level of a statement, without entering nested scopes."""
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
              ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)  # fmt: skip
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            yield (alias.asname or alias.name).split(".")[0]
    elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        yield node.id
    if isinstance(node, scopes):
        return
    for child in ast.iter_child_nodes(node):
        yield from _stored_names(child)


def _loaded_globals(code):
    """(LOAD_NAME at definition time, LOAD_GLOBAL inside bodies) for a compiled def."""
    at_definition = {i.argval for i in dis.get_instructions(code) if i.opname == "LOAD_NAME"}
    in_bodies = set()
    pending = [c for c in code.co_consts if isinstance(c, CodeType)]
    while pending:
        inner = pending.pop()
        in_bodies |= {i.argval for i in dis.get_instructions(inner) if i.opname == "LOAD_GLOBAL"}
        pending += [c for c in inner.co_consts if isinstance(c, CodeType)]
    return at_definition, in_bodies


def app_function_violations(source):
    """Global names that @app.function bodies use but the kernel would not provide.

    An @app.function may only touch: public names from the setup block, other
    @app.function items, and builtins. Cell variables are not module globals, and a
    leading-underscore setup name is private to the setup cell (marimo renames it), so
    both raise NameError when the function runs, and `marimo check` does not notice.
    LOAD_GLOBAL is used rather than co_names, which also lists attribute names.
    """
    tree = ast.parse(source)
    setup, functions = set(), []
    for node in tree.body:
        if isinstance(node, ast.With) and any(
            _is_app_call(item.context_expr, "setup") for item in node.items
        ):
            for statement in node.body:
                setup |= set(_stored_names(statement))
        elif isinstance(node, ast.FunctionDef) and any(
            _is_app_call(d, "function") for d in node.decorator_list
        ):
            functions.append(node)
    allowed = (
        {n for n in setup if not n.startswith("_")}
        | {f.name for f in functions}
        | set(dir(builtins))
    )
    problems = []
    for fn in functions:
        bare = ast.FunctionDef(**{**{f: getattr(fn, f) for f in fn._fields}, "decorator_list": []})
        module = ast.fix_missing_locations(ast.Module(body=[bare], type_ignores=[]))
        at_definition, in_bodies = _loaded_globals(compile(module, "<app.function>", "exec"))
        for name in sorted((at_definition | in_bodies) - allowed):
            problems.append(f"{fn.name} uses {name}")
    return problems


def setup_violations(source):
    """Global names that functions in the setup block use, but nothing in the block defines.

    The column helpers live at the end of the setup block, where a cell variable is not visible
    to them when the module is imported (the tests) though the kernel would provide it, and
    `marimo check` does not notice. LOAD_GLOBAL is used, as above, so attributes do not count.
    """
    setup = nbast.setup_body(source)
    defined = {name for statement in setup for name in _stored_names(statement)}
    module = ast.fix_missing_locations(ast.Module(body=setup, type_ignores=[]))
    at_definition, in_bodies = _loaded_globals(compile(module, "<setup>", "exec"))
    return sorted((in_bodies | at_definition) - defined - set(dir(builtins)))


SYNTHETIC = """
import marimo
app = marimo.App()

with app.setup:
    import os
    from pathlib import Path as P
    PUB = 1
    _PRIV = 2

    def helper():
        return PUB + _PRIV

    class Box:
        pass

{functions}
"""


class TestAppFunctionRule:
    def check(self, functions):
        return app_function_violations(SYNTHETIC.format(functions=functions))

    def test_setup_names_other_functions_and_builtins_are_fine(self):
        assert (
            self.check("""
@app.function(column=1, hide_code=True)
def lc_a(x: int = PUB) -> P:
    total = sum(len(str(i)) for i in range(x)) + helper()
    return P(os.environ.get("X", "")) / str(total) if Box else lc_b(total)

@app.function
def lc_b(n):
    return [lc_a(i) for i in (lambda: range(n))()]
""")
            == []
        )

    def test_a_cell_variable_is_flagged(self):
        assert self.check("""
@app.function(column=1)
def lc_a():
    return lc_threshold * 2
""") == ["lc_a uses lc_threshold"]

    def test_a_private_setup_name_is_flagged(self):
        assert self.check("""
@app.function
def lc_a():
    return _PRIV
""") == ["lc_a uses _PRIV"]

    def test_names_hidden_in_nested_scopes_and_defaults_are_found(self):
        found = self.check("""
@app.function
def lc_a(default=cell_default):
    def inner():
        return [cell_in_comp for _ in range(3)]
    return inner, (lambda: cell_in_lambda)
""")
        assert found == [
            "lc_a uses cell_default",
            "lc_a uses cell_in_comp",
            "lc_a uses cell_in_lambda",
        ]

    def test_attribute_names_are_not_mistaken_for_globals(self):
        assert (
            self.check("""
@app.function
def lc_a(client):
    return client.some_cell_name + client.PUB_lookalike
""")
            == []
        )

    def test_locals_and_parameters_shadowing_are_fine(self):
        assert (
            self.check("""
@app.function
def lc_a(rs_thing):
    rs_other = rs_thing + 1
    for lc_i in range(rs_other):
        rs_other += lc_i
    return rs_other
""")
            == []
        )

    def test_functions_from_a_fragment_that_are_not_app_functions_are_ignored(self):
        assert (
            self.check("""
def plain():
    return undefined_everywhere
""")
            == []
        )

    def test_the_notebook_itself_obeys_the_rule(self, nb_file):
        assert app_function_violations(nb_file.read_text(encoding="utf-8")) == []


class TestSetupHelperRule:
    SOURCE = """
import marimo
app = marimo.App()

with app.setup:
    import os
    PUB = 1
    _PRIV = 2

    def lc_a(x=PUB):
        return lc_b(x) + _PRIV + len(os.sep)

    def lc_b(n):
        return [n for _ in range(PUB)]
{more}
"""

    def check(self, more=""):
        return setup_violations(self.SOURCE.format(more=textwrap.indent(more, "    ")))

    def test_setup_names_private_ones_and_builtins_are_fine(self):
        assert self.check() == []

    def test_a_cell_variable_is_flagged(self):
        assert self.check("""
def lc_c():
    return lc_threshold * 2
""") == ["lc_threshold"]

    def test_a_name_in_a_default_or_a_nested_scope_is_flagged(self):
        found = self.check("""
def lc_c(d=cell_default):
    return lambda: cell_in_lambda
""")
        assert found == ["cell_default", "cell_in_lambda"]

    def test_attributes_and_locals_are_not_names(self):
        assert self.check("""
def lc_c(x):
    y = x.some_cell_name
    return y
""") == []


# ---- choke points are the only way out ------------------------------------------------------
# http_call and connect_redis are opt-in: nothing but a rule stops a cell from building its
# own client, and the first refusal a column meets is a temptation to go round it. So no cell
# or column helper may: this lint reads them all.
_SAFE_TYPES = {"URL", "Headers", "QueryParams", "Request", "Response"}  # they cannot send anything
SAFE_ATTRS = {
    "httpx": _SAFE_TYPES | {n for n in dir(httpx) if isinstance(getattr(httpx, n), type)
                            and issubclass(getattr(httpx, n), BaseException)},
    "redis": {"exceptions"} | {n for n in dir(redis) if isinstance(getattr(redis, n), type)
                               and issubclass(getattr(redis, n), BaseException)},
}  # fmt: skip
NETWORK_MODULES = ("httpx", "redis", "requests", "aiohttp", "urllib3", "socket", "urllib.request",
                   "http.client", "langcache", "redis_agent_memory")  # fmt: skip
SDK_CLIENTS = {"UnifiedClient", "ContextSurfacesClient", "MCPClient"}
SDK_HOME = {"cr_build_models"}  # the one function that may import context_surfaces


def bypass_violations(source):
    """Ways a cell or a column helper could reach a service without http_call or connect_redis."""
    problems = []
    for _, _, _, name, node in [*_cells(source), *_helpers(source)]:
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in ({"HTTP_CLIENT"} | SDK_CLIENTS):
                problems.append(f"{name} uses {child.id}")
            elif isinstance(child, ast.Attribute):
                module = child.value.id if isinstance(child.value, ast.Name) else None
                if module in SAFE_ATTRS and child.attr not in SAFE_ATTRS[module]:
                    problems.append(f"{name} uses {module}.{child.attr}")
                elif child.attr == "from_url" or child.attr in SDK_CLIENTS:
                    problems.append(f"{name} uses .{child.attr}")
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                modules = [child.module or ""] if isinstance(child, ast.ImportFrom) else [
                    alias.name for alias in child.names
                ]
                for module in modules:
                    if any(module == m or module.startswith(m + ".") for m in NETWORK_MODULES):
                        problems.append(f"{name} imports {module}")
                    elif module.split(".")[0] == "context_surfaces" and name not in SDK_HOME:
                        problems.append(f"{name} imports {module}")
    return problems


def lint(body, *, decorator="@app.function(column=1, hide_code=True)", name="lc_thing"):
    """bypass_violations for one cell or function whose body is the given code."""
    source = "import marimo\napp = marimo.App()\n\n"
    source += f"{decorator}\ndef {name}():\n" + textwrap.indent(textwrap.dedent(body), "    ")
    return bypass_violations(source)


class TestNoBypass:
    @pytest.mark.parametrize(
        "code",
        [
            "return HTTP_CLIENT.get('https://x.example')",
            "return httpx.get('https://x.example')",
            "return httpx.Client()",
            "return httpx.AsyncClient()",
            "return httpx.post('https://x.example', json={})",
            "return redis.Redis()",
            "return redis.StrictRedis.from_url(url)",
            "return redis.from_url(url)",
            "return redis.asyncio.Redis()",
            "return GuardedTracedRedis.from_url(url)",
            "import langcache",
            "from langcache import LangCache",
            "from redis_agent_memory import AgentMemory",
            "import requests",
            "import redis.asyncio as aioredis",
            "import httpx as h",
            "import socket",
            "from urllib.request import urlopen",
            "import context_surfaces as cs",
            "from context_surfaces import ContextModel",
            "from context_surfaces import UnifiedClient",
            "import context_surfaces as cs\nreturn cs.UnifiedClient()",
            "return MCPClient('u')",
            "return cs.ContextSurfacesClient()",
        ],
    )
    def test_ways_round_the_choke_points_are_found(self, code):
        assert lint(code), code

    def test_it_reads_cells_as_well_as_functions(self):
        assert lint("return httpx.get('u')", decorator="@app.cell(column=1)", name="_")

    @pytest.mark.parametrize(
        "code",
        [
            "try:\n    pass\nexcept httpx.HTTPError:\n    pass",
            "try:\n    pass\nexcept (redis.ResponseError, redis.exceptions.ConnectionError):\n    pass",
            "return httpx.URL('https://x.example').host",
            "return http_call('lc', 'GET', 'https://x.example')",
            "return connect_redis(url)",
            "return 'import langcache  # this is text, not code'",
        ],
    )
    def test_the_sanctioned_ways_are_not_flagged(self, code):
        assert lint(code) == [], code

    def test_the_sdk_may_be_imported_where_the_models_are_built(self):
        code = "import context_surfaces as cs\nreturn cs.ContextModel"
        assert lint(code, name="cr_build_models") == []
        assert lint("import context_surfaces as cs\nreturn cs.UnifiedClient()", name="cr_build_models")

    def test_the_notebook_goes_through_the_choke_points(self, nb_file):
        assert bypass_violations(nb_file.read_text(encoding="utf-8")) == []


# ---- the Variables panel ---------------------------------------------------------------------
class TestVariablesPanel:
    """`marimo edit` previews every global in its Variables panel; none may show a secret."""

    @staticmethod
    def panel(value):
        """What the panel shows for a global holding value (a UI element shows its .value)."""
        from marimo._messaging.variables import create_variable_value

        return create_variable_value("g", value).value or ""

    def test_no_setup_global_previews_a_secret(self, nb, mock_http, monkeypatch):
        secrets = [ADMIN_KEY, AGENT_KEY, DB_PASSWORD, TRICKY]
        monkeypatch.setenv("LANGCACHE_API_KEY", ADMIN_KEY)
        monkeypatch.setenv("REDIS_URL", f"redis://default:{quote(DB_PASSWORD, safe='')}@h:6379")
        nb.env("LANGCACHE_API_KEY")
        nb.env("REDIS_URL")
        held = [nb.Secret(TRICKY), nb.Secret(AGENT_KEY)]

        mock_http(status=201, json={"id": "ak", "key": AGENT_KEY, "key_type": "agent"})
        mint_key(nb, extra={"connection_config": {"password": DB_PASSWORD}},
                 headers={"X-API-Key": ADMIN_KEY})  # fmt: skip
        with pytest.raises(nb.ServiceError):
            nb.http_call("lc", "POST", f"{LC}/flush")
        assert len(nb.SECRETS) >= 4

        previews = {
            name: self.panel(value)
            for name, value in vars(nb).items()
            if not name.startswith("_") and name not in ("app", "marimo")
        }
        previews |= {"held": self.panel(held), "held-in-dict": self.panel({"k": held[0]})}
        for name, shown in previews.items():
            for secret in secrets:
                assert secret not in shown, f"{name} previews a secret: {shown[:80]}"
        assert "SecretRegistry(" in previews["SECRETS"]

    def test_a_typed_key_previews_masked_in_a_secret_field_and_in_full_in_a_plain_one(self, nb):
        typed = "typed-langcache-key-FAKE-0123456789"
        field = nb.SecretText("LANGCACHE_API_KEY")
        field._update(typed)
        assert typed not in self.panel(field) and self.panel(field) == "••••6789"
        assert field.value.reveal() == typed  # the notebook can still use it
        plain = nb.mo.ui.text(kind="password")
        plain._update(typed)
        assert typed in self.panel(plain), "this is the leak SecretText exists to close"

    def test_a_reply_a_column_keeps_in_a_global_previews_masked(self, nb, mock_http):
        mock_http(status=201, json={"id": "ak", "key": AGENT_KEY, "key_type": "agent"})
        status, cr_key_body = mint_key(nb)
        for name, value in {
            "the body": cr_key_body,
            "the whole result": (status, cr_key_body),
            "a list of them": [cr_key_body],
            "one inside a dict": {"reply": cr_key_body},
        }.items():
            shown = self.panel(value)
            assert AGENT_KEY[:10] not in shown, f"{name} previews the key: {shown[:80]}"
        assert "••••" in self.panel(cr_key_body)

    def test_the_registry_counts_its_secrets_but_cannot_be_listed(self, nb):
        nb.SECRETS.add("value-one-1234")
        assert len(nb.SECRETS) == 1
        assert "value-one-1234" not in repr(nb.SECRETS) and "value-one-1234" not in str(nb.SECRETS)
        with pytest.raises(TypeError):
            list(nb.SECRETS)  # this is the call that would print every credential
        nb.SECRETS.clear()
        assert len(nb.SECRETS) == 0


# ---- UI helpers ---------------------------------------------------------------------------
class TestUiHelpers:
    def test_notes_are_always_in_the_page_marked_for_the_switch_to_show(self, nb):
        shown = nb.notes("say this", "see that").text
        assert 'class="ib-note"' in shown and "say this" in shown and "see that" in shown
        with pytest.raises(TypeError):
            nb.notes("say this", "see that", show=True)  # the switch never reaches a note

    def test_not_configured_names_the_missing_settings(self, nb):
        text = nb.not_configured("LangCache", ["LANGCACHE_HOST", "LANGCACHE_API_KEY"]).text
        assert (
            "LangCache is not configured" in text
            and "LANGCACHE_HOST" in text
            and ".env.example" in text
        )

    def test_not_configured_says_what_to_do_in_the_mode_it_is_shown_in(self, nb, monkeypatch):
        monkeypatch.setattr(nb, "status_callout", lambda kind, message: message)  # the markdown

        def shown(mode, **kwargs):
            monkeypatch.setattr(nb.mo, "app_meta", lambda: SimpleNamespace(mode=mode))
            return " ".join(nb.not_configured("LangCache", ["LANGCACHE_HOST"], **kwargs).split())

        edit, run = shown("edit"), shown("run")
        assert "Fill in the fields above" in edit and "then re-run this cell" in edit
        assert "Fill in the fields above" in run and "then restart the notebook" in run
        assert "re-run" not in run  # `marimo run` has no cell to re-run
        # Nothing works without credentials unless the column says so: no false comfort.
        assert "still works" not in edit and "works without" not in edit
        offline = shown("run", offline="The explorer below works without them.")
        assert offline.endswith("The explorer below works without them.")

    def test_service_failed_gives_a_columns_advice_then_the_shared_advice_then_the_error(
        self, nb
    ):
        def failed(status, hints=None, detail="the detail"):
            error = nb.ServiceError("lc", status, "http", detail)
            return nb.service_failed(error, hints).text

        own = failed(401, {401: "Paste the key again."})
        assert "Paste the key again." in own and "the detail" in own
        assert "lc HTTP" not in own  # the advice replaces the component code
        assert "Too many requests" in failed(429, {401: "x"})  # shared advice still applies
        assert "This key may not do that" in failed(403)
        assert "Failed Dependency" in failed(424) and "the detail" in failed(424)
        assert "Paste" not in failed(429, {401: "Paste the key again."})
        plain = failed(500)  # no advice for it: the error itself
        assert "ServiceError" in plain and "lc HTTP 500 http: the detail" in plain
        assert "ServiceError" in nb.service_failed(nb.ServiceError("lc", None, "timeout", "no reply")).text
        assert "RuntimeError: boom" in nb.service_failed(RuntimeError("boom")).text

    def test_service_failed_scrubs_what_it_shows(self, nb):
        nb.register_secret(ADMIN_KEY)
        error = nb.ServiceError("cr", 401, "http", f"bad key {ADMIN_KEY}")
        assert ADMIN_KEY not in nb.service_failed(error, {401: "Refused."}).text

    def test_code_block_cannot_be_closed_from_inside(self, nb):
        block = nb.code_block("a ``` b", "json")
        assert block.startswith("```json\n") and block.endswith("\n```")
        assert "```" not in block[len("```json\n") : -len("\n```")]

    def test_md_table_wraps_text_and_sets_the_named_columns_in_code_type(self, nb, monkeypatch):
        rows = [{"tool": "get_order", "limited to": "placed, shipped | returned"}, {"tool": "", "limited to": "`x`"}]
        monkeypatch.setattr(nb.mo, "md", lambda text: text)  # the markdown, not its html
        lines = nb.md_table(rows, code=("tool",))
        assert "| tool | limited to |" in lines and "|:---|:---|" in lines
        assert "| `get_order` | placed, shipped / returned |" in lines  # a pipe would split a cell
        assert "|  | 'x' |" in lines  # an empty cell stays empty; a backtick cannot open code

    def test_a_shared_database_is_warned_about_only_when_the_facts_say_so(self, nb, monkeypatch):
        monkeypatch.setattr(nb, "status_callout", lambda kind, message: message)  # the markdown
        quiet = {"shared": False, "dbsize": 5, "notebook_keys": ["irisnb_a_x"] * 5, "indexes": []}
        assert nb.shared_db_warning(quiet, "Use a scratch one.").text == nb.mo.md("").text
        busy = {
            "shared": True, "dbsize": 9, "notebook_keys": ["irisnb_a_x"] * 4,
            "indexes": ["irisnb_a_idx", "memory:store:ltm"],
        }  # fmt: skip
        text = nb.shared_db_warning(busy, "Use a scratch one.")
        assert "`default` user" in text and "**5** keys" in text and "**1** indexes" in text
        assert "Use a scratch one." in text

    def test_age_hours_reads_rfc_3339_and_is_none_for_anything_else(self, nb):
        now = datetime.now(timezone.utc)
        assert 2.9 < nb.age_hours((now - timedelta(hours=3)).isoformat()) < 3.1
        assert 4.9 < nb.age_hours((now - timedelta(hours=5)).replace(tzinfo=None).isoformat()) < 5.1
        assert nb.age_hours("yesterday") is None and nb.age_hours(None) is None

    def test_is_stale_means_older_than_the_orphan_age(self, nb):
        now = datetime.now(timezone.utc)
        assert nb.is_stale((now - timedelta(hours=nb.ORPHAN_AGE_H + 1)).isoformat())
        assert not nb.is_stale((now - timedelta(hours=nb.ORPHAN_AGE_H - 1)).isoformat())
        assert not nb.is_stale("yesterday") and not nb.is_stale(None)

    def test_status_callout_kinds_and_scrubbing(self, nb):
        nb.register_secret(ADMIN_KEY)  # plain, so it would show verbatim if not scrubbed
        for kind in ("ok", "info", "warn", "error"):
            assert "hello" in nb.status_callout(kind, "hello").text
        shown = nb.status_callout("error", f"failed with {ADMIN_KEY}").text
        assert "failed with ****" in shown and ADMIN_KEY not in shown
        shown = nb.status_callout("error", RuntimeError(f"boom {ADMIN_KEY}")).text
        assert "RuntimeError: boom ****" in shown and ADMIN_KEY not in shown
        with pytest.raises(KeyError):
            nb.status_callout("fatal", "x")

    def test_a_plain_table_has_no_chart_band_however_many_rows_it_has(self, nb):
        # marimo turns the band on by itself from 11 rows, which is what this helper undoes.
        rows = [{"n": i} for i in range(40)]
        assert nb.mo.ui.table(rows)._show_column_summaries is True
        assert nb.plain_table(rows)._show_column_summaries is False

    def test_the_command_trace_draws_its_table_with_plain_table(self, nb, monkeypatch):
        drawn = []
        monkeypatch.setattr(nb, "plain_table", lambda rows: drawn.append(rows) or nb.mo.md("t"))
        nb.REDIS_TRACE.append(nb.RedisCall("PING", 0.1, "True", "ok"))
        nb.render_redis_trace()
        assert len(drawn) == 1

    def test_the_notebook_makes_no_table_but_through_plain_table(self, nb_file):
        made_in = []

        def walk(node, function):
            function = node.name if isinstance(node, ast.FunctionDef) else function
            if isinstance(node, ast.Call) and nbast.call_name(node) == "mo.ui.table":
                made_in.append(function)
            for child in ast.iter_child_nodes(node):
                walk(child, function)

        walk(ast.parse(nb_file.read_text(encoding="utf-8")), None)
        assert made_in == ["plain_table"]

    def test_recorded_live_is_empty_for_a_component_with_no_capture(self, nb, monkeypatch):
        assert "Recorded live" not in nb.recorded_live("rs").text  # column 0 needs no service
        assert "Recorded live" not in nb.recorded_live("nobody").text
        monkeypatch.setitem(
            nb.RECORDED_LIVE, "lc", {"date": "2026-10-01", "markdown": "real output"}
        )
        panel = nb.recorded_live("lc").text
        assert "Recorded live on 2026-10-01" in panel and "real output" in panel

    @pytest.mark.parametrize("component", ["lc", "cr", "am"])
    def test_each_hosted_column_has_a_panel_labelled_as_recorded_and_not_simulated(
        self, nb, component
    ):
        capture = nb.RECORDED_LIVE[component]
        assert capture["date"] == "2026-09-30"
        panel = html.unescape(nb.recorded_live(component).text)
        assert "Recorded live on 2026-09-30" in panel and "not a simulation" in panel
        assert len(capture["markdown"]) < 3_000  # compact: a few representative outputs

    def test_the_recorded_panels_hold_no_identifier_of_the_account_they_were_captured_on(self, nb):
        """A cache, store or surface id, an entry or memory id, a host: all placeholders."""
        hex32 = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", re.I)
        uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
        host = re.compile(r"(?:[\w-]+\.)*redis\.io|\.db\.|[a-z][\w.-]*:\d{4,5}(?!\d)", re.I)
        run_id = re.compile(r"(?:irisnb_|ib-|iris-basics-)[0-9a-f]{8}(?![0-9a-f])", re.I)
        # the patterns catch what they should, or a pass means nothing
        assert hex32.search("id ab12cd34ef56ab12cd34ef56ab12cd34 x")
        assert uuid.search("id 0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0")
        assert host.search("https://example-region.langcache.redis.io/v1") and host.search("redis.io")
        assert host.search("h.db.redis.io:12345") and host.search("host:12345")
        assert run_id.search("irisnb_0badc0de_rs:idx") and run_id.search("ib-0badc0de-m1")
        assert not hex32.search("<cache-id> <entry-id> ib-<run> irisnb_<run>_")
        for component, capture in nb.RECORDED_LIVE.items():
            for name, pattern in (("hex id", hex32), ("uuid", uuid), ("host", host),
                                  ("run id", run_id)):  # fmt: skip
                found = pattern.search(capture["markdown"] + capture["date"])
                assert not found, f"{component}: a {name} in the recorded panel: {found[0]}"

    @pytest.mark.parametrize("component", ["lc", "cr", "am"])
    def test_the_recorded_panels_name_what_they_replaced_with_a_placeholder(self, nb, component):
        markdown = nb.RECORDED_LIVE[component]["markdown"]
        placeholders = set(re.findall(r"<[a-z][a-z-]*>", markdown))
        assert placeholders, component
        assert placeholders <= {"<host>", "<mcp-host>", "<cache-id>", "<entry-id>", "<surface-id>",
                                "<store-id>", "<run>", "<hash>", "<entity>", "<anonymous>"}  # fmt: skip

    def test_the_recorded_panels_use_this_notebooks_own_cairn_data_and_nothing_else(self, nb):
        lc = nb.RECORDED_LIVE["lc"]["markdown"]
        questions = [p["text"] for p in nb.lc_probes()] + [s["prompt"] for s in nb.lc_seeds().values()]
        table = [line.split(" | ")[0].lstrip("| ") for line in lc.splitlines() if line.startswith("| ")]
        assert set(table[2:]) <= set(questions), set(table[2:]) - set(questions)
        assert nb.lc_script()["ask"] in lc and nb.lc_script()["again"] not in lc  # the taught pair
        cr = nb.RECORDED_LIVE["cr"]["markdown"]
        customer = [r["tool"] for r in nb.cr_predict_tools(nb.cr_build_models(nb.CR_PREFIX)["data_model"])
                    if r["tool"].endswith(("customer", "customer_by_id", "customer_by_text", "_results"))]
        assert customer and all(tool in cr for tool in customer), customer
        assert "Ada Lovelace" in cr and "C001" in cr
        am = nb.RECORDED_LIVE["am"]["markdown"]
        assert nb.am_wow_presets()[0]["query"] in am
        assert all(memory["text"] in am for memory in nb.am_memories())

    def test_with_spinner_runs_the_function(self, nb):
        assert nb.with_spinner("LangCache", lambda: 41 + 1) == 42
        with pytest.raises(ZeroDivisionError):
            nb.with_spinner("LangCache", lambda: 1 / 0)


# The intro and title cells only exist in the assembled notebook, not in column fragments.
assembled_only = pytest.mark.skipif(
    os.environ.get("IRIS_NB_MODULE", "iris_basics") != "iris_basics",
    reason="intro and titles live in the assembled notebook",
)


# ---- notebook structure (the plan's layout contract) ----------------------------------------
def _helpers(source):
    """The column helpers, in the shape _cells uses: they are functions in the setup block."""
    return [("function", h.column, h.hide_code, h.node.name, h.node) for h in nbast.helpers(source)]


def _cells(source):
    """(kind, column, hide_code, name, node) for every @app.cell / @app.function."""
    out = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            for kind in ("cell", "function"):
                if _is_app_call(dec, kind):
                    kwargs = {
                        k.arg: k.value.value
                        for k in getattr(dec, "keywords", [])
                        if isinstance(k.value, ast.Constant)
                    }
                    out.append(
                        (kind, kwargs.get("column"), kwargs.get("hide_code"), node.name, node)
                    )
    return out


class TestNotebookStructure:
    def test_header_pins_the_stack(self, nb_file):
        source = nb_file.read_text(encoding="utf-8")
        # PEP 723: the header is TOML behind a "# " on every line.
        header = source.split("# ///", 2)[1].strip().splitlines()[1:]  # after "script"
        meta = tomllib.loads("\n".join(line.removeprefix("#").removeprefix(" ") for line in header))
        assert meta["requires-python"] == ">=3.11,<3.14"
        # The output above the code, so that in edit mode the audience sees the notebook first.
        # marimo lets a script's header set this one (not runtime settings such as auto-run).
        assert meta["tool"]["marimo"]["display"] == {"cell_output": "above"}
        deps = meta["dependencies"]
        # Every dependency is pinned exactly, to the set the research validated. A range would
        # let a fresh install drift onto versions nobody has run (plan section "Stack").
        assert all("==" in d for d in deps), deps
        assert dict(d.split("==") for d in deps) == {
            "marimo": "0.25.0",
            "redis": "8.1.0",
            "numpy": "2.4.6",
            "model2vec": "0.9.0",
            "huggingface-hub": "1.33.0",
            "httpx": "0.28.1",
            "redis-context-retriever": "0.0.6",
        }
        assert 'App(width="columns", app_title="Redis Iris basics")' in source
        assert '__generated_with = "0.25.0"' in source

    def test_every_item_has_an_explicit_column_and_columns_are_contiguous(self, nb_file):
        items = _cells(nb_file.read_text(encoding="utf-8"))
        assert items, "no cells found"
        columns = [column for _, column, _, _, _ in items]
        assert all(isinstance(c, int) for c in columns), (
            "every @app.cell / @app.function needs column=N"
        )
        assert columns == sorted(columns), "a column's items must be contiguous and in order"
        assert set(columns) == set(range(max(columns) + 1)), "column indices must have no gaps"

    def test_app_functions_are_hidden(self, nb_file):
        for kind, _, hide, name, _ in _cells(nb_file.read_text(encoding="utf-8")):
            if kind == "function":
                assert hide is True, f"{name} must be hide_code=True"

    def test_the_column_helpers_are_in_the_setup_block_and_the_item_count_stays_small(self, nb_file):
        # marimo edit makes a cell of every @app.cell and @app.function. With 194 hidden helpers
        # as items the notebook had 300, and Run all took 40 s and a control change 20 s; with the
        # helpers in the setup block it has about 120. Keep it that way.
        source = nb_file.read_text(encoding="utf-8")
        assert [n for kind, _, _, n, _ in _cells(source) if kind == "function"] == []
        helpers = nbast.helpers(source)
        assert {h.column for h in helpers} == {0, 1, 2, 3} and len(helpers) > 150
        assert len(_cells(source)) < 140, "every item is a cell in marimo edit"

    def test_the_setup_block_defines_every_name_its_functions_use(self, nb_file):
        assert setup_violations(nb_file.read_text(encoding="utf-8")) == []

    def test_globals_carry_the_column_prefixes(self, nb_file):
        allowed = ("ib_", "rs_", "lc_", "cr_", "am_")
        for kind, _, _, name, node in _cells(nb_file.read_text(encoding="utf-8")):
            if kind == "function":
                assert name.startswith(allowed) or name == "cr_build_models", name
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.Return) and stmt.value is not None:
                    names = [
                        e.id
                        for e in (
                            stmt.value.elts if isinstance(stmt.value, ast.Tuple) else [stmt.value]
                        )
                        if isinstance(e, ast.Name)
                    ]
                    assert all(n.startswith(allowed) for n in names), (
                        f"{names} need a column prefix"
                    )

    @assembled_only
    def test_four_title_cells_share_one_shape(self, nb_file):
        items = _cells(nb_file.read_text(encoding="utf-8"))
        expected = {
            0: "# 0 · Redis Search",
            1: "# 1 · LangCache",
            2: "# 2 · Context Retriever",
            3: "# 3 · Agent Memory",
        }
        cells = [(column, hide, node) for kind, column, hide, _, node in items if kind == "cell"]
        source = nb_file.read_text(encoding="utf-8")
        # Column 0 opens with the intro (and the cell that shows the presenter notes), and the
        # others with their title: it is the cell with the column's heading in it.
        intro = cells[0]
        titles = {
            column: next(
                cell for cell in cells
                if cell[0] == column and expected[column] in ast.get_source_segment(source, cell[2])
            )
            for column in expected
        }  # fmt: skip
        assert set(titles) == set(expected)
        # Only the intro and the titles hide their code. Every other cell is the demo, and
        # edit mode is the presenting mode, so it is not this test's business to hide it.
        assert intro[1] is True, "the intro should be hide_code=True"
        for column, (_, hide, node) in titles.items():
            assert hide is True, f"the title in column {column} should be hide_code=True"
            text = ast.get_source_segment(source, node)
            assert expected[column] in text, column
            assert 'mo.md(r"""' in text and "mo.mermaid(" in text, column
            assert "**Docs:**" in text and "**Create one:**" in text, column
            assert re.search(r"https://redis\.io/docs/latest/\S+", text)
            prose = re.search(r"# \d · [^\n]+\n\n(.+?)\n\n\s*\*\*Docs", text, re.S).group(1)
            sentences = [s for s in re.split(r"(?<=[.!?])\s+", " ".join(prose.split())) if s]
            assert 3 <= len(sentences) <= 6, (column, len(sentences))


# ---- files that ship next to the notebook ----------------------------------------------------
class TestShippedFiles:
    @pytest.fixture
    def root(self, nb_file):
        return nb_file.parent

    def test_env_example_lists_every_setting_commented_out(self, nb, root):
        path = root / ".env.example"
        if not path.exists():
            pytest.skip("no .env.example next to this module")
        lines = path.read_text(encoding="utf-8").splitlines()
        assert nb.parse_env("\n".join(lines)) == {}, "everything must be commented out"
        for line in lines:
            assert not line.strip() or line.lstrip().startswith("#"), (
                f"comments must be on their own line: {line!r}"
            )
        named = set(re.findall(r"^#\s*([A-Z][A-Z_]+)=", "\n".join(lines), re.M))
        assert named == nb.ENV_ALLOWLIST

    def test_env_example_never_mentions_the_never_read_names_as_settings(self, root):
        path = root / ".env.example"
        if not path.exists():
            pytest.skip("no .env.example next to this module")
        text = path.read_text(encoding="utf-8")
        for name in (
            "CTX_SURFACE_ID",
            "MCP_AGENT_KEY",
            "MEMORY_OWNER_ID",
            "MEMORY_NAMESPACE",
            "OPENAI_API_KEY",
        ):
            assert not re.search(rf"^#?\s*{name}=", text, re.M), name

    def test_gitignore_keeps_secrets_and_session_caches_out(self, root):
        path = root / ".gitignore"
        if not path.exists():
            pytest.skip("no .gitignore next to this module")
        entries = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
        assert {".env", "__marimo__/", "__pycache__/", "*.html", ".agents/build/"} <= entries


@assembled_only
class TestIntroCell:
    """Run the whole notebook headless (no server, no network) and read what it shows."""

    def render(self, load_fresh, **env):
        fresh = load_fresh(**env)
        outputs, defs = fresh.app.run()
        return " ".join(getattr(o, "text", str(o)) for o in outputs), defs

    def test_quiet_by_default(self, load_fresh):
        text, defs = self.render(load_fresh)
        assert "AUTORUN is on" not in text
        assert defs["ib_notes"].value is False
        assert "Redis Iris basics" in text

    @pytest.mark.parametrize("stage", ["demo", "cleanup"])
    def test_red_banner_when_autorun_is_on(self, load_fresh, stage):
        text, _ = self.render(load_fresh, IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN=stage)
        assert "AUTORUN is on" in text and "danger" in text

    def test_ignored_autorun_shows_no_banner(self, load_fresh):
        text, _ = self.render(load_fresh, IRIS_BASICS_AUTORUN="demo")
        assert "AUTORUN is on" not in text

    def test_settings_detected_lists_names_and_never_values(self, load_fresh):
        text, _ = self.render(
            load_fresh, LANGCACHE_API_KEY="fake-key-abcdef-123456", REDIS_HOST="fake-host.invalid"
        )
        assert "LANGCACHE_API_KEY" in text and "REDIS_HOST" in text
        assert "fake-key-abcdef-123456" not in text and "fake-host.invalid" not in text

    def test_the_width_override_is_for_run_mode_only(self, load_fresh):
        text, _ = self.render(load_fresh)  # headless mode is not "run"
        assert "--content-width" not in text

    def test_every_anchor_in_the_intro_has_a_heading_to_land_on(self, load_fresh):
        text, _ = self.render(load_fresh)
        targets = set(re.findall(r'href=\\?"#([^"\\]+)', text))
        ids = set(re.findall(r'id=\\?"([^"\\]+)', text))
        assert targets == {"0-redis-search", "1-langcache", "2-context-retriever", "3-agent-memory"}
        assert targets <= ids

    def test_each_title_has_a_mermaid_diagram(self, load_fresh):
        text, _ = self.render(load_fresh)
        assert text.count("marimo-mermaid") >= 4

    def test_the_diagrams_lie_flat_in_edit_mode_and_stand_in_run_mode(
        self, load_fresh, monkeypatch
    ):
        # Edit mode caps an output at 610 px, which cut the lower half off a tall diagram; run
        # mode is 560 px wide, which a wide one would overflow.
        text, _ = self.render(load_fresh)
        assert text.count("flowchart LR") == 4 and "flowchart TB" not in text
        fresh = load_fresh()
        monkeypatch.setattr(fresh.mo, "app_meta", lambda: SimpleNamespace(mode="run"))
        outputs, _ = fresh.app.run()
        text = " ".join(getattr(o, "text", str(o)) for o in outputs)
        assert text.count("flowchart TB") == 4 and "flowchart LR" not in text

    @pytest.mark.parametrize("mode", ["edit", "run"])
    def test_marimos_floating_outline_is_hidden_in_both_modes(self, load_fresh, monkeypatch, mode):
        # It is a column of ticks at the right edge that covers text wherever a column reaches it.
        fresh = load_fresh()
        monkeypatch.setattr(fresh.mo, "app_meta", lambda: SimpleNamespace(mode=mode))
        outputs, _ = fresh.app.run()
        text = " ".join(getattr(o, "text", str(o)) for o in outputs)
        assert "div.fixed.right-8.z-10000 { display: none !important; }" in text

    def test_the_launch_notes_say_what_the_first_minute_of_presenting_needs(self, load_fresh):
        text, _ = self.render(load_fresh)
        words = " ".join(re.sub(r"<[^>]+>", "", text).split())  # the markup and the line breaks out
        for needed in ("Ctrl+Shift+R", "Run all stale cells", "67-75%", "the .env wins"):
            assert needed in words, needed


@assembled_only
class TestPresenterSwitch:
    """The Presenter notes switch, flipped in a real kernel with no settings at all.

    Flipping re-runs the cells that read the switch and not the cells above them. When a cell
    that showed a result read it, a result that a stopped cell never made was a NameError: 14
    red cells in edit mode and an "internal error" toast in run mode.
    """

    def test_flipping_it_on_and_off_raises_nothing(self, load_fresh):
        async def scenario(book):
            await book.set("ib_notes", True)
            assert "display: block" in book.g("ib_notes_style").text
            await book.set("ib_notes", False)
            assert "display: none" in book.g("ib_notes_style").text

        sink = drive(load_fresh(), scenario)
        assert sink.exceptions() == []


@assembled_only
class TestExportedBanner:
    """Run the real `marimo export html`, the way verify.py drives the notebook.

    marimo reports mode "edit" for an export, exactly as it does for `marimo edit`, so
    faking mo.app_meta() (as the tests above do) cannot show whether AUTORUN survives a
    headless export. Only a real one can. It is read back from the session JSON that
    marimo writes beside the notebook, which holds every cell's rendered output.
    """

    def export(self, nb_file, tmp_path, **env):
        work = tmp_path / "exported"
        work.mkdir()
        shutil.copy2(nb_file, work / nb_file.name)
        # conftest has already emptied the environment of settings; this child is not a test.
        child = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        child.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", **env)
        done = subprocess.run(
            [sys.executable, "-m", "marimo", "-y", "export", "html", "--no-sandbox",
             nb_file.name, "-o", "out.html", "-f"],
            cwd=work, env=child, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        session = work / "__marimo__" / "session" / f"{nb_file.name}.json"
        return session.read_text(encoding="utf-8")

    def test_a_headless_export_autoruns_and_says_so(self, nb_file, tmp_path):
        session = self.export(
            nb_file, tmp_path,
            IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN="demo", IRIS_BASICS_HEADLESS="1",
        )  # fmt: skip
        assert "AUTORUN is on" in session

    def test_an_export_that_does_not_say_headless_is_treated_as_marimo_edit(
        self, nb_file, tmp_path
    ):
        session = self.export(
            nb_file, tmp_path, IRIS_BASICS_RUN="0123abcd", IRIS_BASICS_AUTORUN="demo"
        )
        assert "Redis Iris basics" in session, "the export should have rendered the intro"
        assert "AUTORUN is on" not in session


# ---- reply helpers ---------------------------------------------------------------------------
class TestReplyHelpers:
    """decode_reply and reply_map, which every column that reads Redis parses replies with."""

    def test_decode_reply_decodes_bytes_at_any_depth(self, nb):
        reply = [b"a", (b"b", [b"c"]), {b"k": b"v"}, 1, 2.5, None, "already text"]
        decoded = nb.decode_reply(reply)
        assert decoded == ["a", ["b", ["c"]], {"k": "v"}, 1, 2.5, None, "already text"]

    def test_decode_reply_never_raises_on_binary(self, nb):
        assert nb.decode_reply(b"\xff\xfe") == "��"  # a vector blob is not an error

    def test_reply_map_reads_a_flat_resp2_list_or_a_mapping(self, nb):
        assert nb.reply_map(["k", 1, "j", [2]]) == {"k": 1, "j": [2]}
        assert nb.reply_map({"k": 1}) == {"k": 1}
        assert nb.reply_map([]) == {}

    @pytest.mark.parametrize(
        "odd",
        [
            ["k"], ["k", 1, "j"], "text", b"bytes", None, 7,
            # An even length, but not a map: a count followed by rows, or names that are not text.
            [3, ["k", 1], ["k", 2], ["k", 3]], [b"a", [1], [2], [3]], [["k"], 1], [1, 2], [None, 1],
        ],
    )  # fmt: skip
    def test_reply_map_gives_an_empty_dict_for_anything_else(self, nb, odd):
        assert nb.reply_map(odd) == {}

    def test_reply_map_keeps_byte_names_so_a_raw_reply_can_be_read_before_decoding(self, nb):
        assert nb.reply_map([b"k", 1, b"j", [2]]) == {b"k": 1, b"j": [2]}


# ---- the cell recipe every column follows (plan sections 3.2 and 3.6) -------------------------
# These read a notebook's source, so they cover the assembled file and every column fragment,
# and each later column's cells are held to the same rules as column 0's.
INERT_CALLS = frozenset(
    {"SecretText", "secret_hint", "status_callout", "not_configured", "notes", "wire_view"}
    | {"recorded_live", "env", "env_present"}
)  # setup helpers that only build a message or read a setting: none of them can fail or stop
NEVER_AUTORUN = ("orphan", "reset", "reclaim", "extract", "new_session")


def is_cleanup(node):
    """True for the cell that presses `pressed(button, stage="cleanup")`."""
    stages = (nbast.keyword(c, "stage") for c in nbast.calls(node, "pressed"))
    return any(s is not None and ast.unparse(s) == "'cleanup'" for s in stages)


def why_not_inert(node):
    """What a cell does that could stop, fail or gate it: any call but UI and message ones."""
    reasons = []
    for child in (n for statement in node.body for n in ast.walk(statement)):  # not the decorator
        if isinstance(child, ast.Raise):
            reasons.append("raises")
        elif isinstance(child, ast.Call):
            name = nbast.call_name(child)
            if name == "mo.stop":
                reasons.append("calls mo.stop")
            elif not (name.startswith("mo.") or name in INERT_CALLS or name in dir(builtins)):
                reasons.append(f"calls {name or 'something'}")
    return reasons


def cleanup_problems(source):
    """Ways another cell could keep a cleanup cell from running, one message each.

    marimo cancels a cell whose ancestor stopped or raised, so cleanup may depend only on
    cells that can do neither: the config controls and its own buttons. Anything else it
    needs (a client) it builds itself when the button is pressed.
    """
    cells = [i for i in nbast.items(source) if i.kind == "cell"]
    owner = {name: cell for cell in cells for name in nbast.defs(cell.node)}
    problems = []
    for cleanup in (c for c in cells if is_cleanup(c.node)):
        seen, todo = set(), list(nbast.refs(cleanup.node))
        while todo:
            cell = owner.get(todo.pop())
            if cell is None or cell.node.lineno in seen:
                continue
            seen.add(cell.node.lineno)
            for why in sorted(set(why_not_inert(cell.node))):
                problems.append(
                    f"the cleanup cell at line {cleanup.node.lineno} depends on the cell at "
                    f"line {cell.node.lineno}, which {why}"
                )
            todo += nbast.refs(cell.node)
    return problems


def columns_without_cleanup(source):
    """Columns with a demo button (pressed(...)) but no cleanup cell."""
    cells = [i for i in nbast.items(source) if i.kind == "cell"]
    demo = {c.column for c in cells if nbast.calls(c.node, "pressed") and not is_cleanup(c.node)}
    return sorted(demo - {c.column for c in cells if is_cleanup(c.node)})


def empty_text_boxes(source):
    """Text boxes that start empty, which AUTORUN could not ask anything. `*_confirm` may."""
    found = []
    for item in nbast.items(source):
        for node in ast.walk(item.node):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
                continue
            if nbast.call_name(node.value) not in ("mo.ui.text", "mo.ui.text_area"):
                continue
            value = nbast.keyword(node.value, "value")
            literal = isinstance(value, ast.Constant)
            blank = value is None or (literal and not str(value.value).strip())
            if blank and not node.targets[0].id.endswith("_confirm"):
                found.append(node.targets[0].id)
    return found


def autorunning_buttons_that_must_not(source):
    """Buttons named like an orphan delete or a reset, which are passed to pressed()."""
    tree = ast.parse(source)
    buttons = {
        t.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Call)
        and nbast.call_name(n.value) == "mo.ui.run_button"
        for t in n.targets
    }
    pressed = {
        c.args[0].id
        for c in ast.walk(tree)
        if isinstance(c, ast.Call)
        and nbast.call_name(c) == "pressed"
        and isinstance(c.args[0], ast.Name)
    }
    return sorted(b for b in buttons & pressed if any(word in b for word in NEVER_AUTORUN))


def cells_source(*cells):
    """A notebook of the given cells (each a `def _(...)` text), all in column 1."""
    cells = ["@app.cell(column=1)\n" + textwrap.dedent(c).strip("\n") for c in cells]
    return "import marimo\napp = marimo.App()\n\n\n" + "\n\n\n".join(cells)


CONFIG = """
    def _():
        lc_key = SecretText("LANGCACHE_API_KEY")
        lc_go = mo.ui.run_button()
        mo.vstack([lc_key, lc_go])
        return lc_go, lc_key
"""
BUTTONS = """
    def _():
        lc_clean = mo.ui.run_button()
        lc_clean
        return (lc_clean,)
"""
CLEANUP = """
    def _(lc_clean, lc_key):
        mo.stop(not pressed(lc_clean, stage="cleanup"), None)
        http_call("lc", "DELETE", lc_key.value.reveal())
"""
GATED = """
    def _(lc_go, lc_key):
        mo.stop(not pressed(lc_go), None)
        lc_client = http_call("lc", "GET", lc_key.value.reveal())
        return (lc_client,)
"""


class TestCleanupIndependence:
    def test_cleanup_that_needs_only_config_and_its_own_buttons_is_fine(self):
        source = cells_source(CONFIG, BUTTONS, GATED, CLEANUP)
        assert cleanup_problems(source) == [] and columns_without_cleanup(source) == []

    def test_a_cleanup_that_reads_a_gated_cell_is_flagged(self):
        needs_client = """
            def _(lc_clean, lc_client):
                mo.stop(not pressed(lc_clean, stage="cleanup"), None)
                lc_client.delete()
        """
        problems = cleanup_problems(cells_source(CONFIG, BUTTONS, GATED, needs_client))
        assert any("calls mo.stop" in p for p in problems), problems
        assert any("calls http_call" in p for p in problems), problems

    def test_the_gate_can_be_further_up_the_chain(self):
        controls = """
            def _(lc_client):
                lc_threshold = mo.ui.slider(1, 10, value=lc_client.top)
                return (lc_threshold,)
        """
        cleanup = """
            def _(lc_clean, lc_threshold):
                mo.stop(not pressed(lc_clean, stage="cleanup"), None)
        """
        source = cells_source(CONFIG, BUTTONS, GATED, controls, cleanup)
        assert any("calls mo.stop" in p for p in cleanup_problems(source))

    def test_a_cell_that_raises_or_connects_is_not_inert(self):
        raises = """
            def _():
                lc_url = env("LANGCACHE_HOST")
                if not lc_url:
                    raise ValueError("no host")
                return (lc_url,)
        """
        connects = """
            def _(lc_url):
                lc_db = connect_redis(lc_url)
                return (lc_db,)
        """
        cleanup = """
            def _(lc_clean, lc_db):
                mo.stop(not pressed(lc_clean, stage="cleanup"), None)
        """
        problems = cleanup_problems(cells_source(BUTTONS, raises, connects, cleanup))
        assert any("raises" in p for p in problems)
        assert any("calls connect_redis" in p for p in problems)

    def test_a_column_with_a_demo_button_and_no_cleanup_is_reported(self):
        assert columns_without_cleanup(cells_source(CONFIG, GATED)) == [1]

    def test_the_notebook_cleanup_cells_depend_on_nothing_that_can_stop_or_fail(self, nb_file):
        source = nb_file.read_text(encoding="utf-8")
        assert cleanup_problems(source) == [] and columns_without_cleanup(source) == []

    def test_the_notebook_has_a_cleanup_cell_where_it_has_a_demo(self, nb_file):
        cells = [i for i in nbast.items(nb_file.read_text(encoding="utf-8")) if i.kind == "cell"]
        pressed_in = {c.column for c in cells if nbast.calls(c.node, "pressed")}
        assert {c.column for c in cells if is_cleanup(c.node)} == pressed_in


class TestCellRecipe:
    def test_a_text_box_that_starts_empty_is_found(self):
        source = cells_source(
            """
            def _():
                lc_a = mo.ui.text()
                lc_b = mo.ui.text(value="")
                lc_c = mo.ui.text_area(value="  ")
                lc_d = mo.ui.text(value="a question")
                lc_e = mo.ui.text(placeholder="type it")
                lc_f = SecretText("LANGCACHE_API_KEY")
                return lc_a, lc_b, lc_c, lc_d, lc_e, lc_f
            """
        )
        assert empty_text_boxes(source) == ["lc_a", "lc_b", "lc_c", "lc_e"]

    def test_a_confirmation_box_is_the_one_text_box_that_may_start_empty(self):
        source = cells_source(
            """
            def _():
                lc_orphan_confirm = mo.ui.text(placeholder="type it")
                return (lc_orphan_confirm,)
            """
        )
        assert empty_text_boxes(source) == []

    def test_every_text_box_in_the_notebook_has_a_preset(self, nb_file):
        assert empty_text_boxes(nb_file.read_text(encoding="utf-8")) == []

    def test_buttons_that_must_not_autorun_are_found_if_passed_to_pressed(self):
        bad = cells_source(
            """
            def _():
                lc_orphan_delete = mo.ui.run_button()
                lc_reset_all = mo.ui.run_button()
                lc_send = mo.ui.run_button()
                go = pressed(lc_orphan_delete) or pressed(lc_send)
                return lc_orphan_delete, lc_reset_all, lc_send
            """
        )
        assert autorunning_buttons_that_must_not(bad) == ["lc_orphan_delete"]

    def test_no_button_that_must_not_autorun_is_passed_to_pressed_in_the_notebook(self, nb_file):
        assert autorunning_buttons_that_must_not(nb_file.read_text(encoding="utf-8")) == []

    def test_a_note_is_only_what_to_say_and_what_to_expect(self, nb_file):
        found = 0
        for item in nbast.all_items(nb_file.read_text(encoding="utf-8")):
            for call in nbast.calls(item.node, "notes"):
                found += 1
                assert len(call.args) == 2 and not call.keywords
        assert found >= 15

    def test_one_cell_reads_the_presenter_switch_and_it_reads_nothing_else(self, nb_file):
        # Flipping a switch re-runs the cells that read it, without the cells above them. A cell
        # that showed a result and read the switch met a result that a stopped cell never made:
        # a NameError in 14 cells, and an internal-error toast in run mode.
        readers = [
            i for i in nbast.items(nb_file.read_text(encoding="utf-8"))
            if "ib_notes" in nbast.refs(i.node)
        ]  # fmt: skip
        assert len(readers) == 1 and nbast.refs(readers[0].node) == ["ib_notes"]
        assert "ib-note" in ast.unparse(readers[0].node)


class TestCanonicalFormat:
    def test_the_notebook_is_exactly_what_marimo_would_write(self, nb, nb_file):
        """Signatures and returns are hand-written here, so let marimo say if they are right."""
        import difflib

        from marimo._ast.app import InternalApp

        source = nb_file.read_text(encoding="utf-8")
        marker = "import marimo\n"  # the header and docstring above it are ours, not marimo's
        written = InternalApp(nb.app).to_py()
        diff = list(
            difflib.unified_diff(
                source[source.index(marker) :].splitlines(),
                written[written.index(marker) :].splitlines(),
                "file", "marimo", lineterm="", n=2,
            )  # fmt: skip
        )
        assert not diff, "\n".join(diff[:40])
