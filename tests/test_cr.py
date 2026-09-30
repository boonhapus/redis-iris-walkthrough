"""Tests for column 2, Context Retriever (plan section 5).

No service. The hosted Context Retriever and the Redis behind it are one small in-memory stand-in
(fake_cr.py): the admin API answers in snake_case, the MCP endpoint speaks JSON-RPC 2.0, the
service builds indexes under names of its own and writes JSON documents, and Redis answers as
RESP2 does. The requests, the parsing, the cells and the cleanup all run for real against it, in
process, in a marimo kernel driven the way the browser drives it, and in the real `marimo export`
(TestRealRuntime). What the stand-in cannot tell us is listed in tests/README.md under "What
only a live run can show". Its default tool surface is the unified one the live service served on
2026-09-30; the classic per-field surface is kept, and the tests marked BOTH run against each.
"""

import ast
import html
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import notebook_ast as nbast
import pytest
import redis
from fake_cr import ADMIN_KEY, API, MCP, FakeWorld, txt
from fake_search import MISSING_INDEX
from kernel_driver import drive

COLUMN_PREFIX = "cr_"  # tests/conftest.py: this module tests column 2

URL = "redis://default:fake-pw-9f3a@db.example:6379"
RUN = "0123abcd"
# The 21 tools the live service made for the default model on 2026-09-30, and, for the older
# classic surface, the 15 that a workshop recorded.
DEFAULT_TOOLS = sorted(
    """
    count_customer count_order count_product except_results expand_results filter_customer
    filter_order filter_product get_customer_by_id get_order_by_id get_product_by_id
    intersect_results list_customer list_order list_product search_customer_by_text
    search_product_by_text summarize_customer summarize_order summarize_product union_results
    """.split()
)
CLASSIC_TOOLS = sorted(
    """
    filter_customer_by_region filter_customer_by_tier find_customer_by_lifetime_value_range
    get_customer_by_id search_customer_by_text filter_product_by_category find_product_by_price_range
    get_product_by_id search_product_by_text filter_order_by_customer_id filter_order_by_region
    filter_order_by_sku filter_order_by_status find_order_by_total_range get_order_by_id
    """.split()
)


@pytest.fixture(autouse=True)
def column_present(nb):
    if not hasattr(nb, "cr_records"):
        pytest.skip("this module has no column 2")


def install(monkeypatch, fake):
    """Answer every Redis command and every HTTP request from `fake` (the guards still run first)."""
    monkeypatch.setattr(redis.Redis, "execute_command", lambda self, *a, **k: fake.handle(a))
    monkeypatch.setattr(httpx.Client, "send", lambda self, request, **k: fake.http(request))


@pytest.fixture
def world(request, nb, monkeypatch):
    """A FakeWorld on the unified surface; parametrise indirectly with "classic" for the older."""
    fake = FakeWorld(getattr(request, "param", "unified"))
    install(monkeypatch, fake)
    return fake


BOTH = pytest.mark.parametrize("world", ["unified", "classic"], indirect=True)


def probe(nb, url=URL, api="", mcp=""):
    return nb.cr_probe(nb.Secret(ADMIN_KEY), nb.Secret(url), api, mcp)


@pytest.fixture
def conn(nb, world):
    return probe(nb)


@pytest.fixture
def model(nb):
    return nb.cr_build_models(nb.CR_PREFIX)


@pytest.fixture
def session(nb, world, conn, model):
    """A surface made, loaded and keyed: what pressing Create leaves behind."""
    return nb.cr_launch(conn, model)


@pytest.fixture
def catalog(nb, session):
    return nb.cr_list_tools(session)[0]


def words(rendered):
    """The words a reader sees in rendered output: callouts hide their markup inside JSON."""
    text = html.unescape(str(rendered)).replace("\\u003c", "<").replace("\\u003e", ">")
    text = text.replace("\\n", " ")  # a newline inside a callout's JSON
    text = re.sub(r"</?(?:li|p|div|ul|ol|tr|td|th|br|h\d|pre)\b[^>]*>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", text))).strip()


def callout_kind(raw, phrase):
    """The kind ("success", "warn", "danger") of the callout whose message holds `phrase`."""
    at = raw.index(phrase)
    tag = raw[raw.rindex("<marimo-callout-output", 0, at) : raw.index("</marimo-callout-output>", at)]
    return re.search(r"data-kind='&quot;(\w+)&quot;'", tag)[1]


class Shown(str):
    """What a run displayed, as words; .raw keeps the markup, where tables hold their rows."""

    raw = ""


def texts(outputs):
    raw = " ".join(getattr(o, "text", str(o)) for o in outputs)
    shown = Shown(words(raw))
    shown.raw = raw
    return shown


# ---- the data model ---------------------------------------------------------------------------
class TestModels:
    def test_the_classes_are_called_exactly_customer_product_and_order(self, nb, model):
        assert {n: c.__name__ for n, c in model["classes"].items()} == {
            "Customer": "Customer", "Product": "Product", "Order": "Order",
        }  # fmt: skip

    def test_every_key_template_starts_with_the_prefix_and_names_its_key_field(self, nb, model):
        templates = {e["name"]: e["redis_key_template"] for e in model["data_model"]["entities"]}
        assert templates == {
            "Customer": f"{nb.CR_PREFIX}customer:{{customer_id}}",
            "Product": f"{nb.CR_PREFIX}product:{{sku}}",
            "Order": f"{nb.CR_PREFIX}order:{{order_id}}",
        }

    def test_another_prefix_reaches_every_template(self, nb):
        built = nb.cr_build_models("elsewhere:")["data_model"]
        assert all(e["redis_key_template"].startswith("elsewhere:") for e in built["entities"])

    def test_the_export_has_the_three_entities_and_no_access_mappings(self, nb, model):
        data_model = model["data_model"]
        assert data_model["entity_count"] == 3
        assert [e["name"] for e in data_model["entities"]] == ["Customer", "Product", "Order"]
        assert all("acl_field_mappings" not in e for e in data_model["entities"])

    def test_the_export_is_asked_for_its_entities_by_name(self, nb_file):
        (build,) = [
            i.node for i in nbast.all_items(nb_file.read_text("utf-8")) if i.node.name == "cr_build_models"
        ]
        (export,) = nbast.calls(build, "export_data_model")
        assert ast.unparse(nbast.keyword(export, "entities")) == "list(classes.values())"

    def test_every_record_survives_its_class_unchanged(self, nb, model):
        # pydantic drops a field the model does not know without a word
        for entity, rows in nb.cr_records().items():
            for row in rows:
                assert model["classes"][entity](**row).model_dump() == row

    def test_the_dataset_is_six_customers_six_products_and_eight_orders(self, nb):
        rows = nb.cr_records()
        assert {e: len(r) for e, r in rows.items()} == {"Customer": 6, "Product": 6, "Order": 8}
        for region in ("emea", "apac"):
            assert sum(r["region"] == region for r in rows["Customer"]) == 3
            assert sum(r["region"] == region for r in rows["Order"]) == 4
        keys = [r["customer_id"] for r in rows["Customer"]] + [r["sku"] for r in rows["Product"]]
        assert not [k for k in keys if "-" in k]  # a hyphen is a TAG query operator

    def test_the_default_indexes_and_menus_are_the_ones_the_research_validated(self, nb, model):
        fields = {
            f"{e['name']}.{f['name']}": f
            for e in model["data_model"]["entities"]
            for f in e["fields"]
        }
        kinds = {k: [i["type"] for i in f["redis_indices"]] for k, f in fields.items()}
        assert kinds["Customer.name"] == ["text"] and kinds["Customer.customer_id"] == []
        assert kinds["Customer.tier"] == kinds["Order.status"] == ["tag"]
        assert kinds["Customer.lifetime_value"] == kinds["Order.total"] == ["numeric"]
        assert fields["Customer.name"]["redis_indices"][0]["weight"] == 2.0
        assert fields["Order.status"]["allowed_values"] == ["placed", "shipped", "delivered", "returned"]
        assert fields["Customer.tier"]["allowed_values"] == ["bronze", "silver", "gold"]
        assert "allowed_values" not in fields["Product.category"]
        assert fields["Customer.customer_id"]["is_key_component"] is True

    def test_building_with_other_picks_and_back_leaves_no_trace(self, nb):
        before = nb.cr_build_models(nb.CR_PREFIX)["data_model"]
        nb.cr_build_models(nb.CR_PREFIX, {"Order.status": {"index": "none", "allowed": False}})
        assert nb.cr_build_models(nb.CR_PREFIX)["data_model"] == before

    def test_it_is_the_only_place_that_imports_the_sdk(self, nb_file):
        importers = set()
        for item in nbast.all_items(nb_file.read_text("utf-8")):
            for node in ast.walk(item.node):
                if isinstance(node, ast.ImportFrom) and node.module == "context_surfaces":
                    importers.add(item.node.name)
                if isinstance(node, ast.Import) and any(a.name == "context_surfaces" for a in node.names):
                    importers.add(item.node.name)
        assert importers == {"cr_build_models"}


class TestPicker:
    def test_the_menu_lists_the_twelve_fields_that_are_not_keys(self, nb):
        ids = [f["id"] for f in nb.cr_model_fields()]
        assert len(ids) == 12 and "Order.status" in ids and "Order.order_id" not in ids
        assert {f["id"]: f["index"] for f in nb.cr_model_fields()}["Product.category"] == "tag"

    def test_a_text_can_be_text_or_tag_and_a_number_only_numeric(self, nb):
        assert nb.cr_field_state("Customer.name", {})["kinds"] == ["text", "tag", "none"]
        assert nb.cr_field_state("Order.total", {})["kinds"] == ["numeric", "none"]

    def test_the_controls_start_at_the_default_and_then_at_the_kept_change(self, nb):
        assert nb.cr_field_state("Order.status", {}) | {"kinds": None} == {
            "kinds": None, "index": "tag", "allowed": True,
        }  # fmt: skip
        assert nb.cr_field_state("Product.category", {})["allowed"] is False
        kept = {"Order.status": {"index": "none", "allowed": False}}
        now = nb.cr_field_state("Order.status", kept)
        assert (now["index"], now["allowed"]) == ("none", False)
        assert nb.cr_field_state("Order.region", kept)["index"] == "tag"  # another field

    def test_turning_an_index_off_removes_the_field_from_the_export_and_its_tool(self, nb):
        built = nb.cr_build_models(nb.CR_PREFIX, {"Order.status": {"index": "none", "allowed": False}})
        rows = {r["tool"]: r for r in nb.cr_predict_tools(built["data_model"])}
        assert rows["filter_order"]["vs default"] == "changed"
        assert "status" not in rows["filter_order"]["accepts"]
        assert "status" not in rows["count_order"]["accepts"]
        assert rows["get_order_by_id"]["vs default"] == "" and rows["list_order"]["vs default"] == ""
        status = nb.cr_field_json(built["data_model"], "Order.status")
        assert status["redis_indices"] == [] and "allowed_values" not in status

    def test_indexing_the_only_text_field_as_a_tag_swaps_the_text_search_for_a_filter_condition(
        self, nb
    ):
        built = nb.cr_build_models(nb.CR_PREFIX, {"Customer.name": {"index": "tag", "allowed": False}})
        rows = {r["tool"]: r for r in nb.cr_predict_tools(built["data_model"])}
        assert rows["search_customer_by_text"]["vs default"] == "gone"
        assert rows["filter_customer"]["vs default"] == "changed"
        assert "name" in rows["filter_customer"]["accepts"].split("tag: ")[1].split(" · ")[0]
        assert sum(r["vs default"] == "" for r in rows.values()) == 17
        assert {t for t, r in rows.items() if r["vs default"] == "changed"} == {
            "count_customer", "filter_customer", "summarize_customer",
        }  # fmt: skip

    def test_indexing_a_field_as_text_adds_the_text_search_of_an_entity_that_had_none(self, nb):
        built = nb.cr_build_models(nb.CR_PREFIX, {"Order.status": {"index": "text", "allowed": False}})
        rows = {r["tool"]: r for r in nb.cr_predict_tools(built["data_model"])}
        assert rows["search_order_by_text"]["vs default"] == "new"
        assert rows["search_order_by_text"]["accepts"] == "text: status"
        assert rows["filter_order"]["vs default"] == "changed"

    def test_an_entity_with_no_tag_and_no_number_has_no_filter_and_no_summary(self, nb):
        picks = {
            "Product.category": {"index": "none", "allowed": False},
            "Product.price": {"index": "none", "allowed": False},
        }
        built = nb.cr_build_models(nb.CR_PREFIX, picks)
        rows = {r["tool"]: r for r in nb.cr_predict_tools(built["data_model"])}
        assert rows["filter_product"]["vs default"] == "gone"
        assert rows["summarize_product"]["vs default"] == "gone"
        assert rows["count_product"]["accepts"] == "record counts only"
        assert rows["list_product"]["accepts"] == "no sort field"

    def test_the_menu_of_a_tag_can_be_switched_off_and_on(self, nb):
        off = nb.cr_build_models(nb.CR_PREFIX, {"Order.status": {"index": "tag", "allowed": False}})
        rows = {r["tool"]: r for r in nb.cr_predict_tools(off["data_model"])}
        assert rows["filter_order"]["vs default"] == "changed"
        assert "status (" not in rows["filter_order"]["accepts"]  # the menu is off ...
        assert "status" in rows["filter_order"]["accepts"]  # ... and the field is still a tag
        on = nb.cr_build_models(nb.CR_PREFIX, {"Product.category": {"index": "tag", "allowed": True}})
        category = nb.cr_field_json(on["data_model"], "Product.category")
        assert category["allowed_values"] == ["accessories", "apparel", "camping", "footwear"]

    def test_a_menu_on_a_field_that_is_not_a_tag_is_ignored_not_an_error(self, nb):
        built = nb.cr_build_models(nb.CR_PREFIX, {"Order.status": {"index": "text", "allowed": True}})
        status = nb.cr_field_json(built["data_model"], "Order.status")
        assert [i["type"] for i in status["redis_indices"]] == ["text"]
        assert "allowed_values" not in status

    def test_the_default_model_predicts_the_twenty_one_tools_the_live_service_made(self, nb, model):
        rows = nb.cr_predict_tools(model["data_model"])
        assert [r["tool"] for r in rows] == DEFAULT_TOOLS
        assert {r["vs default"] for r in rows} == {""}

    def test_the_prediction_uses_no_classic_name(self, nb, model):
        names = {r["tool"] for r in nb.cr_predict_tools(model["data_model"])}
        shared = {n for n in CLASSIC_TOOLS if n.startswith(("get_", "search_"))}
        assert names & set(CLASSIC_TOOLS) == shared  # only the names both surfaces use

    def test_the_default_prediction_says_which_fields_each_filter_takes_as_the_live_service_listed_them(
        self, nb, model
    ):
        rows = {r["tool"]: r["accepts"] for r in nb.cr_predict_tools(model["data_model"])}
        assert rows["filter_customer"] == (
            "tag: tier (bronze, silver, gold), region (emea, apac) · numeric: lifetime_value"
        )
        assert rows["filter_order"] == (
            "tag: customer_id, sku, region (emea, apac), status (placed, shipped, delivered, "
            "returned) · numeric: total"
        )
        assert rows["filter_product"] == "tag: category · numeric: price"
        assert rows["search_customer_by_text"] == "text: name"
        assert rows["search_product_by_text"] == "text: title"
        assert rows["count_customer"] == (
            "count_distinct of lifetime_value, region, tier · group by region, tier"
        )
        assert rows["count_order"] == (
            "count_distinct of customer_id, region, sku, status, total · "
            "group by customer_id, region, sku, status"
        )
        assert rows["list_customer"] == "sort by lifetime_value"

    def test_every_index_choice_shows_in_the_prediction(self, nb):
        for field in nb.cr_model_fields():
            kinds = nb.cr_field_state(field["id"], {})["kinds"]
            for kind in kinds:
                pick = {field["id"]: {"index": kind, "allowed": False}}
                built = nb.cr_build_models(nb.CR_PREFIX, pick)
                rows = nb.cr_predict_tools(built["data_model"])
                changed = [r for r in rows if r["vs default"]]
                if nb.cr_changes(pick):
                    assert changed, (field["id"], kind)
                else:
                    assert not changed, (field["id"], kind)

    def test_changes_lists_only_what_differs_from_the_default(self, nb):
        picks = {
            "Order.status": {"index": "tag", "allowed": True},  # the default, restated
            "Customer.name": {"index": "tag", "allowed": False},
            "Order.total": {"index": "none", "allowed": False},
        }
        assert nb.cr_changes(picks) == [
            "`Customer.name`: text → tag",
            "`Order.total`: numeric → not indexed",
        ]
        assert nb.cr_changes({}) == []

    def test_the_tool_diff_says_when_prediction_and_service_agree_and_when_they_do_not(self, nb, model):
        predicted = nb.cr_predict_tools(model["data_model"])
        same = nb.cr_tool_diff(predicted, DEFAULT_TOOLS)
        assert "All **21** predicted tools exist" in same["note"]
        assert {(r["predicted"], r["made"]) for r in same["rows"]} == {("yes", "yes")}
        classic = ["get_customer_by_id", "filter_customer_by_tier", "find_order_by_total_range"]
        other = nb.cr_tool_diff(predicted, classic)
        assert "**1** of the 21 predicted tools exist" in other["note"]
        assert "**2** were not predicted" in other["note"] and "classic" in other["note"]
        rows = {r["tool"]: (r["predicted"], r["made"]) for r in other["rows"]}
        assert rows["filter_customer_by_tier"] == ("", "yes") and rows["get_order_by_id"] == ("yes", "")
        assert rows["filter_customer"] == ("yes", "")

    def test_a_surface_made_from_another_model_is_not_blamed_on_the_service_version(self, nb, model):
        predicted = nb.cr_predict_tools(model["data_model"])
        made = [t for t in DEFAULT_TOOLS if t != "get_order_by_id"]
        assert "version" in nb.cr_tool_diff(predicted, made)["note"]
        stale = nb.cr_tool_diff(predicted, made, stale=True)["note"]
        assert "earlier model" in stale and "version" not in stale and "classic" not in stale

    def test_the_predicted_table_names_the_kind_of_index_each_tool_comes_from(self, nb, model):
        rows = {r["tool"]: r["index"] for r in nb.cr_predict_tools(model["data_model"])}
        assert rows["get_order_by_id"] == "key" and rows["search_product_by_text"] == "text"
        assert rows["filter_order"] == "tag, numeric" and rows["list_order"] == "numeric"
        assert rows["union_results"] == "any"
        tags_only = nb.cr_build_models(nb.CR_PREFIX, {"Order.total": {"index": "none", "allowed": False}})
        now = {r["tool"]: r["index"] for r in nb.cr_predict_tools(tags_only["data_model"])}
        assert now["filter_order"] == "tag" and now["list_order"] == ""
        lost = [r for r in nb.cr_predict_tools(tags_only["data_model"]) if r["vs default"] == "gone"]
        assert [(r["tool"], r["index"]) for r in lost] == [("summarize_order", "")]

    def test_a_tool_the_picks_removed_is_not_expected_of_the_service(self, nb):
        built = nb.cr_build_models(nb.CR_PREFIX, {"Order.total": {"index": "none", "allowed": False}})
        predicted = nb.cr_predict_tools(built["data_model"])
        made = [t for t in DEFAULT_TOOLS if t != "summarize_order"]
        assert nb.cr_tool_diff(predicted, made)["note"].startswith("All **20** predicted")


class TestEndpoints:
    def test_defaults_typed_values_and_the_environment(self, nb, monkeypatch):
        assert nb.cr_endpoints() == (API, MCP)
        assert nb.cr_endpoints("https://x.example/ctx/ ", " https://y.example/mcp/") == (
            "https://x.example/ctx", "https://y.example/mcp",
        )  # fmt: skip
        monkeypatch.setenv("CTX_API_URL", "https://env.example/api")
        monkeypatch.setenv("CTX_MCP_URL", "https://env.example/mcp")
        assert nb.cr_endpoints("", "") == ("https://env.example/api", "https://env.example/mcp")
        assert nb.cr_endpoints("https://typed.example", "")[0] == "https://typed.example"

    def test_the_prefilled_fields_offer_the_same_defaults(self, nb, nb_file):
        def literal(name):
            for node in ast.walk(ast.parse(nb_file.read_text("utf-8"))):
                if (
                    isinstance(node, ast.Assign)
                    and [t.id for t in node.targets if isinstance(t, ast.Name)] == [name]
                ):
                    return nbast.keyword(node.value, "value").values[-1].value
            raise LookupError(name)

        assert (literal("cr_api_url"), literal("cr_mcp_url")) == nb.cr_endpoints()

    def test_a_url_becomes_the_connection_config_the_service_is_given(self, nb):
        config = nb.cr_connection_config("redis://us%40er:p%40ss%2Fw@db.example:6380/2")
        assert config == {
            "addr": "db.example:6380", "username": "us@er", "password": "p@ss/w", "db": 2,
            "tls_enabled": False, "pool_size": 2, "min_idle_conns": 0,
        }  # fmt: skip
        tls = nb.cr_connection_config("rediss://:pw@h.example")
        assert tls["tls_enabled"] is True and tls["username"] == "default" and tls["addr"] == "h.example:6379"
        assert tls["db"] == 0


# ---- test connection and orphans -----------------------------------------------------------------
class TestConnection:
    def test_it_only_reads(self, nb, world, conn):
        assert world.requests == [("GET", "/context-surfaces/api/v1/context-surfaces")]
        assert world.names() == ["PING", "INFO", "FT._LIST", "SCAN", "DBSIZE", "INFO", "JSON.GET"]
        assert world.writes() == []
        assert conn["facts"]["version"] == "8.4.0" and conn["facts"]["where"] == "db.example:6379"
        assert conn["surfaces"] == [] and len(conn["calls"]) == 1

    def stranger(self, world):
        """A surface that is somebody else's, with a name, a description and a model to leak."""
        sid = world.add_surface("acme-prod-claims-agent", run=None, created_by="acme")
        world.surfaces[sid]["description"] = "claims triage for the ACME production account"
        world.surfaces[sid]["data_model"] = {
            "entities": [{"name": "Claim", "fields": [{"name": "ssn", "redis_indices": []}]}]
        }
        return sid

    def test_other_peoples_surfaces_are_counted_and_never_shown_or_kept(self, nb, world):
        self.stranger(world)
        found = probe(nb)
        assert found["listed"] == 1 and found["surfaces"] == []
        shown = words(nb.cr_describe(found).text)
        assert "sees 1 surfaces" in shown and "1 surfaces, not shown" in shown
        for private in ("acme", "claims", "ACME", "ssn", "Claim"):
            assert private not in shown + repr(found), private
        (listing,) = found["calls"]
        assert listing.method == "GET" and listing.request == {"headers": {"X-API-Key": "****"}}
        assert listing.response == {"context_surfaces": "1 surfaces, not shown: they are your account's"}

    def test_a_launch_shows_no_foreign_surface_either(self, nb, world, conn, model):
        self.stranger(world)
        session = nb.cr_launch(conn, model)
        shown = words(nb.cr_launched(session).text) + repr(session)
        assert "not shown" in shown
        for private in ("acme", "claims", "ACME", "ssn", "Claim"):
            assert private not in shown, private

    def test_only_this_notebooks_own_surfaces_are_kept_and_only_four_fields_of_each(self, nb, world):
        world.add_surface("iris-basics-feedcafe", run="feedcafe")
        world.surfaces["cs_0001"]["data_model"] = {"entities": []}
        self.stranger(world)
        found = probe(nb)
        assert found["listed"] == 2
        assert [set(s) for s in found["surfaces"]] == [{"id", "name", "metadata", "created_at"}]
        assert found["surfaces"][0]["name"] == "iris-basics-feedcafe"

    def test_only_a_successful_listing_of_surfaces_is_replaced_by_a_count(self, nb):
        url = API + "/api/v1/context-surfaces"
        listing = nb.HttpCall("cr", "GET", url + "?page=1&page_size=50", {}, 200, 5.0, "x" * 50)
        assert nb.cr_shown(listing).response == {
            "context_surfaces": "some surfaces, not shown: they are your account's"
        }
        failed, one = listing._replace(status=401), listing._replace(url=url + "/cs_0001")
        assert nb.cr_shown(failed) is failed and nb.cr_shown(one) is one  # the run's own surface

    def test_it_pages_through_every_surface(self, nb, world):
        for i in range(120):
            world.add_surface(f"other-{i}", run=None, created_by="someone")
        found = nb.cr_list_surfaces(API, nb.Secret(ADMIN_KEY))
        assert len(found) == 120 and len(world.requests) == 3

    def test_an_account_the_listing_cut_short_is_said_to_have_at_least_that_many(self, nb, world):
        """Live, 2026-09-30: "sees 500 surfaces" was the ten pages of fifty, not the account."""
        for i in range(nb.CR_LIST_PAGE * nb.CR_LIST_PAGES + 20):
            world.add_surface(f"other-{i}", run=None, created_by="someone")
        found = probe(nb)
        assert found["listed"] == 500 and len(world.requests) == nb.CR_LIST_PAGES  # a page at a time
        shown = words(nb.cr_describe(found).text)
        assert "sees 500 or more surfaces" in shown and "sees 500 surfaces" not in shown

    def test_an_account_of_a_few_hundred_surfaces_is_counted_exactly(self, nb, world):
        for i in range(123):
            world.add_surface(f"other-{i}", run=None, created_by="someone")
        assert "sees 123 surfaces" in words(nb.cr_describe(probe(nb)).text)

    def test_a_wrong_admin_key_is_a_scrubbed_401(self, nb, world):
        with pytest.raises(nb.ServiceError) as caught:
            nb.cr_list_surfaces(API, nb.Secret("cs_admin_WRONG0123456789"))
        assert caught.value.status == 401 and "WRONG0123456789" not in str(caught.value)

    def test_a_database_without_search_or_json_says_how_to_fix_it(self, nb, world):
        for missing in ("search", "json_module"):
            setattr(world, missing, False)
            with pytest.raises(nb.ServiceError, match="no Search or no JSON") as caught:
                probe(nb)
            assert "redis.io/try-free" in str(caught.value)
            setattr(world, missing, True)

    def test_the_default_user_on_a_database_with_other_data_is_flagged(self, nb, world):
        world.docs["prod:customer:1"] = {}
        shared = probe(nb)
        assert shared["facts"]["shared"] is True
        assert "default" in nb.cr_describe(shared).text and "scratch" in nb.cr_describe(shared).text
        named = probe(nb, "redis://app:pw-0123@db.example:6379")
        assert named["facts"]["shared"] is False and "scratch" not in nb.cr_describe(named).text

    def test_a_refused_key_or_a_wrong_address_is_advice_and_not_a_status_code(self, nb):
        refused = " ".join(nb.cr_failed(nb.ServiceError("cr", 401, "http", "bad key")).text.split())
        assert "CTX_ADMIN_KEY" in refused and "Admin keys" in refused and "cr HTTP" not in refused
        assert f"{nb.CR_KEY_TTL_S // 3600} h after Create" in refused
        missing = " ".join(nb.cr_failed(nb.ServiceError("cr", 404, "http", "nope")).text.split())
        assert "Admin API address" in missing
        assert "Failed Dependency" in nb.cr_failed(nb.ServiceError("cr", 424, "http", "x")).text

    def test_only_this_notebooks_own_keys_do_not_make_the_database_shared(self, nb, world):
        # Column 0 seeds the same database before this column tests it, and a colleague's
        # earlier run of this notebook leaves keys too: neither is another app's data.
        world.docs[f"{nb.RS_PREFIX}doc:a"] = {}
        world.docs["irisnb_deadbeef_rs:doc:b"] = {}
        assert probe(nb)["facts"]["shared"] is False
        assert "scratch" not in nb.cr_describe(probe(nb)).text
        world.docs["prod:customer:1"] = {}
        facts = probe(nb)["facts"]
        assert facts["shared"] is True and len(facts["notebook_keys"]) == 2
        assert facts["dbsize"] - len(facts["notebook_keys"]) == 1  # the one that is not ours

    def test_an_empty_database_is_not_shared(self, nb, world, conn):
        assert conn["facts"]["shared"] is False
        assert "scratch" not in nb.cr_describe(conn).text

    def test_typed_addresses_win_and_lose_their_trailing_slash(self, nb, world):
        found = probe(nb, api=API + "/", mcp="https://eu.example/mcp/")
        assert (found["api"], found["mcp"]) == (API, "https://eu.example/mcp")

    def test_preflight_lists_the_surfaces_and_reaches_the_database(self, nb, world, monkeypatch):
        monkeypatch.setenv("CTX_ADMIN_KEY", ADMIN_KEY)
        monkeypatch.setenv("REDIS_URL", URL)
        world.add_surface("iris-basics-feedcafe")
        ok, detail = nb.cr_test_connection()
        assert ok is True and "1 surfaces visible" in detail and "Redis 8.4.0" in detail
        assert world.writes() == [] and {m for m, _ in world.requests} == {"GET"}

    def test_preflight_composes_the_url_from_the_split_settings(self, nb, world, monkeypatch):
        monkeypatch.setenv("CTX_ADMIN_KEY", ADMIN_KEY)
        monkeypatch.setenv("REDIS_HOST", "db.example")
        monkeypatch.setenv("REDIS_PASSWORD", "fake-pw-9f3a")
        assert nb.cr_test_connection()[0] is True

    def test_preflight_without_settings_is_a_no_not_a_crash(self, nb, world):
        ok, detail = nb.cr_test_connection()
        assert ok is False and "CTX_ADMIN_KEY" in detail and world.requests == []


class TestOrphans:
    def stale_and_friends(self, nb, world):
        world.add_surface("radish-bank-workshop", run=None, created_by="somebody")  # a demo app's
        old = world.add_surface("iris-basics-feedcafe", run="feedcafe", age_h=5)
        young = world.add_surface("iris-basics-deadbeef", run="deadbeef", age_h=0.5)
        mine = world.add_surface(nb.RUN_TAG, run=nb.RUN, age_h=9)
        return old, young, mine

    def test_only_other_runs_of_this_notebook_are_listed_and_only_old_ones_offered(self, nb, world):
        old, young, _ = self.stale_and_friends(nb, world)
        found = nb.cr_orphans(nb.cr_list_surfaces(API, nb.Secret(ADMIN_KEY)))
        assert [r["surface"] for r in found["rows"]] == [old, young]
        assert [s["id"] for s in found["stale"]] == [old]
        offered = {r["surface"]: r["offered"] for r in found["rows"]}
        assert offered[old] == "yes" and offered[young].startswith("no") and "2 h" in offered[young]

    def test_a_stamp_the_choke_point_would_refuse_is_shown_but_not_offered(self, nb, world, conn):
        old, *_ = self.stale_and_friends(nb, world)
        legacy = world.add_surface("iris-basics-abc123", run="abc123", age_h=5)  # 6 hex digits
        renamed = world.add_surface("my-own-name", run="cafef00d", age_h=5)
        found = nb.cr_orphans(nb.cr_list_surfaces(API, conn["admin"]))
        offered = {r["surface"]: r["offered"] for r in found["rows"]}
        assert offered[legacy] == offered[renamed] == "no, the stamp is not recognised"
        assert [s["id"] for s in found["stale"]] == [old]
        for surface in found["stale"]:  # everything offered is something the guard accepts
            nb.approve_cr_orphan(surface, nb.ORPHAN_CONFIRM)
        assert nb.cr_delete_orphans(conn, found["stale"], nb.ORPHAN_CONFIRM) == [old]
        assert {legacy, renamed} <= set(world.surfaces)

    def test_a_surface_with_no_readable_age_is_never_offered(self, nb):
        odd = {"id": "x", "name": "iris-basics-feedcafe", "created_at": "soon",
               "metadata": {"created_by": nb.CR_CREATED_BY, "run": "feedcafe"}}  # fmt: skip
        found = nb.cr_orphans([odd])
        assert found["stale"] == [] and found["rows"][0]["age"] == "unknown"

    def test_the_delete_needs_the_typed_confirmation_and_sends_nothing_without_it(self, nb, world, conn):
        old, *_ = self.stale_and_friends(nb, world)
        stale = nb.cr_orphans(nb.cr_list_surfaces(API, conn["admin"]))["stale"]
        before = len(world.requests)
        with pytest.raises(ValueError, match="to confirm"):
            nb.cr_delete_orphans(conn, stale, "yes please")
        assert len(world.requests) == before and old in world.surfaces

    def test_a_confirmed_delete_removes_the_old_orphan_and_nothing_else(self, nb, world, conn):
        old, young, mine = self.stale_and_friends(nb, world)
        stale = nb.cr_orphans(nb.cr_list_surfaces(API, conn["admin"]))["stale"]
        assert nb.cr_delete_orphans(conn, stale, nb.ORPHAN_CONFIRM) == [old]
        assert [m for m, _ in world.requests].count("DELETE") == 1
        assert old not in world.surfaces and {young, mine} <= set(world.surfaces)
        assert any(s["name"] == "radish-bank-workshop" for s in world.surfaces.values())

    def test_one_surface_that_is_not_this_notebooks_stops_the_lot_before_any_delete(self, nb, world, conn):
        old, *_ = self.stale_and_friends(nb, world)
        good = world.surfaces[old]
        bad = {**good, "id": "cs_9999", "name": "someone-elses"}
        with pytest.raises(ValueError, match="stale surface"):
            nb.cr_delete_orphans(conn, [good, bad], nb.ORPHAN_CONFIRM)
        assert old in world.surfaces and [m for m, _ in world.requests].count("DELETE") == 0

    def test_a_surface_the_console_already_removed_counts_as_gone(self, nb, world, conn):
        old, *_ = self.stale_and_friends(nb, world)
        stale = nb.cr_orphans(nb.cr_list_surfaces(API, conn["admin"]))["stale"]
        del world.surfaces[old]
        assert nb.cr_delete_orphans(conn, stale, nb.ORPHAN_CONFIRM) == [old]


# ---- create and load ------------------------------------------------------------------------------
def posts(world, suffix):
    return [b for m, p, b in world.bodies if m == "POST" and p.endswith(suffix)]


class TestLaunch:
    def test_the_create_request_is_stamped_pooled_and_carries_the_decoded_credentials(self, nb, world):
        url = "redis://us%40er:p%40ss%2Fw@db.example:6380/0"
        nb.cr_launch(probe(nb, url), nb.cr_build_models(nb.CR_PREFIX))
        (body,) = posts(world, "/api/v1/context-surfaces")
        assert body["name"] == nb.RUN_TAG
        assert body["metadata"] == {"created_by": "iris-basics-notebook", "run": nb.RUN}
        assert body["data_source"]["type"] == "redis"
        assert world.config == {
            "addr": "db.example:6380", "username": "us@er", "password": "p@ss/w", "db": 0,
            "tls_enabled": False, "pool_size": 2, "min_idle_conns": 0,
        }  # fmt: skip
        assert all(e["redis_key_template"].startswith(nb.CR_PREFIX) for e in body["data_model"]["entities"])

    def test_the_agent_key_expires_in_two_hours(self, nb, world, session):
        (body,) = posts(world, "/agent-keys")
        expires = datetime.fromisoformat(body["expires_at"])
        soon = datetime.now(timezone.utc) + timedelta(seconds=nb.CR_KEY_TTL_S)
        assert abs((expires - soon).total_seconds()) < 60 and body["name"] == nb.RUN_TAG + "-agent"
        assert nb.CR_KEY_TTL_S == 7200

    def test_the_mcp_address_comes_from_the_key_listing(self, nb, world, session):
        assert session["mcp_url"] == MCP and "list of agent keys" in session["mcp_from"]

    def test_without_a_listed_address_the_setting_is_used(self, nb, world, model):
        world.mcp_url = None
        found = nb.cr_launch(probe(nb, mcp="https://my.example/mcp/"), model)
        assert found["mcp_url"] == "https://my.example/mcp" and "setting" in found["mcp_from"]

    def test_records_go_one_entity_per_call_in_order_and_stop_at_the_first_failure(self, nb, world, session):
        sent = posts(world, "/data")
        assert [b["entity"] for b in sent] == ["Customer", "Product", "Order"]
        assert all(b["options"] == {"on_conflict": "overwrite", "on_error": "fail_fast"} for b in sent)
        assert [b["records"] for b in sent] == list(nb.cr_records().values())
        assert session["imported"] == {"Customer": 6, "Product": 6, "Order": 8}

    def test_a_reported_failure_is_an_error_and_not_a_shrug(self, nb, world, conn, model, monkeypatch):
        failed = {"imported": 0, "failed": 2, "errors": [{"error": "bad tier"}]}
        monkeypatch.setattr(world, "load", lambda request, sid, body: world.reply(request, 200, failed))
        with pytest.raises(ValueError, match="2 Customer records"):
            nb.cr_launch(conn, model)

    def test_every_key_is_expired_by_name_and_only_those(self, nb, world, session, model):
        expected = nb.cr_record_keys(model["data_model"])
        assert len(expected) == 20 and session["keys"] == expected
        assert session["expired"] == 20
        assert world.ttls == {key: nb.CR_KEY_TTL_S for key in expected}
        expiries = [a for a in world.sent if txt(a[0]) == "EXPIRE"]
        assert [txt(a[1]) for a in expiries] == expected
        names = [txt(a[0]) for a in world.sent]
        assert not set(names) & {"KEYS", "FLUSHDB", "DEL", "HSET"}
        assert names.count("SCAN") == 1  # the look at Test connection; the launch scans nothing

    def test_the_ledger_names_the_surface_the_key_every_document_and_the_new_indexes(self, nb, world, session):
        assert nb.ledger_live("cr", "surface") == [session["surface"]]
        assert nb.ledger_state("cr", "surface")[nb.RUN_TAG] == "deleted"  # the intent was settled
        assert nb.ledger_live("cr", "agent_key") == [session["agent_id"]]
        assert sorted(nb.ledger_live("cr", "key")) == sorted(session["keys"])
        assert sorted(nb.ledger_live("cr", "index")) == sorted(world.indexes)
        assert all(i["ours"] for i in session["indexes"]) and len(session["indexes"]) == 3

    def test_the_surface_and_the_keys_are_ledgered_before_the_requests_that_make_them(
        self, nb, world, conn, model, monkeypatch
    ):
        seen = {}
        original = world.http

        def spying(request):
            if request.method == "POST" and request.url.path.endswith("/api/v1/context-surfaces"):
                seen["create"] = nb.ledger_state("cr", "surface")
            if request.method == "POST" and request.url.path.endswith("/data"):
                seen.setdefault("import", nb.ledger_state("cr", "key"))
            return original(request)

        monkeypatch.setattr(httpx.Client, "send", lambda self, request, **k: spying(request))
        nb.cr_launch(conn, model)
        assert seen["create"] == {nb.RUN_TAG: "intent"}  # by name: the id does not exist yet
        assert set(seen["import"].values()) == {"intent"} and len(seen["import"]) == 20

    def test_the_password_and_the_agent_key_appear_in_no_trace_and_no_summary(self, nb, world, session):
        key = next(iter(world.agent_keys))
        shown = nb.wire_view("t", session["calls"]).text + nb.cr_launched(session).text
        shown += repr(list(nb.HTTP_TRACE)) + repr(session)
        assert key not in shown and "fake-pw-9f3a" not in shown
        assert key in nb.SECRETS.forms() and "fake-pw-9f3a" in nb.SECRETS.forms()
        assert nb.scrub(f"leak {key}") == "leak ****"
        assert session["agent"].reveal() == key and str(session["agent"]).startswith("••••")

    def test_every_call_is_a_traced_cr_call_and_the_secrets_are_masked_in_it(self, nb, world, session):
        assert session["calls"] and {c.component for c in session["calls"]} == {"cr"}
        assert [(c.method, urlpath(c.url)) for c in session["calls"]][:3] == [
            ("GET", "/context-surfaces/api/v1/context-surfaces"),
            ("POST", "/context-surfaces/api/v1/context-surfaces"),
            ("POST", f"/context-surfaces/api/v1/context-surfaces/{session['surface']}/agent-keys"),
        ]
        create = session["calls"][1].request["json"]
        assert create["data_source"]["connection_config"]["password"] == "****"
        assert session["calls"][1].request["headers"] == {"X-API-Key": "****"}

    def test_pressing_create_twice_reuses_the_surface(self, nb, world, conn, model, session):
        again = nb.cr_launch(conn, model)
        assert again["reused"] is True and again["surface"] == session["surface"]
        assert again["stale"] is False and [i["ours"] for i in again["indexes"]] == [True] * 3
        assert "the service built 3 indexes" in words(nb.cr_launched(again).text)
        assert len(world.surfaces) == 1 and len(posts(world, "/api/v1/context-surfaces")) == 1
        assert len(world.indexes) == 3 and len(world.docs) == 20
        assert again["agent"].reveal() != session["agent"].reveal()  # a fresh key each time

    def test_a_create_whose_reply_was_lost_is_found_by_name_and_reused(self, nb, world, conn, model):
        world.create_reply_lost = True
        with pytest.raises(nb.ServiceError, match="504"):
            nb.cr_launch(conn, model)
        assert nb.ledger_state("cr", "surface") == {nb.RUN_TAG: "intent"}
        assert len(world.surfaces) == 1  # it exists, and the caller could not know
        world.create_reply_lost = False
        again = nb.cr_launch(conn, model)
        assert again["reused"] is True and len(world.surfaces) == 1
        assert nb.ledger_live("cr", "surface") == [again["surface"]]

    def test_a_relaunch_after_a_lost_reply_still_ledgers_the_indexes_the_service_built(
        self, nb, world, conn, model
    ):
        world.create_reply_lost = True
        with pytest.raises(nb.ServiceError, match="504"):
            nb.cr_launch(conn, model)
        world.create_reply_lost = False
        again = nb.cr_launch(conn, model)  # the service's indexes are in the snapshot by now
        assert again["reused"] is True and [i["ours"] for i in again["indexes"]] == [True] * 3
        assert sorted(nb.ledger_live("cr", "index")) == sorted(world.indexes) and len(world.indexes) == 3
        assert "the service built 3 indexes" in words(nb.cr_launched(again).text)

    def test_a_failed_import_still_expires_what_it_wrote_and_ledgers_the_indexes(
        self, nb, world, conn, model
    ):
        world.forced["data"] = 500  # the Order import: Customer and Product are in by then
        with pytest.raises(nb.ServiceError, match="500"):
            nb.cr_launch(conn, model)
        assert len(world.docs) == 12 and world.ttls == {k: nb.CR_KEY_TTL_S for k in world.docs}
        assert sorted(nb.ledger_live("cr", "index")) == sorted(world.indexes) and len(world.indexes) == 3
        assert len(nb.ledger_live("cr", "key", pending=True)) == 20  # by name, written or not
        done = nb.cr_cleanup(conn["admin"], conn["api"], conn["url"])
        assert (done["removed"], done["keys"], done["dropped"]) == (12, 20, 3)
        assert world.docs == {} and world.indexes == {} and world.surfaces == {}

    def test_a_surface_that_reports_a_lifecycle_status_shows_it_and_one_that_does_not_stays_quiet(
        self, nb, world, conn, model
    ):
        assert ", status" not in words(nb.cr_launched(nb.cr_launch(conn, model)).text)
        nb.cr_cleanup(conn["admin"], conn["api"], conn["url"])
        world.status = "ready"
        shown = words(nb.cr_launched(nb.cr_launch(conn, model)).text)
        assert re.search(r"\(id cs_\d+, status ready\)", shown)
        assert ", status ready)" in words(nb.cr_launched(nb.cr_launch(conn, model)).text)  # picked up

    def test_a_reused_surface_made_from_another_model_is_stale_and_says_so(
        self, nb, world, conn, model, session
    ):
        changed = nb.cr_build_models(nb.CR_PREFIX, {"Order.status": {"index": "none", "allowed": False}})
        again = nb.cr_launch(conn, changed)
        assert again["reused"] is True and again["stale"] is True and len(world.surfaces) == 1
        shown = words(nb.cr_launched(again).text)
        assert "Picked up the surface" in shown and "made from an earlier model" in shown
        assert nb.cr_launch(conn, model)["stale"] is False
        assert "earlier model" not in words(nb.cr_launched(nb.cr_launch(conn, model)).text)

    def test_a_surface_reply_without_the_model_is_not_called_stale(
        self, nb, world, conn, model, session, monkeypatch
    ):
        shown = world.shown
        monkeypatch.setattr(world, "shown", lambda sid: {k: v for k, v in shown(sid).items() if k != "data_model"})
        changed = nb.cr_build_models(nb.CR_PREFIX, {"Order.status": {"index": "none", "allowed": False}})
        assert nb.cr_launch(conn, changed)["stale"] is False

    def test_the_model_is_compared_by_its_indexes_and_menus_and_nothing_else(self, nb, model):
        exported = model["data_model"]
        noisy = json.loads(json.dumps(exported))
        noisy["entities"][0]["extra"] = "added by the service"
        nb.cr_field_json(noisy, "Customer.tier")["redis_indices"] = [{"type": "TAG", "weight": 2}]
        assert nb.cr_signature(noisy) == nb.cr_signature(exported)
        nb.cr_field_json(noisy, "Order.status")["allowed_values"] = ["placed"]
        assert nb.cr_signature(noisy) != nb.cr_signature(exported)
        assert nb.cr_signature(None) == {}

    def test_a_create_reply_without_an_id_is_a_message_naming_what_it_did_have(
        self, nb, world, conn, model, monkeypatch
    ):
        monkeypatch.setattr(world, "create", lambda request, body: world.reply(request, 201, {"name": "x"}))
        with pytest.raises(nb.ServiceError, match="creating the surface has no 'id'; it has: name"):
            nb.cr_launch(conn, model)
        assert nb.ledger_state("cr", "surface") == {nb.RUN_TAG: "intent"}

    def test_an_agent_key_reply_without_the_key_is_a_message_and_the_id_is_still_ledgered(
        self, nb, world, conn, model, monkeypatch
    ):
        monkeypatch.setattr(world, "mint", lambda request, sid, body: world.reply(request, 201, {"id": "ak_1"}))
        with pytest.raises(nb.ServiceError, match="minting an agent key has no 'key'"):
            nb.cr_launch(conn, model)
        assert nb.ledger_live("cr", "agent_key") == ["ak_1"]

    def test_a_listed_surface_without_an_id_is_a_message(self, nb, world, conn, monkeypatch):
        sid = world.add_surface(nb.RUN_TAG, run=nb.RUN)
        del world.surfaces[sid]["id"]
        with pytest.raises(nb.ServiceError, match="the list of surfaces has no 'id'"):
            nb.cr_adopt(API, conn["admin"])

    def test_a_failed_create_leaves_the_intent_and_makes_nothing_else(self, nb, world, conn, model):
        world.forced["create"] = 500
        with pytest.raises(nb.ServiceError, match="500"):
            nb.cr_launch(conn, model)
        assert world.docs == {} and world.agent_keys == {} and world.writes() == []
        assert nb.ledger_state("cr", "surface") == {nb.RUN_TAG: "intent"}

    def test_keys_outside_the_run_prefix_are_refused_before_anything_is_sent(self, nb, world, conn):
        before = len(world.requests)
        with pytest.raises(ValueError, match="must start with"):
            nb.cr_launch(conn, nb.cr_build_models("prod:"))
        assert len(world.requests) == before and nb.ledger_state("cr", "surface") == {}

    def test_the_choke_point_would_refuse_them_too(self, nb, world, conn):
        body = {
            "name": nb.RUN_TAG,
            "metadata": {"created_by": nb.CR_CREATED_BY, "run": nb.RUN},
            "data_model": nb.cr_build_models("prod:")["data_model"],
            "data_source": {"connection_config": nb.cr_connection_config(URL)},
        }
        with pytest.raises(nb.ServiceError, match="redis_key_template"):
            nb.http_call("cr", "POST", f"{API}/api/v1/context-surfaces", json=body)

    def test_an_index_that_is_not_under_the_prefix_is_reported_and_never_ledgered(self, nb, world, conn, model):
        world.appear_on_create = [("idx:otherapp", "prod:app:")]
        found = nb.cr_launch(conn, model)
        others = [i for i in found["indexes"] if not i["ours"]]
        assert [i["name"] for i in others] == ["idx:otherapp"] and others[0]["prefixes"] == ["prod:app:"]
        assert "idx:otherapp" not in nb.ledger_live("cr", "index")
        assert "idx:otherapp" not in nb.OWNED_INDEXES and len(nb.OWNED_INDEXES) == 3
        assert "not under " + nb.CR_PREFIX in words(nb.cr_launched(found).text)
        assert "idx:otherapp" in words(nb.cr_launched(found).text)

    def test_the_view_of_a_launch_names_the_surface_the_counts_and_the_indexes(self, nb, world, session):
        shown = words(nb.cr_launched(session).text)
        assert f"Created the surface {nb.RUN_TAG} (id {session['surface']})" in shown
        assert "loaded 20 records (6 Customer, 6 Product, 8 Order)" in shown
        assert "20 of 20 keys now expire in 2 h" in shown
        assert "the service built 3 indexes: idx:cs_0001" in shown
        assert "The requests, as HTTP" in shown
        assert session["stale"] is False and "earlier model" not in shown

    def test_a_second_launch_says_it_picked_the_surface_up(self, nb, world, conn, model, session):
        assert "Picked up the surface" in words(nb.cr_launched(nb.cr_launch(conn, model)).text)


def urlpath(url):
    return httpx.URL(url).path


class TestAdopt:
    def look_alikes(self, nb, world):
        return [
            world.add_surface(nb.RUN_TAG, run=None, created_by="somebody"),  # the name only
            world.add_surface(nb.RUN_TAG, run="feedcafe"),  # the name, another run
            world.add_surface("radish-bank-workshop", run=nb.RUN),  # this run, another name
            world.add_surface(nb.RUN_TAG, run=nb.RUN, created_by="somebody"),  # not stamped by us
        ]

    def test_only_a_surface_with_the_whole_stamp_is_this_runs_and_ledgered(self, nb, world, conn):
        self.look_alikes(nb, world)
        assert nb.cr_adopt(API, conn["admin"]) == []
        assert nb.ledger_state("cr", "surface") == {}
        mine = world.add_surface(nb.RUN_TAG, run=nb.RUN)
        assert [s["id"] for s in nb.cr_adopt(API, conn["admin"])] == [mine]
        assert nb.ledger_live("cr", "surface") == [mine]

    def test_a_launch_makes_its_own_surface_rather_than_take_a_look_alike(self, nb, world, conn, model):
        look_alikes = self.look_alikes(nb, world)
        made = nb.cr_launch(conn, model)
        assert made["reused"] is False and made["surface"] not in look_alikes

    def test_cleanup_finding_a_lost_create_by_name_leaves_the_look_alikes_alone(self, nb, world, conn):
        look_alikes = self.look_alikes(nb, world)
        nb.ledger_add("cr", "surface", nb.RUN_TAG, "intent")
        done = nb.cr_cleanup(conn["admin"], conn["api"], conn["url"])
        assert done["surfaces"] == 0 and set(look_alikes) <= set(world.surfaces)
        assert nb.ledger_live("cr", "surface", pending=True) == []


class TestPeek:
    def test_it_reads_one_document_and_the_index_of_each_entity_and_only_reads(self, nb, world, conn, session):
        before = len(world.sent)
        seen = nb.cr_look(conn["redis"], session)
        assert seen["key"] == f"{nb.CR_PREFIX}customer:C001"
        assert json.loads(seen["doc"])["name"] == "Ada Lovelace"
        assert sorted(row["documents"] for row in seen["indexes"]) == ["6", "6", "8"]
        assert all(row["over"].startswith(nb.CR_PREFIX) for row in seen["indexes"])
        fields = {row["fields"] for row in seen["indexes"]}
        assert "name TEXT, tier TAG, region TAG, lifetime_value NUMERIC" in fields
        assert [txt(a[0]) for a in world.sent[before:]] == ["JSON.GET"] + ["FT.INFO"] * 3
        assert seen["commands"][0] == f"JSON.GET {nb.CR_PREFIX}customer:C001"

    def test_no_document_at_the_key_is_shown_as_none(self, nb, world, conn, session):
        del world.docs[session["keys"][0]]
        assert nb.cr_look(conn["redis"], session)["doc"] is None


# ---- MCP ----------------------------------------------------------------------------------------------
@BOTH
class TestMcp:
    def test_tools_list_is_json_rpc_and_comes_back_sorted(self, nb, world, session):
        tools, call = nb.cr_list_tools(session)
        names = [t["name"] for t in tools]
        assert names == sorted(names) and "get_customer_by_id" in names
        assert len(names) == (15 if world.style == "classic" else 21)
        method, path, body = world.bodies[-1]
        assert (method, path) == ("POST", "/mcp")
        assert body == {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert call.request["headers"] == {"X-API-Key": "****"} and call.status == 200

    def test_tools_call_is_json_rpc_and_the_text_is_parsed(self, nb, world, session):
        found = nb.cr_call_tool(session, "get_customer_by_id", {"id": "C001"})
        assert found["outcome"] == "ok" and found["data"]["name"] == "Ada Lovelace"
        assert world.bodies[-1][2] == {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_customer_by_id", "arguments": {"id": "C001"}},
        }  # fmt: skip
        assert found["call"].method == "POST" and found["code"] is None

    def test_a_tool_level_failure_is_read_from_iserror_and_not_raised(self, nb, world, session):
        found = nb.cr_call_tool(session, "get_customer_by_id", {"id": "C999"})
        assert found["outcome"] == "tool_error" and found["data"] is None
        assert found["message"].startswith("Error executing tool: get by ID failed")

    def test_arguments_outside_the_schema_come_back_as_the_services_own_words(self, nb, world, session):
        extra = nb.cr_call_tool(session, "get_customer_by_id", {"id": "C001", "x": 1})
        assert extra["outcome"] == "rejected" and extra["code"] == -32602
        assert extra["message"] == (
            'invalid params: validating "arguments": validating root: '
            'unexpected additional properties ["x"]'
        )
        missing = nb.cr_call_tool(session, "get_customer_by_id", {})
        assert missing["outcome"] == "rejected" and "missing properties" in missing["message"]

    def test_a_menu_is_enforced_by_the_service(self, nb, world, session, catalog):
        plan = nb.cr_break_plan(catalog, "get_customer_by_id")
        found = nb.cr_call_tool(session, plan["tool"], plan["arguments"])
        assert found["outcome"] == "rejected" and found["code"] == -32602
        assert "not-a-choice does not equal any of" in found["message"]

    def test_text_that_is_not_json_is_kept_as_raw_text(self, nb, world, session, monkeypatch):
        reply = {"result": {"content": [{"type": "text", "text": "plain words"}]}}
        monkeypatch.setattr(nb, "cr_rpc", lambda *args: (reply, None))
        assert nb.cr_call_tool(session, "any", {})["data"] == {"raw_text": "plain words"}

    def test_a_bad_agent_key_is_a_401_and_not_a_json_rpc_reply(self, nb, world, session):
        world.agent_keys.clear()
        with pytest.raises(nb.ServiceError) as caught:
            nb.cr_list_tools(session)
        assert caught.value.status == 401 and "invalid agent key" in str(caught.value)

    def test_a_400_carrying_a_json_rpc_error_is_read_as_one(self, nb, world, session, monkeypatch):
        error = {"jsonrpc": "2.0", "id": 2, "error": {"code": -32600, "message": "bad request"}}
        monkeypatch.setattr(world, "mcp", lambda request, body: world.reply(request, 400, error))
        found = nb.cr_call_tool(session, "get_customer_by_id", {"id": "C001"})
        assert (found["outcome"], found["code"], found["message"]) == ("rejected", -32600, "bad request")

    def test_a_reply_that_is_not_json_rpc_is_an_error(self, nb, world, session, monkeypatch):
        monkeypatch.setattr(world, "mcp", lambda request, body: world.reply(request, 200, {"hello": 1}))
        with pytest.raises(nb.ServiceError, match="did not answer in JSON-RPC"):
            nb.cr_list_tools(session)

    def test_a_server_error_is_an_error(self, nb, world, session):
        world.forced["mcp"] = 500
        with pytest.raises(nb.ServiceError, match="500"):
            nb.cr_list_tools(session)

    def test_a_result_that_is_not_an_object_is_not_a_json_rpc_reply(self, nb, world, session, monkeypatch):
        monkeypatch.setattr(world, "mcp", lambda request, body: world.reply(request, 200, {"result": "yes"}))
        with pytest.raises(nb.ServiceError, match="did not answer in JSON-RPC"):
            nb.cr_list_tools(session)

    def test_tools_that_are_not_a_list_of_named_tools_are_a_message_not_a_key_error(
        self, nb, world, session, monkeypatch
    ):
        for tools in ({"a": 1}, [{"description": "no name"}], ["get"]):
            reply = {"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}
            monkeypatch.setattr(world, "mcp", lambda request, body: world.reply(request, 200, reply))
            with pytest.raises(nb.ServiceError, match="list of named tools"):
                nb.cr_list_tools(session)

    def test_the_mcp_error_object_of_tools_list_is_raised_with_its_code(self, nb, world, session, monkeypatch):
        error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no such method"}}
        monkeypatch.setattr(world, "mcp", lambda request, body: world.reply(request, 200, error))
        with pytest.raises(nb.ServiceError, match="-32601: no such method"):
            nb.cr_list_tools(session)


# ---- the tool explorer: forms made from schemas ------------------------------------------------------------------
SCHEMA = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {"type": "string", "enum": ["a", "b"]},
        "flag": {"type": "boolean"},
        "n": {"type": "integer", "minimum": 1, "maximum": 9},
        "x": {"type": ["number", "null"]},
        "rows": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": ["p", "q"]},
                    "value": {"type": "string", "description": "A code such as Z9"},
                },
            },
        },
        "where": {"type": "object", "properties": {"k": {"type": "string", "default": "kk"}}},
        "note": {"type": "string"},
    },
}


class TestForm:
    def test_an_example_is_the_first_choice_a_default_or_a_value_of_the_right_type(self, nb):
        assert nb.cr_example({"enum": ["gold", "silver"]}) == "gold"
        assert nb.cr_example({"enum": ["any", "all"], "default": "all"}) == "all"
        assert nb.cr_example({"enum": ["any", "all"], "default": "some"}) == "any"  # not a choice
        assert nb.cr_example({"type": "integer", "default": 10}) == 10
        assert nb.cr_example({"type": "integer", "minimum": 3}) == 3
        assert nb.cr_example({"type": "boolean"}) is False
        assert nb.cr_example({"type": "string", "description": "Order ID such as O1001"}) == "O1001"
        assert nb.cr_example({"type": "string", "default": ""}) == "C001"  # a blank is no example
        assert nb.cr_example({"type": ["null", "integer"]}) == 0
        assert nb.cr_example(SCHEMA["properties"]["rows"]) == [{"field": "p", "value": "Z9"}] * 2
        assert nb.cr_example({}) == "C001"

    def test_each_kind_of_argument_gets_its_own_control(self, nb):
        made = {
            name: type(nb.cr_widget(name, prop, name in SCHEMA["required"])).__name__
            for name, prop in SCHEMA["properties"].items()
        }
        assert made == {
            "status": "dropdown", "flag": "checkbox", "n": "number", "x": "number",
            "rows": "text_area", "where": "text_area", "note": "text",
        }  # fmt: skip

    def test_a_required_argument_is_starred_and_the_form_as_it_stands_sends_only_what_was_chosen(self, nb):
        required = nb.cr_widget("status", SCHEMA["properties"]["status"], True)
        assert "status *" in words(required.text)
        assert "status *" not in words(nb.cr_widget("note", SCHEMA["properties"]["note"], False).text)
        form = nb.mo.ui.dictionary(
            {n: nb.cr_widget(n, p, n in SCHEMA["required"]) for n, p in SCHEMA["properties"].items()}
        )
        # the required menu starts at its first choice and a checkbox cannot be empty; every other
        # optional argument starts empty and stays out of the call
        assert nb.cr_arguments(SCHEMA, form.value) == {"status": "a", "flag": False}

    def test_a_required_argument_or_one_with_a_default_starts_at_its_example(self, nb):
        schema = {
            "required": ["query", "min_value", "kind", "rows"],
            "properties": {
                "query": {"type": "string", "description": "Words such as boots"},
                "min_value": {"type": "number", "minimum": 5},
                "kind": {"enum": ["a", "b"]},
                "rows": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 10, "minimum": 1},
                "mode": {"type": "string", "enum": ["any", "all"], "default": "all"},
                "note": {"type": "string", "default": "hello"},
            },
        }
        form = nb.mo.ui.dictionary(
            {n: nb.cr_widget(n, p, n in schema["required"]) for n, p in schema["properties"].items()}
        )
        assert nb.cr_arguments(schema, form.value) == {
            "query": "boots", "min_value": 5, "kind": "a", "rows": ["C001"], "limit": 10,
            "mode": "all", "note": "hello",
        }  # fmt: skip

    def test_an_optional_box_shows_the_example_as_a_hint_and_can_be_filled_in(self, nb):
        rows = SCHEMA["properties"]["rows"]
        box = nb.cr_widget("rows", rows, False)
        assert box.value == "" and "field" in box._component_args["placeholder"]
        filled = nb.cr_arguments(SCHEMA, {"status": "a", "rows": '[{"field": "q"}]'})
        assert filled["rows"] == [{"field": "q"}]
        note = nb.cr_widget("note", SCHEMA["properties"]["note"], False)
        assert note._component_args["placeholder"] == "C001" and note.value == ""
        assert nb.cr_widget("x", SCHEMA["properties"]["x"], False).value is None

    def test_a_menu_of_numbers_keeps_them_numbers(self, nb):
        menu = nb.cr_widget("size", {"type": "integer", "enum": [10, 25]}, True)
        assert menu.value == 10 and nb.cr_arguments({"properties": {"size": {"type": "integer"}}}, {"size": 25.0}) == {"size": 25}

    def test_blank_optional_text_a_cleared_number_and_an_unchosen_menu_are_left_out(self, nb):
        menu = {"type": "string", "enum": ["a"]}
        schema = {**SCHEMA, "properties": {**SCHEMA["properties"], "mode": menu}}
        values = {"status": "b", "note": "  ", "n": None, "where": "", "mode": None}
        assert nb.cr_arguments(schema, values) == {"status": "b"}

    def test_a_blank_required_text_is_sent_as_it_is_for_the_service_to_refuse(self, nb):
        schema = {"required": ["id"], "properties": {"id": {"type": "string"}}}
        assert nb.cr_arguments(schema, {"id": ""}) == {"id": ""}

    def test_json_that_does_not_parse_says_which_box_it_was(self, nb):
        with pytest.raises(ValueError, match="rows is not valid JSON"):
            nb.cr_arguments(SCHEMA, {"status": "a", "rows": "[{"})

    def test_a_tool_that_takes_nothing_makes_an_empty_form(self, nb):
        assert nb.mo.ui.dictionary({}).value == {} and nb.cr_arguments({"type": "object"}, {}) == {}

    def test_enums_are_found_in_properties_and_in_array_items_but_not_at_the_root(self, nb):
        found = [(path, choices) for path, choices in nb.cr_enums(SCHEMA)]
        assert found == [(("status",), ["a", "b"]), (("rows", 0, "field"), ["p", "q"])]
        assert list(nb.cr_enums({"enum": ["root"]})) == []


TOOL_GET = {
    "name": "get_thing", "description": "Get one",
    "inputSchema": {"type": "object", "required": ["id"], "additionalProperties": False,
                    "properties": {"id": {"type": "string"}}},
}  # fmt: skip
TOOL_MENU = {
    "name": "filter_thing", "description": "Filter",
    "inputSchema": {"type": "object", "required": ["value"],
                    "properties": {"value": {"type": "string", "enum": ["red", "blue"]}}},
}  # fmt: skip
TOOL_OPEN = {
    "name": "search_thing", "description": "Search",
    "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
}  # fmt: skip


class TestBreakIt:
    def test_a_value_outside_a_menu_comes_first_wherever_the_menu_is(self, nb):
        plan = nb.cr_break_plan([TOOL_GET, TOOL_MENU, TOOL_OPEN], "get_thing")
        assert plan["tool"] == "filter_thing" and plan["arguments"] == {"value": "not-a-choice"}
        assert "`value`" in plan["how"] and "2 choices" in plan["how"] and "filter_thing" in plan["how"]

    def test_the_selected_tool_is_tried_first(self, nb):
        other = {**TOOL_MENU, "name": "aaa_filter"}
        assert nb.cr_break_plan([other, TOOL_MENU], "filter_thing")["tool"] == "filter_thing"
        assert nb.cr_break_plan([other, TOOL_MENU], "aaa_filter")["tool"] == "aaa_filter"

    def test_a_menu_inside_an_array_is_found_and_the_rest_of_the_example_stays_valid(self, nb):
        tool = {"name": "filter_x", "inputSchema": {"type": "object", "properties": {
            "note": {"type": "string"},
            "conditions": {"type": "array", "items": {"type": "object", "properties": {
                "field": {"type": "string", "enum": ["a", "b"]}, "value": {"type": "string"}}}},
        }}}  # fmt: skip
        plan = nb.cr_break_plan([tool], "filter_x")
        assert plan["arguments"] == {"conditions": [{"field": "not-a-choice", "value": "C001"}]}
        assert "conditions.0.field" in plan["how"]

    # count_customer as the live service listed it on 2026-09-30: `function` and `field` are tied
    # by an if/then/else, so a bad `field` is rejected as "validating /then: not: ...", not as a menu.
    LIVE_COUNT = {"name": "count_customer", "inputSchema": {
        "additionalProperties": False, "type": "object", "required": ["function"],
        "if": {"properties": {"function": {"const": "count"}}, "required": ["function"]},
        "then": {"not": {"required": ["field"]}}, "else": {"required": ["field"]},
        "properties": {
            "field": {"type": "string", "enum": ["lifetime_value", "region", "tier"]},
            "function": {"type": "string", "enum": ["count", "count_distinct"]},
            "group_by": {"type": "string", "enum": ["region", "tier"]},
            "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
        },
    }}  # fmt: skip

    def test_a_menu_that_no_rule_of_the_schema_mentions_is_broken_before_one_that_one_does(self, nb):
        plan = nb.cr_break_plan([self.LIVE_COUNT], "get_customer_by_id")
        assert plan["arguments"] == {"function": "count", "group_by": "not-a-choice"}
        assert "`group_by`" in plan["how"] and "2 choices" in plan["how"]

    def test_the_names_the_rules_mention_are_the_properties_and_the_required_ones(self, nb):
        assert nb.cr_ruled(self.LIVE_COUNT["inputSchema"]) == {"function", "field"}
        assert nb.cr_ruled({"properties": {"a": {"enum": ["x"]}}, "required": ["a"]}) == set()
        assert nb.cr_ruled({}) == set()

    def test_when_every_menu_is_ruled_one_of_them_is_still_broken(self, nb):
        tool = {"name": "t", "inputSchema": {
            "type": "object", "required": ["mode"],
            "if": {"properties": {"mode": {"const": "a"}}}, "then": {"required": ["kind"]},
            "properties": {"mode": {"type": "string", "enum": ["a", "b"]},
                           "kind": {"type": "string", "enum": ["x", "y"]}}}}  # fmt: skip
        plan = nb.cr_break_plan([tool], "t")
        assert plan is not None and "not-a-choice" in plan["arguments"].values()

    def test_a_menu_on_an_optional_argument_is_added_to_the_required_ones(self, nb):
        tool = {"name": "t", "inputSchema": {"type": "object", "required": ["q"], "properties": {
            "q": {"type": "string"}, "mode": {"type": "string", "enum": ["any", "all"]}}}}  # fmt: skip
        assert nb.cr_break_plan([tool], "t")["arguments"] == {"q": "C001", "mode": "not-a-choice"}

    def test_without_a_menu_an_argument_nobody_declared_goes_to_a_strict_tool(self, nb):
        plan = nb.cr_break_plan([TOOL_OPEN, TOOL_GET], "search_thing")
        assert plan["tool"] == "get_thing"
        assert plan["arguments"] == {"id": "C001", "not_an_argument": "x"}
        assert "does not declare" in plan["how"]

    def test_without_either_there_is_nothing_to_break_and_the_button_hides(self, nb):
        assert nb.cr_break_plan([TOOL_OPEN], "search_thing") is None
        assert nb.cr_break_plan([], "x") is None

    @BOTH
    def test_whatever_it_builds_the_service_refuses(self, nb, world, session, catalog):
        for selected in (t["name"] for t in catalog):
            plan = nb.cr_break_plan(catalog, selected)
            found = nb.cr_call_tool(session, plan["tool"], plan["arguments"])
            assert found["outcome"] == "rejected" and found["code"] == -32602, (selected, plan)

    def as_it_stands(self, nb, session, tool):
        """(arguments, outcome) of running a tool's form untouched."""
        schema = tool["inputSchema"]
        required = schema.get("required") or []
        form = nb.mo.ui.dictionary(
            {n: nb.cr_widget(n, p, n in required) for n, p in schema.get("properties", {}).items()}
        )
        arguments = nb.cr_arguments(schema, form.value)
        return arguments, nb.cr_call_tool(session, tool["name"], arguments)

    @BOTH
    def test_a_form_left_as_it_stands_is_accepted_for_every_tool_of_both_surfaces(
        self, nb, world, session, catalog
    ):
        for tool in catalog:
            _, found = self.as_it_stands(nb, session, tool)
            assert found["outcome"] in ("ok", "tool_error"), (tool["name"], found["message"])

    @BOTH
    def test_a_form_left_as_it_stands_finds_rows_wherever_nothing_needs_typing(
        self, nb, world, session, catalog
    ):
        """No 0-to-0 range, no made-up filter: a tool with nothing required finds everything."""
        typed = {"search_customer_by_text", "search_product_by_text", "filter_order_by_sku",
                 "filter_product_by_category"}  # a required free-text value: an id fits only some
        ran = {t["name"]: self.as_it_stands(nb, session, t) for t in catalog}
        for name, (arguments, found) in ran.items():
            data = found["data"] or {}
            if name in typed or "total_count" not in data:
                continue
            assert data["total_count"] > 0, (name, arguments)
            assert not {"min_value", "max_value", "tag_conditions", "text_query"} & set(arguments)
        if world.style == "classic":
            assert ran["find_customer_by_lifetime_value_range"][1]["data"]["total_count"] == 6
            assert ran["find_order_by_total_range"][1]["data"]["total_count"] == 8
        else:
            assert ran["list_customer"][1]["data"]["total_count"] == 6
            assert ran["count_customer"][1]["data"]["value"] == 6
            # the unified filters refuse a call with no condition, as the live service did
            refused = ran["filter_customer"][1]
            assert refused["outcome"] == "tool_error"
            assert refused["message"] == "Error executing tool: at least one condition is required"


class TestPaging:
    @pytest.mark.parametrize("world", ["classic"], indirect=True)
    def test_classic_offers_the_filters_whose_argument_is_a_menu(self, nb, world, session, catalog):
        assert nb.cr_pageable(catalog) == {
            "filter_customer_by_region": {"value": "emea"},
            "filter_customer_by_tier": {"value": "bronze"},
            "filter_order_by_region": {"value": "emea"},
            "filter_order_by_status": {"value": "placed"},
        }

    def test_unified_offers_each_filter_with_a_condition_that_every_record_meets(
        self, nb, world, session, catalog
    ):
        def everything(field):
            return {"numeric_conditions": [{"field": field, "min_value": 0}]}

        assert nb.cr_pageable(catalog) == {
            "filter_customer": everything("lifetime_value"),
            "filter_order": everything("total"),
            "filter_product": everything("price"),
        }

    def test_the_unified_filter_refuses_the_bare_paging_call_the_live_service_refused(
        self, nb, world, session
    ):
        bare = nb.cr_call_tool(session, "filter_customer", {"limit": 2, "offset": 0})
        assert bare["outcome"] == "tool_error"
        assert bare["message"] == "Error executing tool: at least one condition is required"

    @BOTH
    def test_the_paging_call_the_cell_makes_is_accepted_by_every_pageable_tool_on_both_surfaces(
        self, nb, world, session, catalog
    ):
        pages = nb.cr_pageable(catalog)
        assert pages
        for name, arguments in pages.items():
            found = nb.cr_call_tool(session, name, {**arguments, "limit": 2, "offset": 0})
            assert found["outcome"] == "ok", (name, found["message"])
            data = found["data"]
            assert data["total_count"] > 0, name  # the condition keeps records, on either surface
            assert data["count"] == min(2, data["total_count"]), name
            assert data["has_more"] is (data["total_count"] > 2), name
            past = nb.cr_call_tool(session, name, {**arguments, "limit": 2, "offset": 100})
            assert past["data"]["results"] == [] and past["data"]["has_more"] is False, name

    def test_a_numeric_condition_from_zero_wins_and_names_a_field_from_the_tools_enum(self, nb):
        schema = {"properties": {
            "tag_conditions": {"type": "array", "items": {"type": "object", "properties": {
                "field": {"type": "string", "enum": ["region"]}, "value": {"type": "string"},
                "exclude": {"type": "boolean"}}}},
            "numeric_conditions": {"type": "array", "items": {"type": "object", "properties": {
                "field": {"type": "string", "enum": ["price", "weight"]},
                "min_value": {"type": "number"}, "max_value": {"type": "number"}}}},
            "limit": {"type": "number"},
        }}  # fmt: skip
        assert nb.cr_match_most(schema) == {
            "numeric_conditions": [{"field": "price", "min_value": 0}]
        }

    def test_without_a_numeric_field_a_tag_condition_takes_a_value_from_its_enum(self, nb):
        schema = {"properties": {"tag_conditions": {"type": "array", "items": {
            "type": "object", "properties": {
                "field": {"type": "string", "enum": ["tier", "region"]},
                "value": {"type": "string", "enum": ["gold", "silver"]},
                "exclude": {"type": "boolean"}}}}}}  # fmt: skip
        assert nb.cr_match_most(schema) == {"tag_conditions": [{"field": "tier", "value": "gold"}]}

    def test_a_tag_with_no_list_of_values_is_excluded_by_an_example_so_every_record_stays(self, nb):
        tags = {"type": "array", "items": {"type": "object", "properties": {
            "field": {"type": "string", "enum": ["tier"]}, "value": {"type": "string"},
            "exclude": {"type": "boolean"}}}}  # fmt: skip
        assert nb.cr_match_most({"properties": {"tag_conditions": tags}}) == {
            "tag_conditions": [{"field": "tier", "value": "C001", "exclude": True}]
        }
        plain = {**tags, "items": {"type": "object", "properties": {
            "field": {"type": "string", "enum": ["tier"]}, "value": {"type": "string"}}}}
        assert nb.cr_match_most({"properties": {"tag_conditions": plain}}) == {
            "tag_conditions": [{"field": "tier", "value": "C001"}]
        }

    def test_a_schema_with_no_conditions_needs_none(self, nb):
        assert nb.cr_match_most({"properties": {"limit": {"type": "number"}}}) == {}
        assert nb.cr_match_most({}) == {}
        odd = {"properties": {"things": {"type": "array", "items": {"type": "string"}}}}
        assert nb.cr_match_most(odd) == {}

    def test_a_tool_that_needs_free_text_is_not_offered(self, nb):
        assert nb.cr_pageable([{**TOOL_OPEN, "name": "filter_free"}]) == {}
        assert nb.cr_pageable([TOOL_MENU]) == {}  # it does not page

    @pytest.mark.parametrize("world", ["classic"], indirect=True)
    def test_limit_and_offset_move_the_window_and_the_totals_hold_still(self, nb, world, session):
        def page(limit, offset):
            args = {"value": "emea", "limit": limit, "offset": offset}
            return nb.cr_call_tool(session, "filter_order_by_region", args)["data"]

        first, second, past = page(3, 0), page(3, 3), page(3, 9)
        assert (first["count"], first["has_more"], first["total_count"]) == (3, True, 4)
        assert (second["count"], second["has_more"], second["total_count"]) == (1, False, 4)
        assert (past["count"], past["results"], past["total_count"]) == (0, [], 4)
        assert {r["id"] for r in first["results"]} | {r["id"] for r in second["results"]} == {
            f"{nb.CR_PREFIX}order:O100{n}" for n in (1, 2, 5, 7)
        }

    def test_limit_and_offset_move_the_window_on_the_unified_surface_too(self, nb, world, session):
        def page(limit, offset):
            emea = {"tag_conditions": [{"field": "region", "value": "emea"}]}
            args = {**emea, "limit": limit, "offset": offset}
            return nb.cr_call_tool(session, "filter_order", args)["data"]

        first, second, past = page(3, 0), page(3, 3), page(3, 9)
        assert (first["count"], first["has_more"], first["total_count"]) == (3, True, 4)
        assert (second["count"], second["has_more"], second["total_count"]) == (1, False, 4)
        assert (past["count"], past["results"], past["total_count"]) == (0, [], 4)

    @pytest.mark.parametrize("world", ["classic"], indirect=True)
    def test_the_view_shows_the_totals_and_says_when_there_is_no_provenance_stamp(self, nb, world, session):
        found = nb.cr_call_tool(session, "filter_order_by_region", {"value": "emea", "limit": 2})
        view = nb.cr_outcome_view(found)
        assert "total_count" in view.text and "has_more" in view.text and "returned" in view.text
        assert "These records carry no _retrieval stamp" in words(view.text)

    def test_the_view_shows_the_retrieval_stamp_when_the_service_sends_one(
        self, nb, world, session, catalog
    ):
        arguments = {**nb.cr_pageable(catalog)["filter_order"], "limit": 2}
        found = nb.cr_call_tool(session, "filter_order", arguments)
        assert found["data"]["results"][0]["_retrieval"]["source"] == session["surface"]
        shown = words(nb.cr_outcome_view(found).text)
        assert "source_version" in shown and "carry no _retrieval stamp" not in shown


class TestViews:
    def test_a_rejection_shows_its_code_and_message_verbatim(self, nb, world, session):
        found = nb.cr_call_tool(session, "get_customer_by_id", {"id": "C001", "x": 1})
        odd = {**found, "message": "bad *x* and _y_ and [z]"}  # markdown must not touch it
        shown = words(nb.cr_outcome_view(odd).text)
        assert "Rejected, code -32602: bad *x* and _y_ and [z]" in shown

    def test_the_code_block_cannot_be_closed_from_inside(self, nb):
        assert "```" not in nb.code_block("a ``` b")[3:-3]

    def test_each_outcome_reads_differently(self, nb, world, session, catalog):
        ok = nb.cr_call_tool(session, "get_customer_by_id", {"id": "C001"})
        failed = nb.cr_call_tool(session, "get_customer_by_id", {"id": "C999"})
        rejected = nb.cr_call_tool(session, "get_customer_by_id", {"id": "C001", "x": 1})
        assert "Ada Lovelace" in words(nb.cr_outcome_view(ok).text)
        assert "The tool ran and reported an error (isError):" in words(nb.cr_outcome_view(failed).text)
        assert "key C999 not found" in words(nb.cr_outcome_view(failed).text)
        text = words(nb.cr_outcome_view(rejected, broke=True).text)
        assert "Rejected, code -32602:" in text and "unexpected additional properties" in text
        assert "accepted it" not in text
        assert "The service accepted it" in words(nb.cr_outcome_view(ok, broke=True).text)

    def test_the_wire_view_under_a_call_is_the_real_json_rpc_with_the_key_masked(self, nb, world, session):
        found = nb.cr_call_tool(session, "get_customer_by_id", {"id": "C001"})
        shown = words(nb.cr_outcome_view(found).text)
        assert "tools/call" in shown and "jsonrpc" in shown and "$CTX_AGENT_KEY" in shown
        assert next(iter(world.agent_keys)) not in shown

    def test_replies_are_shown_as_the_most_natural_table(self, nb):
        assert "count = 7" in words(nb.cr_data_view({"function": "count", "value": 7}).text)
        groups = nb.cr_data_view({"groups": [{"value": 2, "group_value": "gold"}], "has_more": False})
        assert "gold" in groups.text  # a table keeps its rows in an attribute
        ids = nb.cr_data_view({"ids": ["a:1", "b:2"], "count": 2}).text
        assert "a:1" in ids and "b:2" in ids
        one = nb.cr_data_view({"name": "Ada", "nested": {"k": [1, 2]}}).text
        assert "Ada" in one and "nested" in one
        assert "No rows" in words(nb.cr_data_view({"results": [], "total_count": 0}).text)
        assert "[ 1, 2 ]" in words(nb.cr_data_view([1, 2]).text)

    def test_the_catalogue_lists_names_descriptions_and_starred_required_arguments(self, nb):
        rows = nb.cr_catalogue_rows([TOOL_GET, TOOL_OPEN, {"name": "ping"}])
        assert rows == [
            {"tool": "get_thing", "description": "Get one", "arguments": "id*"},
            {"tool": "search_thing", "description": "Search", "arguments": "q"},
            {"tool": "ping", "description": "", "arguments": ""},
        ]


# ---- clean up ---------------------------------------------------------------------------------------------------
class TestCleanup:
    def clean(self, nb, conn):
        return nb.cr_cleanup(conn["admin"], conn["api"], conn["url"])

    def test_it_deletes_the_surface_then_unlinks_the_keys_then_drops_the_indexes(self, nb, world, conn, session):
        world.events.clear()
        done = self.clean(nb, conn)
        assert done == {
            "surfaces": 1, "deleted": 1, "keys": 20, "removed": 20, "indexes": 3, "dropped": 3,
            "gone": 0, "left": [], "stopped": "",
        }  # fmt: skip
        assert world.surfaces == {} and world.docs == {} and world.indexes == {}
        assert world.agent_keys == {}
        order = [e for e in world.events if e[0] == "http" or e[1] in ("UNLINK", "FT.DROPINDEX")]
        assert [e[1] if e[0] == "http" else e[1] for e in order] == (
            ["DELETE", "UNLINK"] + ["FT.DROPINDEX"] * 3
        )
        assert order[0][2].endswith(session["surface"])

    def test_the_keys_go_in_one_unlink_and_the_indexes_are_never_dropped_with_dd(self, nb, world, conn, session):
        world.sent.clear()
        self.clean(nb, conn)
        unlinks = [a for a in world.sent if txt(a[0]) == "UNLINK"]
        assert len(unlinks) == 1 and len(unlinks[0]) == 21
        drops = [a for a in world.sent if txt(a[0]) == "FT.DROPINDEX"]
        assert len(drops) == 3 and all(len(a) == 2 for a in drops)
        assert not {txt(a[0]) for a in world.sent} & {"SCAN", "KEYS", "DEL", "FLUSHALL", "FLUSHDB"}

    def test_the_ledger_ends_empty_and_a_second_clean_up_has_nothing_to_do(self, nb, world, conn, session):
        self.clean(nb, conn)
        for kind in ("surface", "agent_key", "key", "index"):
            assert nb.ledger_live("cr", kind, pending=True) == [], kind
        world.events.clear()
        world.sent.clear()
        again = self.clean(nb, conn)
        assert again == {
            "surfaces": 0, "deleted": 0, "keys": 0, "removed": 0, "indexes": 0, "dropped": 0,
            "gone": 0, "left": [], "stopped": "",
        }  # fmt: skip
        assert [e for e in world.events if e[0] == "http"] == [] and world.writes() == []
        assert "Nothing to clean up" in nb.cr_cleanup_report(again)

    def test_a_run_that_made_no_surface_never_contacts_the_database(self, nb, world, conn):
        world.sent.clear()
        world.events.clear()
        done = self.clean(nb, conn)
        assert done["surfaces"] == 0 and world.sent == [] and world.events == []
        assert "Nothing to clean up" in nb.cr_cleanup_report(done)

    def test_the_report_gives_the_counts(self, nb, world, conn, session):
        report = nb.cr_cleanup_report(self.clean(nb, conn))
        assert "Deleted <" not in report
        assert "Deleted **1** of 1 surfaces" in report and "unlinked **20** of 20 keys" in report
        assert "dropped **3** of 3 indexes" in report and "Left behind" not in report

    def test_a_surface_the_console_already_deleted_counts_as_done(self, nb, world, conn, session):
        del world.surfaces[session["surface"]]
        done = self.clean(nb, conn)
        assert done["surfaces"] == 1 and done["deleted"] == 0 and done["removed"] == 20
        assert nb.ledger_live("cr", "surface", pending=True) == []

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_an_index_that_is_already_gone_counts_as_done_in_either_wording(
        self, nb, world, conn, session, wording
    ):
        world.wording = wording
        del world.indexes[next(iter(world.indexes))]
        done = self.clean(nb, conn)
        assert (done["indexes"], done["dropped"], done["gone"], done["left"]) == (3, 2, 1, [])
        assert done["stopped"] == "" and world.surfaces == {} and world.docs == {}
        assert nb.ledger_live("cr", "index", pending=True) == []

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_the_indexes_the_service_dropped_with_the_surface_are_gone_and_not_a_stop(
        self, nb, world, conn, session, wording
    ):
        """Redis 8.6.2, live: after DELETE of the surface every index was already gone."""
        world.wording = wording
        world.drops_indexes = True
        world.sent.clear()
        done = self.clean(nb, conn)
        assert done == {
            "surfaces": 1, "deleted": 1, "keys": 20, "removed": 20, "indexes": 3, "dropped": 0,
            "gone": 3, "left": [], "stopped": "",
        }  # fmt: skip
        assert world.docs == {} and world.indexes == {}
        assert "FT.DROPINDEX" not in world.names()
        for kind in ("surface", "agent_key", "key", "index"):
            assert nb.ledger_live("cr", kind, pending=True) == [], kind

    def test_the_report_says_the_service_dropping_its_indexes_with_the_surface_is_expected(self, nb):
        done = {"surfaces": 1, "deleted": 1, "keys": 20, "removed": 20, "indexes": 3, "dropped": 0,
                "gone": 3, "left": [], "stopped": ""}  # fmt: skip
        report = nb.cr_cleanup_report(done)
        assert "dropped **0** of 3 indexes" in report
        assert "**3** of the indexes were already gone" in report and "expected" in report
        assert "drops the indexes it built together with the surface" in report
        assert "stopped" not in report and "Left behind" not in report
        assert "already gone" not in nb.cr_cleanup_report({**done, "gone": 0, "dropped": 3})

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_an_index_that_vanishes_between_the_look_and_the_drop_is_gone_not_a_stop(
        self, nb, world, conn, session, wording, monkeypatch
    ):
        world.wording = wording
        original = world.ft_dropindex

        def racing(name, *rest):
            original(name, *rest)  # someone else's drop wins ...
            return original(name, *rest)  # ... and this one finds nothing

        monkeypatch.setitem(world.__dict__, "ft_dropindex", racing)
        done = self.clean(nb, conn)
        assert (done["dropped"], done["gone"], done["stopped"]) == (0, 3, "")
        assert nb.ledger_live("cr", "index", pending=True) == []

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_an_index_listed_and_gone_before_it_is_read_is_skipped_when_learning(
        self, nb, world, conn, session, wording, monkeypatch
    ):
        world.wording = wording
        world.add_index("idx:late", nb.CR_PREFIX + "extra:")
        listed = world.ft_list

        def stale_list():
            names = listed()
            world.indexes.pop("idx:late", None)  # dropped since the listing
            return names

        monkeypatch.setitem(world.__dict__, "ft_list", stale_list)
        found = {row["name"] for row in nb.cr_learn_indexes(conn["redis"])}
        assert "idx:late" not in nb.ledger_live("cr", "index") and found

    def test_an_index_that_no_longer_matches_the_prefix_is_left_behind_and_reported(self, nb, world, conn, session):
        name = next(iter(world.indexes))
        world.indexes[name]["prefixes"] = ["prod:"]
        done = self.clean(nb, conn)
        assert done["dropped"] == 2 and done["left"] == [name] and name in world.indexes
        report = nb.cr_cleanup_report(done)
        assert "Left behind" in report and name in report
        assert nb.ledger_live("cr", "index", pending=True) == [name]

    def test_an_index_under_the_run_prefix_goes_whether_or_not_the_ledger_names_it(
        self, nb, world, conn, session
    ):
        world.add_index("idx:late", nb.CR_PREFIX + "extra:")  # built after the launch had looked
        column_zero = nb.RS_PREFIX + "idx"
        world.add_index(column_zero, nb.RS_PREFIX + "doc:")  # this run's, but not this column's
        world.add_index("idx:elsewhere", "prod:")
        world.indexes["idx:mixed"] = {"prefixes": [nb.CR_PREFIX + "a:", "prod:"], "fields": []}
        assert "idx:late" not in nb.ledger_live("cr", "index")
        done = self.clean(nb, conn)
        assert (done["indexes"], done["dropped"], done["left"]) == (4, 4, [])
        assert set(world.indexes) == {column_zero, "idx:elsewhere", "idx:mixed"}
        assert "idx:mixed" not in nb.ledger_live("cr", "index", pending=True)

    def test_a_lost_create_is_cleaned_up_with_the_indexes_the_service_built_and_the_ledger_never_saw(
        self, nb, world, conn, model
    ):
        world.create_reply_lost = True
        with pytest.raises(nb.ServiceError):
            nb.cr_launch(conn, model)
        assert nb.ledger_live("cr", "index") == [] and len(world.indexes) == 3
        done = self.clean(nb, conn)
        assert (done["deleted"], done["indexes"], done["dropped"]) == (1, 3, 3)
        assert world.surfaces == {} and world.indexes == {}
        assert "dropped **3** of 3 indexes" in nb.cr_cleanup_report(done)

    def test_the_surface_goes_even_when_the_database_cannot_be_reached(self, nb, world, conn, session):
        world.down = True
        done = self.clean(nb, conn)
        assert world.surfaces == {} and "Refused" in done["stopped"] and "db.example" in done["stopped"]
        assert (done["deleted"], done["removed"], done["dropped"]) == (1, 0, 0)
        assert (done["keys"], done["indexes"]) == (20, 3)
        assert nb.ledger_live("cr", "surface", pending=True) == []  # the delete stands
        assert len(nb.ledger_live("cr", "key", pending=True)) == 20  # and the rest is still owed
        assert len(nb.ledger_live("cr", "index", pending=True)) == 3
        report = nb.cr_cleanup_report(done)
        assert "Deleted **1** of 1 surfaces" in report and "The database steps stopped" in report
        assert "press Clean up again" in report and "fake-pw-9f3a" not in report
        world.down = False
        again = self.clean(nb, conn)
        assert (again["deleted"], again["removed"], again["dropped"], again["stopped"]) == (0, 20, 3, "")
        assert world.docs == {} and world.indexes == {}

    def test_an_unreachable_database_with_nothing_owed_is_still_a_message(self, nb, world, conn, session):
        self.clean(nb, conn)
        world.down = True
        done = self.clean(nb, conn)
        assert "Refused" in done["stopped"] and "The database steps stopped" in nb.cr_cleanup_report(done)

    def test_a_404_is_believed_only_if_the_listing_no_longer_shows_the_surface(
        self, nb, world, conn, session
    ):
        world.forced["delete"] = 404  # every DELETE says so, and the surface is still listed
        with pytest.raises(nb.ServiceError, match="answers 404 to deleting cs_0001 but still lists it"):
            self.clean(nb, conn)
        assert nb.ledger_live("cr", "surface") == [session["surface"]] and world.docs

    def test_a_404_from_a_wrong_admin_address_leaves_the_surface_on_the_ledger(
        self, nb, world, conn, session
    ):
        world.forced.update(delete=404, list=404)  # nothing at this address answers properly
        with pytest.raises(nb.ServiceError, match="cs_0001 was not found at cloud.redis.io.*Admin API address"):
            self.clean(nb, conn)
        assert nb.ledger_live("cr", "surface") == [session["surface"]] and session["surface"] in world.surfaces

    def test_only_what_the_ledger_names_is_touched(self, nb, world, conn, session):
        elsewhere = world.add_surface("iris-basics-feedcafe", run="feedcafe", age_h=9)
        world.docs["prod:customer:1"] = {"name": "not ours"}
        world.add_index("idx:other", "prod:")
        world.add_index("idx:another-run", "irisnb_feedcafe_cr:customer:")
        self.clean(nb, conn)
        assert elsewhere in world.surfaces and "prod:customer:1" in world.docs
        assert {"idx:other", "idx:another-run"} <= set(world.indexes)

    def test_a_ledger_line_outside_the_prefix_is_refused_by_the_guard(self, nb, world, conn, session):
        world.docs["prod:customer:1"] = {}
        nb.ledger_add("cr", "key", "prod:customer:1")
        with pytest.raises(nb.Denied, match="outside"):
            self.clean(nb, conn)
        assert "prod:customer:1" in world.docs

    def test_it_deletes_the_surfaces_the_ledger_names_and_no_other(self, nb, world, conn):
        named = world.add_surface("iris-basics-feedcafe", run="feedcafe", age_h=9)
        nb.ledger_add("cr", "surface", named, "created")  # the ledger is what it trusts
        other = world.add_surface("radish-bank-workshop", run=None, created_by="somebody")
        old = world.add_surface("iris-basics-deadbeef", run="deadbeef", age_h=9)  # an orphan
        self.clean(nb, conn)
        assert named not in world.surfaces and {other, old} <= set(world.surfaces)

    def test_a_create_that_never_reported_back_is_found_by_name_and_deleted(self, nb, world, conn, model):
        world.create_reply_lost = True
        with pytest.raises(nb.ServiceError):
            nb.cr_launch(conn, model)
        done = self.clean(nb, conn)
        assert world.surfaces == {} and done["surfaces"] == 1 and done["deleted"] == 1
        assert nb.ledger_live("cr", "surface", pending=True) == []

    def test_an_intent_for_a_create_that_never_happened_is_settled_without_deleting_anything(
        self, nb, world, conn
    ):
        bystander = world.add_surface("radish-bank-workshop", run=None, created_by="somebody")
        nb.ledger_add("cr", "surface", nb.RUN_TAG, "intent")
        done = self.clean(nb, conn)
        assert done["surfaces"] == 0 and bystander in world.surfaces
        assert nb.ledger_live("cr", "surface", pending=True) == []

    def test_a_failing_service_stops_it_and_the_ledger_still_names_everything_for_a_retry(
        self, nb, world, conn, session
    ):
        world.forced["delete"] = 500
        with pytest.raises(nb.ServiceError, match="500"):
            self.clean(nb, conn)
        assert world.docs and world.indexes  # nothing after the failed delete ran
        assert nb.ledger_live("cr", "surface") == [session["surface"]]
        del world.forced["delete"]
        assert self.clean(nb, conn)["dropped"] == 3 and world.docs == {}

    def test_a_fresh_kernel_with_the_same_run_id_can_clean_up_what_another_made(
        self, nb, world, load_fresh
    ):
        first = load_fresh(IRIS_BASICS_RUN=RUN)
        first.WRITE_RATE = 1_000_000
        made = first.cr_launch(
            first.cr_probe(first.Secret(ADMIN_KEY), first.Secret(URL), "", ""),
            first.cr_build_models(first.CR_PREFIX),
        )
        assert made["surface"] in world.surfaces
        second = load_fresh(IRIS_BASICS_RUN=RUN)
        second.WRITE_RATE = 1_000_000
        conn = second.cr_probe(second.Secret(ADMIN_KEY), second.Secret(URL), "", "")
        done = second.cr_cleanup(conn["admin"], conn["api"], conn["url"])
        assert done["deleted"] == 1 and done["removed"] == 20 and done["dropped"] == 3
        assert world.surfaces == {} and world.docs == {} and world.indexes == {}


# ---- the notebook's shape ------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def column(nb_file):
    """Column 2's items in file order: (kind, node)."""
    source = nb_file.read_text(encoding="utf-8")
    return [(i.kind, i.node) for i in nbast.column_items(source, 2)]


@pytest.fixture(scope="module")
def cells(column):
    return [node for kind, node in column if kind == "cell"]


def ui_elements(cells, *kinds):
    """{global name: the mo.ui call that builds it} for the given kinds, across the cells."""
    return {
        stmt.targets[0].id: stmt.value
        for cell in cells
        for stmt in ast.walk(cell)
        if isinstance(stmt, ast.Assign)
        and isinstance(stmt.value, ast.Call)
        and nbast.call_name(stmt.value) in kinds
    }


class TestShape:
    def test_the_column_runs_in_the_planned_order(self, cells):
        # config, connect, orphans, then each core cell as controls / fetch / render, the wow,
        # cleanup and the deep dive
        markers = [
            "cr_admin_key", "cr_conn", "cr_orphan_drop", "cr_field", "cr_index", "cr_model",
            "cr_create", "cr_session", "cr_catalog", "cr_seen", "cr_page_tool", "cr_page",
            "cr_tool", "cr_form", "cr_cleanup_btn", "cr_deep",
        ]  # fmt: skip
        at = [next(i for i, c in enumerate(cells) if m in nbast.defs(c)) for m in markers]
        assert at == sorted(at) and len(set(at)) == len(markers)
        for controls, fetch in (("cr_page_tool", "cr_page"), ("cr_tool", "cr_form")):
            assert at[markers.index(fetch)] == at[markers.index(controls)] + 1

    def test_the_helpers_come_last_all_hidden_and_all_prefixed(self, column, nb_file):
        kinds = [kind for kind, _ in column]
        assert set(kinds[kinds.index("function") :]) == {"function"}
        functions = nbast.column_items(nb_file.read_text("utf-8"), 2)
        assert all(i.hide_code is True for i in functions if i.kind == "function")
        assert all(node.name.startswith("cr_") for kind, node in column if kind == "function")

    def test_every_global_a_cell_returns_has_the_column_prefix(self, cells):
        for cell in cells:
            assert all(n.startswith("cr_") for n in nbast.defs(cell)), nbast.defs(cell)

    def test_the_cells_that_need_a_connection_or_a_session_stop_when_there_is_none(self, cells):
        first = [ast.unparse(c.body[0]) for c in cells if c.body]
        assert first.count("mo.stop(cr_conn is None, None)") == 2  # the orphans panel, Create
        assert first.count("mo.stop(cr_session is None, None)") == 2  # the tool list, the peek

    def test_python_3_14_stops_the_model_cells_with_a_message(self, cells):
        (controls,) = [c for c in cells if "cr_field" in nbast.defs(c)]
        guard = ast.unparse(controls.body[0])
        assert guard.startswith("mo.stop(sys.version_info >= (3, 14)") and "ContextModel" in guard

    def test_only_the_demo_and_cleanup_buttons_can_autorun(self, cells):
        pressed = {
            (c.args[0].id, ast.unparse(nbast.keyword(c, "stage") or ast.Constant("demo")))
            for cell in cells
            for c in nbast.calls(cell, "pressed")
        }
        assert pressed == {
            ("cr_connect", "'demo'"), ("cr_create", "'demo'"), ("cr_run", "'demo'"),
            ("cr_break", "'demo'"), ("cr_cleanup_btn", "'cleanup'"),
        }  # fmt: skip

    def test_the_orphan_reset_and_model_buttons_are_read_with_a_plain_value(self, cells):
        source = "\n".join(ast.unparse(c) for c in cells)
        for button in ("cr_orphan_drop", "cr_reset_btn", "cr_model_reset", "cr_keep"):
            assert f"{button}.value" in source and f"pressed({button}" not in source

    def test_every_run_button_in_the_column_is_accounted_for(self, cells):
        assert set(ui_elements(cells, "mo.ui.run_button")) == {
            "cr_connect", "cr_orphan_drop", "cr_keep", "cr_model_reset", "cr_create", "cr_run",
            "cr_break", "cr_cleanup_btn", "cr_reset_btn",
        }  # fmt: skip

    def test_the_four_presenter_notes_are_plain_say_and_expect_calls(self, cells):
        calls = [c for cell in cells for c in nbast.calls(cell, "notes")]
        assert len(calls) == 4  # the model, the data, paging and the wow
        assert all(len(c.args) == 2 and not c.keywords for c in calls)

    def test_a_cell_that_shows_notes_never_calls_the_service(self, cells):
        for cell in cells:
            reads = set(nbast.refs(cell))
            if nbast.calls(cell, "notes"):
                assert not reads & {"cr_conn", "cr_session"}, "a display cell must not be a fetch cell"

    def test_the_cleanup_cell_reads_only_config_and_its_own_buttons(self, cells):
        cleanup = [c for c in cells if "stage='cleanup'" in ast.unparse(c)]
        assert len(cleanup) == 1
        assert set(nbast.refs(cleanup[0])) == {
            "cr_admin_key", "cr_api_url", "cr_cleanup_btn", "cr_reset_btn", "cr_set_gen", "cr_url",
        }  # fmt: skip

    def test_every_text_box_starts_with_something_and_only_the_confirmation_may_not(self, cells):
        boxes = ui_elements(cells, "mo.ui.text", "mo.ui.text_area")
        assert set(boxes) == {"cr_api_url", "cr_mcp_url", "cr_orphan_confirm"}
        assert nbast.keyword(boxes["cr_orphan_confirm"], "value") is None

    def test_every_slider_waits_for_the_drag_to_end(self, cells):
        sliders = ui_elements(cells, "mo.ui.slider", "mo.ui.range_slider")
        assert set(sliders) == {"cr_limit", "cr_offset"}
        for name, call in sliders.items():
            assert ast.literal_eval(nbast.keyword(call, "debounce")) is True, name

    def test_the_config_cell_links_the_console_for_every_secret_and_warns(self, cells):
        text = "\n".join(ast.unparse(c) for c in nbast.config_panel(cells, "cr_admin_key"))
        for needed in (
            "cloud.redis.io", "Admin keys", "Generate key",
            "Public endpoint", "create-service",
            "only works after you", "Never on a shared service", "given your database password",
        ):  # fmt: skip
            assert needed in text, needed

    def test_the_consent_says_the_database_password_is_sent_to_the_hosted_service(self, cells):
        (consent,) = [c for c in cells if "cr_create" in nbast.defs(c)]
        text = " ".join(ast.unparse(consent).split())
        assert "carries your database password" in text and "keeps it to reach" in text
        assert "ledger" in text and "picks the surface up again" in text
        label = ast.unparse(nbast.keyword(ui_elements([consent], "mo.ui.run_button")["cr_create"], "label"))
        assert "Create the surface and load" in label and "records" in label

    def test_connect_calls_the_reset_token_and_shows_the_recorded_panel_when_unconfigured(self, cells):
        (connect,) = [c for c in cells if "cr_conn" in nbast.defs(c)]
        assert nbast.calls(connect, "cr_gen")  # a state getter registers only when it is called
        assert [ast.unparse(c.args[0]) for c in nbast.calls(connect, "recorded_live")] == ["'cr'"]

    def test_the_deep_dive_switch_follows_autorun(self, cells):
        (switch,) = [c for c in cells if "cr_deep" in nbast.defs(c)]
        assert "mo.ui.switch(value=AUTORUN == 'demo'" in ast.unparse(switch)

    def test_the_wow_starts_at_the_first_get_tool_and_needs_no_tool_name(self, cells, column):
        (controls,) = [c for c in cells if "cr_tool" in nbast.defs(c)]
        assert "startswith('get_')" in ast.unparse(controls)
        names = re.compile(r"^(get|filter|find|search|count|intersect|union|except|expand)_\w+")
        predictor = {"cr_unified_tools"}  # builds names from f-strings, which is its whole job
        for kind, node in column:
            if getattr(node, "name", "") in predictor:
                continue
            for const in ast.walk(node):
                if isinstance(const, ast.Constant) and isinstance(const.value, str):
                    assert not names.match(const.value), (node.name, const.value)

    def test_every_hosted_call_goes_through_http_call_as_cr_and_redis_through_connect_redis(self, column):
        found = 0
        for _, node in column:
            for call in nbast.calls(node, "http_call"):
                found += 1
                assert ast.unparse(call.args[0]) == "'cr'", ast.unparse(call)[:80]
        assert found >= 8
        source = "\n".join(ast.unparse(node) for _, node in column)
        assert "connect_redis(" in source and "Client(" not in source and "from_url" not in source

    def test_the_refusals_it_leans_on_are_the_setups_and_nothing_here_sends_a_put(self, column):
        source = "\n".join(ast.unparse(node) for _, node in column)
        assert not re.search(r"'PUT'|\"PUT\"", source) and "flush" not in source.lower()


# ---- the whole column, cell by cell, against the fake ------------------------------------------------------------------
class Btn:
    """Stands in for a mo.ui.run_button in app.run(defs=...)."""

    def __init__(self, value):
        self.value = value


ENV = {"CTX_ADMIN_KEY": ADMIN_KEY, "REDIS_URL": URL}


class TestWholeColumn:
    def run(self, load_fresh, stage, defs=None, **env):
        fresh = load_fresh(
            IRIS_BASICS_RUN=RUN, IRIS_BASICS_AUTORUN=stage, IRIS_BASICS_HEADLESS="1", **env
        )
        fresh.WRITE_RATE = 1_000_000  # pacing has its own tests; 20 expiries would take a second
        outputs, found = fresh.app.run(defs=defs)
        return fresh, texts(outputs), found

    def test_with_no_settings_the_config_the_offline_model_and_cleanup_render(self, load_fresh, world):
        outputs, defs = load_fresh().app.run()
        text = texts(outputs)
        assert "Context Retriever is not configured" in text
        assert "CTX_ADMIN_KEY" in text and "REDIS_URL" in text
        assert "From data model to tools" in text and "The whole exported data model" in text
        assert "21 tools, predicted with the unified naming" in text and "Order.status" in text
        assert text.count("classic") == 1 and "filter_customer_by_tier" in text  # one line, once
        # without settings the column still shows what a real run looked like, labelled as such
        assert "Recorded live on 2026-09-30" in text and "not a simulation" in text
        assert "get_customer_by_id" in text and "at least one condition is required" in text
        assert defs["cr_conn"] is None and "cr_session" not in defs
        for later in ("Create the surface and load", "Leftover surfaces", "tool explorer", "catalogue"):
            assert later not in text, later
        assert "Clean up" in text
        assert world.requests == [] and world.sent == []

    def test_pressing_cleanup_without_settings_only_says_so(self, load_fresh, world):
        _, text, _ = self.run(load_fresh, "cleanup")
        assert text.count("Context Retriever is not configured") == 2
        assert world.requests == [] and world.sent == []

    def test_with_settings_but_no_press_nothing_reaches_the_service_or_the_database(self, load_fresh, world):
        outputs, defs = load_fresh(**ENV).app.run()
        assert "Press Test connection" in texts(outputs)
        assert defs["cr_conn"] is None and world.requests == [] and world.sent == []

    def test_with_only_one_of_the_two_settings_it_names_the_other_as_missing(
        self, load_fresh, world, monkeypatch
    ):
        text = texts(load_fresh(CTX_ADMIN_KEY=ADMIN_KEY).app.run()[0])
        assert "Missing: REDIS_URL." in text
        monkeypatch.delenv("CTX_ADMIN_KEY")
        text = texts(load_fresh(REDIS_URL=URL).app.run()[0])
        assert "Missing: CTX_ADMIN_KEY." in text
        assert world.requests == []

    @BOTH
    def test_autorun_demo_drives_the_column_from_connect_to_the_deep_dive(self, load_fresh, world):
        fresh, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["cr_conn"] is not None and defs["cr_session"]["reused"] is False
        assert len(world.surfaces) == 1 and len(world.docs) == 20 and len(world.indexes) == 3
        assert {r["tool"] for r in defs["cr_predicted"]} == set(DEFAULT_TOOLS)
        assert [t["name"] for t in defs["cr_catalog"]] == sorted(world.surfaces["cs_0001"]["tools"])
        assert "The tools the service really made" in text and "Where did my data go" in text
        assert "Ada Lovelace" in text and fresh.CR_PREFIX in text
        # the wow: the first get_ tool with its example id, then Break it
        assert defs["cr_tool"].value == "get_customer_by_id"
        ran, broke = defs["cr_results"]()["run"], defs["cr_results"]()["break"]
        assert ran["outcome"] == "ok" and ran["arguments"] == {"id": "C001"}
        assert broke["outcome"] == "rejected" and broke["code"] == -32602
        calls = [b["params"]["name"] for m, p, b in world.bodies if b and b.get("method") == "tools/call"]
        assert calls[-2] == "get_customer_by_id" and calls[-1] == defs["cr_plan"]["tool"]
        assert "Rejected, code -32602" in text and "not-a-choice" in text
        # paging
        assert defs["cr_page"]["outcome"] == "ok" and defs["cr_page"]["data"]["total_count"] > 0
        assert "total_count" in text and "has_more" in text
        # the deep dive
        assert defs["cr_deep"].value is True and "The tool catalogue" in text
        assert "inputSchema of get_customer_by_id" in text
        assert {c for c, t in world.writes() if not t.startswith(fresh.PREFIX)} == set()
        assert sorted(fresh.ledger_live("cr", "key")) == sorted(world.docs)
        assert fresh.ledger_live("cr", "surface") == ["cs_0001"]

    @BOTH
    def test_autorun_cleanup_removes_it_all_from_the_ledger_without_connecting(self, load_fresh, world):
        self.run(load_fresh, "demo", **ENV)
        world.sent.clear()
        world.requests.clear()
        fresh, text, defs = self.run(load_fresh, "cleanup", **ENV)
        assert world.surfaces == {} and world.docs == {} and world.indexes == {}
        assert "Deleted 1 of 1 surfaces, unlinked 20 of 20 keys and dropped 3 of 3 indexes" in text
        assert defs["cr_conn"] is None, "the demo cells did not run"
        # its own connection (PING), the unlink and the drops; none of Test connection's reads
        assert set(world.names()) - {"PING", "UNLINK", "FT._LIST", "FT.INFO", "FT.DROPINDEX"} == set()
        assert {"UNLINK", "FT.DROPINDEX"} <= set(world.names())
        assert world.requests == [("DELETE", "/context-surfaces/api/v1/context-surfaces/cs_0001")]
        for kind in ("surface", "agent_key", "key", "index"):
            assert fresh.ledger_live("cr", kind, pending=True) == [], kind

    def test_cleanup_deletes_the_surface_and_says_what_it_could_not_do_when_the_database_is_down(
        self, load_fresh, world
    ):
        self.run(load_fresh, "demo", **ENV)
        world.down = True
        fresh, text, _ = self.run(load_fresh, "cleanup", **ENV)
        assert world.surfaces == {} and "The database steps stopped" in text
        assert "Traceback" not in text and "fake-pw-9f3a" not in text
        assert fresh.ledger_live("cr", "surface", pending=True) == []
        assert len(fresh.ledger_live("cr", "key", pending=True)) == 20
        world.down = False
        _, text, _ = self.run(load_fresh, "cleanup", **ENV)
        assert world.docs == {} and world.indexes == {} and "unlinked 20 of 20 keys" in text

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_cleanup_after_a_service_that_drops_its_indexes_with_the_surface_is_a_success(
        self, load_fresh, world, wording
    ):
        """Live, 2026-09-30: the report read "dropped 0 of 3 indexes ... stopped" (a warning)."""
        world.wording = wording
        world.drops_indexes = True
        self.run(load_fresh, "demo", **ENV)
        assert len(world.indexes) == 3
        fresh, text, _ = self.run(load_fresh, "cleanup", **ENV)
        assert "Deleted 1 of 1 surfaces, unlinked 20 of 20 keys and dropped 0 of 3 indexes" in text
        assert "3 of the indexes were already gone" in text and "expected" in text
        assert "The database steps stopped" not in text and "Left behind" not in text
        assert callout_kind(text.raw, "were already gone") == "success"  # finished, not unfinished
        assert world.surfaces == {} and world.docs == {} and world.indexes == {}
        for kind in ("surface", "agent_key", "key", "index"):
            assert fresh.ledger_live("cr", kind, pending=True) == [], kind

    def test_cleanup_still_works_when_connect_would_fail(self, load_fresh, world):
        self.run(load_fresh, "demo", **ENV)
        world.forced["list"] = 500  # Test connection would now fail
        world.search = False  # and so would the database probe
        self.run(load_fresh, "cleanup", **ENV)
        assert world.surfaces == {} and world.docs == {}

    def test_the_cleanup_and_reset_buttons_work_when_clicked_and_only_reset_bumps_the_token(
        self, load_fresh, world
    ):
        def clicked(cleanup, reset):
            bumps = []
            config = {
                "cr_admin_key": SimpleNamespace(value=None), "cr_url": SimpleNamespace(value=None),
                "cr_api_url": SimpleNamespace(value=API), "cr_mcp_url": SimpleNamespace(value=MCP),
                "cr_connect": Btn(False), "cr_gen": lambda: 0,
                "cr_set_gen": lambda update: bumps.append(update(0)),
                "cr_cleanup_btn": Btn(cleanup), "cr_reset_btn": Btn(reset),
            }  # fmt: skip
            return bumps, self.run(load_fresh, "", config, **ENV)

        self.run(load_fresh, "demo", **ENV)
        bumps, (_, text, _) = clicked(cleanup=True, reset=False)
        assert world.surfaces == {} and bumps == [] and "Deleted 1 of 1 surfaces" in text
        self.run(load_fresh, "demo", **ENV)
        bumps, _ = clicked(cleanup=False, reset=True)
        assert world.surfaces == {} and world.docs == {} and bumps == [1]

    def test_a_wrong_admin_key_is_a_red_message_and_not_a_traceback_or_the_key(self, load_fresh, world):
        world.forced["list"] = 401
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["cr_conn"] is None and "key was refused" in text and ADMIN_KEY not in text
        assert "Traceback" not in text and world.writes() == [] and world.docs == {}

    def test_a_database_without_search_stops_at_connect_and_says_how_to_fix_it(self, load_fresh, world):
        world.search = False
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["cr_conn"] is None and "no Search or no JSON" in text and "redis.io/try-free" in text
        assert world.surfaces == {} and "Create the surface and load" not in text

    def test_a_refused_database_connection_is_a_red_message_that_does_not_echo_the_password(
        self, load_fresh, world, monkeypatch
    ):
        def refuse(self, *args, **kwargs):
            raise redis.ConnectionError(f"Error 111 connecting to db.example:6379. {URL}")

        monkeypatch.setattr(redis.Redis, "execute_command", refuse)
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["cr_conn"] is None and "Error 111" in text and "fake-pw-9f3a" not in text

    def test_a_url_with_a_stray_space_is_a_message_that_does_not_echo_it(self, load_fresh, world):
        _, text, defs = self.run(
            load_fresh, "demo", CTX_ADMIN_KEY=ADMIN_KEY, REDIS_URL="redis://default:pa ss-9f3a@db:6379"
        )
        assert defs["cr_conn"] is None and "whitespace" in text and "pa ss-9f3a" not in text
        assert world.requests == []

    def test_a_failed_create_is_a_message_and_the_cells_below_it_do_not_run(self, load_fresh, world):
        world.forced["create"] = 500
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["cr_conn"] is not None and defs["cr_session"] is None
        assert "HTTP 500" in text and "cr_catalog" not in defs
        assert "The tools the service really made" not in text and world.docs == {}

    def test_a_failing_tool_list_is_a_message_and_stops_the_explorer(self, load_fresh, world):
        world.forced["mcp"] = 500
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["cr_session"] is not None and "cr_catalog" not in defs and "HTTP 500" in text
        assert "tool explorer" not in text

    def test_the_explorer_works_for_made_up_tools_because_nothing_is_hardcoded(self, load_fresh, world):
        world.tools_override = [
            {"name": "paint", "description": "Paint a thing", "inputSchema": {
                "type": "object", "required": ["shade"], "properties": {
                    "shade": {"type": "string", "enum": ["red", "blue"]},
                    "coats": {"type": "integer", "minimum": 2}}}},
            {"name": "ping", "description": "Say hello", "inputSchema": {"type": "object"}},
        ]  # fmt: skip
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["cr_tool"].value == "paint"  # no get_ tool: the first of the list
        results = defs["cr_results"]()
        assert results["run"]["data"] == {"tool": "paint", "args": {"shade": "red"}}  # coats: not chosen
        assert results["break"]["outcome"] == "rejected" and "not-a-choice" in text
        assert "Say hello" in text and "Paint a thing" in text  # the deep dive lists every tool

    def test_break_it_hides_when_the_schemas_offer_nothing_to_break(self, load_fresh, world):
        world.tools_override = [TOOL_OPEN]
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["cr_plan"] is None and defs["cr_results"]()["break"] is None
        assert defs["cr_results"]()["run"]["data"]["tool"] == "search_thing"
        assert "Break it will" not in text  # the line that says what the button would send

    def test_a_tool_that_takes_no_arguments_says_so_and_still_runs(self, load_fresh, world):
        world.tools_override = [{"name": "ping", "description": "Say hello", "inputSchema": {"type": "object"}}]
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert "This tool takes no arguments" in text
        assert defs["cr_results"]()["run"]["arguments"] == {}

    @pytest.mark.parametrize("world", ["unified"], indirect=True)
    def test_json_that_does_not_parse_is_a_message_and_nothing_is_sent(self, load_fresh, world):
        kept = []
        controls = {
            "cr_tool": SimpleNamespace(value="filter_customer"),
            "cr_form": SimpleNamespace(value={"tag_conditions": "[{"}),
            "cr_run": Btn(True), "cr_break": Btn(False), "cr_plan": None,
            "cr_results": lambda: {"run": None, "break": None}, "cr_set_results": kept.append,
        }  # fmt: skip
        _, text, _ = self.run(load_fresh, "demo", controls, **ENV)
        assert "tag_conditions is not valid JSON" in text and kept == []
        assert not [b for m, p, b in world.bodies if b and b.get("method") == "tools/call"][1:]

    def test_the_admin_key_the_password_and_the_agent_key_appear_in_no_output_or_preview(
        self, load_fresh, world
    ):
        from marimo._messaging.variables import create_variable_value

        _, text, defs = self.run(load_fresh, "demo", **ENV)
        secrets = ["fake-pw-9f3a", ADMIN_KEY, *world.agent_keys]
        assert world.agent_keys
        for secret in secrets:
            assert secret not in text, secret
        for name, value in defs.items():
            shown = create_variable_value(name, value).value or ""
            assert not [s for s in secrets if s in shown], name

    def test_another_accounts_surfaces_appear_in_no_output_or_preview(self, load_fresh, world):
        from marimo._messaging.variables import create_variable_value

        sid = world.add_surface("acme-prod-claims-agent", run=None, created_by="acme")
        world.surfaces[sid]["description"] = "claims triage for the ACME production account"
        world.surfaces[sid]["data_model"] = {"entities": [{"name": "Claim", "fields": [{"name": "ssn"}]}]}
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["cr_session"] is not None
        for private in ("acme", "claims", "ACME", "ssn"):
            assert private not in text.raw and private not in text, private
            for name, value in defs.items():
                assert private not in (create_variable_value(name, value).value or ""), (name, private)

    def test_a_result_of_run_stays_when_break_it_is_pressed_and_the_other_way_round(self, load_fresh, world):
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        shown = defs["cr_results"]()
        assert shown["run"] and shown["break"]
        assert "Ada Lovelace" in text and "Rejected, code -32602" in text

    def test_the_demo_leaves_another_runs_surface_alone_and_lists_it_for_deletion(self, load_fresh, world):
        old = world.add_surface("iris-basics-feedcafe", run="feedcafe", age_h=5)
        world.add_surface("iris-basics-deadbeef", run="deadbeef", age_h=0.4)
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert "Leftover surfaces from other runs (2)" in text
        assert "iris-basics-feedcafe" in text.raw and "no, not yet 2 h old" in text.raw
        assert old in world.surfaces and defs["cr_orphan_drop"].value is False
        assert [m for m, _ in world.requests].count("DELETE") == 0

    def test_an_orphan_is_deleted_only_after_the_typed_confirmation(self, load_fresh, world):
        old = world.add_surface("iris-basics-feedcafe", run="feedcafe", age_h=5)

        def clicked(phrase):
            controls = {
                "cr_orphan_confirm": SimpleNamespace(value=phrase), "cr_orphan_drop": Btn(True),
            }
            return self.run(load_fresh, "demo", controls, **ENV)[1]

        assert "to confirm" in clicked("nope") and old in world.surfaces
        assert "Deleted 1 of 1 surfaces" in clicked("delete orphans")
        assert old not in world.surfaces

    def test_a_kept_change_reaches_the_prediction_and_the_surface(self, load_fresh, world):
        controls = {
            "cr_edits": lambda: {}, "cr_set_edits": lambda change: None,
            "cr_field": SimpleNamespace(value="Order.status"),
            "cr_index": SimpleNamespace(value="none"), "cr_allowed": SimpleNamespace(value=False),
            "cr_keep": Btn(False), "cr_model_reset": Btn(False),
        }  # fmt: skip
        _, text, defs = self.run(load_fresh, "demo", controls, **ENV)
        assert defs["cr_picks"] == {"Order.status": {"index": "none", "allowed": False}}
        assert "Order.status: tag with allowed_values → not indexed" in text
        made = {t["name"]: t for t in world.surfaces["cs_0001"]["_tools"]}
        assert "status" not in made["filter_order"]["inputSchema"]["properties"]["tag_conditions"]["items"]["properties"]["field"]["enum"]
        predicted = {r["tool"]: r["vs default"] for r in defs["cr_predicted"]}
        assert predicted["filter_order"] == "changed" and len(predicted) == 21
        assert predicted["filter_customer"] == "" and predicted["count_order"] == "changed"


class TestNoCredentialsExport:
    def test_a_real_export_shows_the_config_message_and_the_offline_model_and_nothing_more(
        self, nb_file, tmp_path
    ):
        work = tmp_path / "exported"
        work.mkdir()
        shutil.copy2(nb_file, work / nb_file.name)
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        done = subprocess.run(
            [sys.executable, "-m", "marimo", "-y", "export", "html", "--no-sandbox",
             nb_file.name, "-o", "out.html", "-f"],
            cwd=work, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        session = (work / "__marimo__" / "session" / f"{nb_file.name}.json").read_text("utf-8")
        assert "Context Retriever is not configured" in session
        assert "From data model to tools" in session and "The whole exported data model" in session
        assert "Recorded live on 2026-09-30" in session
        for later in ("Create the surface and load", "Leftover surfaces", "tool explorer"):
            assert later not in session, later


# ---- the real marimo runtime ----------------------------------------------------------------------------------------
RUNNER = """
import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, {tests!r})
import httpx
import redis
from fake_cr import FakeWorld

state = Path({state!r})
world = pickle.loads(state.read_bytes()) if state.exists() else FakeWorld(os.environ["FAKE_STYLE"])


def save():
    state.write_bytes(pickle.dumps(world))


def handle(self, *args, **kwargs):
    try:
        return world.handle(args)
    finally:
        save()


def send(self, request, **kwargs):
    try:
        return world.http(request)
    finally:
        save()


# marimo spawns its kernel, which re-imports this file
redis.Redis.execute_command = handle
httpx.Client.send = send

if __name__ == "__main__":
    from marimo._cli.cli import main

    args = ["-y", "export", "html", "--no-sandbox", {notebook!r}, "-o", "out.html", "-f"]
    main(args, standalone_mode=False)
"""


class TestRealRuntime:
    """`marimo export` itself, not app.run(): its kernel cancels the descendants of a stop.

    Column 0 shares REDIS_URL and its AUTORUN demo runs too, and the fake knows nothing of its
    commands, so only this column's cells are judged.
    """

    def export(self, nb_file, tmp_path, stage, style="unified"):
        work = tmp_path / "real"
        work.mkdir(exist_ok=True)
        shutil.copy2(nb_file, work / nb_file.name)
        state = tmp_path / "world.pkl"
        (work / "runner.py").write_text(
            RUNNER.format(tests=str(Path(__file__).parent), state=str(state), notebook=nb_file.name),
            encoding="utf-8",
        )
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        env.update(
            PYTHONIOENCODING="utf-8", PYTHONUTF8="1", CTX_ADMIN_KEY=ADMIN_KEY, REDIS_URL=URL,
            IRIS_BASICS_RUN=RUN, IRIS_BASICS_AUTORUN=stage, IRIS_BASICS_HEADLESS="1",
            FAKE_STYLE=style,
        )  # fmt: skip
        done = subprocess.run(
            [sys.executable, "runner.py"], cwd=work, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        session = work / "__marimo__" / "session" / f"{nb_file.name}.json"
        return json.loads(session.read_text(encoding="utf-8")), pickle.loads(state.read_bytes())

    @staticmethod
    def mine(nb):
        """The ids of column 2's cells, which marimo also uses in the session file."""
        from marimo._ast.app import InternalApp

        cells = InternalApp(nb.app).cell_manager.valid_cells()
        return {cid for cid, data in cells if data._cell.config.column == 2}

    def errors(self, nb, session):
        ids = self.mine(nb)
        assert ids <= {c["id"] for c in session["cells"]}
        return [
            o
            for c in session["cells"]
            if c["id"] in ids
            for o in c.get("outputs") or []
            if o["type"] == "error"
        ]

    @pytest.mark.parametrize("style", ["classic", "unified"])
    def test_autorun_demo_then_cleanup_run_in_the_real_kernel(self, nb, nb_file, tmp_path, style):
        session, world = self.export(nb_file, tmp_path, "demo", style)
        assert self.errors(nb, session) == []
        text = json.dumps(session)
        assert "The tools the service really made" in text and "Where did my data go" in text
        assert "Ada Lovelace" in text and "Rejected" in text and "The tool catalogue" in text
        assert len(world.surfaces) == 1 and len(world.docs) == 20 and len(world.indexes) == 3
        for secret in ("fake-pw-9f3a", ADMIN_KEY, *world.agent_keys):
            assert secret not in text, secret

        session, world = self.export(nb_file, tmp_path, "cleanup", style)
        assert world.surfaces == {} and world.docs == {} and world.indexes == {}
        assert "Deleted" in json.dumps(session)
        # Test connection did not run, so its descendants were cancelled; cleanup ran regardless.
        assert {e["ename"] for e in self.errors(nb, session)} == {"ancestor-stopped"}


# ---- the notebook in a kernel, driven the way the browser drives it -----------------------------------------------------
class TestInteractive:
    """Buttons and controls pressed one at a time, so the reactive wiring is exercised for real.

    Only marimo's kernel can tell whether a cell that sets state and a cell that reads it settle
    or loop, or whether a run button's reset re-fires what depends on it.
    """

    @pytest.fixture
    def notebook(self, load_fresh, world, monkeypatch):
        for name, value in ENV.items():
            monkeypatch.setenv(name, value)
        return load_fresh()

    def test_kept_changes_pile_up_and_the_reset_button_clears_them(self, notebook, world):
        async def scenario(book):
            assert book.g("cr_picks") == {"Order.status": {"index": "tag", "allowed": True}}
            await book.set("cr_field", ["Customer.name"])
            assert book.g("cr_index").value == "text"  # this field's own default
            await book.set("cr_index", ["tag"])
            assert book.g("cr_picks")["Customer.name"] == {"index": "tag", "allowed": False}
            await book.press("cr_keep")
            await book.set("cr_field", ["Order.total"])
            await book.set("cr_index", ["none"])
            picks = book.g("cr_picks")  # the kept change and the pending one, not the default
            assert set(picks) == {"Customer.name", "Order.total"}
            predicted = {r["tool"]: r["vs default"] for r in book.g("cr_predicted")}
            assert predicted["summarize_order"] == "gone"
            assert predicted["search_customer_by_text"] == "gone"
            assert predicted["filter_customer"] == "changed"
            await book.press("cr_model_reset")
            await book.set("cr_field", ["Order.status"])
            assert book.g("cr_picks") == {"Order.status": {"index": "tag", "allowed": True}}

        assert drive(notebook, scenario).exceptions() == []

    def test_the_presenter_notes_switch_raises_nothing_at_any_stage_of_a_connected_demo(
        self, notebook, world
    ):
        # Flipping it re-runs what reads it and not the cells above; cr_seen, cr_page and
        # cr_results do not exist until Create is pressed, and were NameErrors while the notes
        # were read by the cells that show them.
        async def scenario(book):
            await book.set("ib_notes", True)  # connected to nothing yet
            await book.press("cr_connect")
            await book.set("ib_notes", False)  # connected, nothing created
            await book.press("cr_create")
            await book.set("ib_notes", True)  # created, nothing run
            await book.press("cr_run")
            await book.set("ib_notes", False)
            assert "display: none" in book.g("ib_notes_style").text

        assert drive(notebook, scenario).exceptions() == []

    def test_connect_create_run_break_page_and_clean_up(self, notebook, world):
        async def scenario(book):
            assert book.g("cr_conn") is None
            await book.press("cr_connect")
            assert book.g("cr_conn")["facts"]["version"] == "8.4.0"
            await book.press("cr_create")
            assert book.g("cr_session")["surface"] == "cs_0001" and len(world.docs) == 20
            assert book.g("cr_catalog")[0]["name"] == "count_customer"
            assert book.g("cr_tool").value == "get_customer_by_id"
            assert book.g("cr_results")() == {"run": None, "break": None}  # nothing pressed yet
            await book.press("cr_run")
            assert book.g("cr_results")()["run"]["data"]["name"] == "Ada Lovelace"
            await book.press("cr_break")
            shown = book.g("cr_results")()  # the accepted call is still there beside the rejected one
            assert shown["break"]["outcome"] == "rejected"
            assert shown["run"]["data"]["name"] == "Ada Lovelace"
            await book.press("cr_run")
            assert book.g("cr_results")()["break"]["outcome"] == "rejected"
            await book.set("cr_limit", 1)
            assert book.g("cr_page")["data"]["count"] == 1 and book.g("cr_page")["data"]["has_more"]
            await book.set("cr_offset", 9)
            assert book.g("cr_page")["data"]["results"] == []
            await book.press("cr_cleanup_btn")
            assert world.surfaces == {} and world.docs == {} and world.indexes == {}

        assert drive(notebook, scenario).exceptions() == []

    def test_choosing_another_tool_rebuilds_the_form_from_its_schema(self, notebook, world):
        async def scenario(book):
            await book.press("cr_connect")
            await book.press("cr_create")
            await book.set("cr_tool", ["filter_order"])
            assert set(book.g("cr_form").value) == {
                "limit", "offset", "sort_by", "sort_order", "tag_conditions", "numeric_conditions",
            }  # fmt: skip
            assert book.g("cr_plan")["tool"] == "filter_order"  # its own menu is broken first
            await book.press("cr_run")  # nothing typed: the service wants a condition
            ran = book.g("cr_results")()["run"]
            assert ran["outcome"] == "tool_error" and "at least one condition" in ran["message"]
            await book.set("cr_tool", ["get_order_by_id"])
            assert book.g("cr_results")() == {"run": None, "break": None}  # a new form starts afresh

        assert drive(notebook, scenario).exceptions() == []

    @pytest.mark.parametrize("world", ["classic"], indirect=True)
    def test_choosing_another_classic_tool_rebuilds_the_form_from_its_schema(self, notebook, world):
        async def scenario(book):
            await book.press("cr_connect")
            await book.press("cr_create")
            await book.set("cr_tool", ["filter_order_by_status"])
            assert set(book.g("cr_form").value) == {"value", "limit", "offset"}
            assert book.g("cr_form").value["value"] == "placed"
            assert book.g("cr_plan")["tool"] == "filter_order_by_status"
            await book.press("cr_run")
            assert book.g("cr_results")()["run"]["data"]["total_count"] == 2
            await book.set("cr_tool", ["get_order_by_id"])
            assert book.g("cr_results")() == {"run": None, "break": None}  # a new form starts afresh

        assert drive(notebook, scenario).exceptions() == []

    def test_changing_the_model_after_create_drops_the_session_and_create_picks_it_up_again(
        self, notebook, world
    ):
        async def scenario(book):
            await book.press("cr_connect")
            await book.press("cr_create")
            first = book.g("cr_session")["surface"]
            await book.set("cr_index", ["none"])  # the model changes under the surface
            assert book.g("cr_session") is None  # and the button was not pressed again
            await book.press("cr_create")
            assert book.g("cr_session")["reused"] is True and book.g("cr_session")["surface"] == first
            assert book.g("cr_session")["stale"] is True  # and it says the tools are the old model's
            assert len(world.surfaces) == 1
            old = next(t for t in book.g("cr_catalog") if t["name"] == "filter_order")
            assert "status" in old["inputSchema"]["properties"]["tag_conditions"]["items"]["properties"]["field"]["enum"]  # the old model

        assert drive(notebook, scenario).exceptions() == []

    def test_reset_cleans_up_and_puts_the_column_back_to_press_test_connection(self, notebook, world):
        async def scenario(book):
            await book.press("cr_connect")
            await book.press("cr_create")
            await book.press("cr_reset_btn")
            assert world.surfaces == {} and world.docs == {}
            assert book.g("cr_conn") is None

        assert drive(notebook, scenario).exceptions() == []
