"""The data objects the notebook and this package pass around.

HelpArticle is one help-center article. Customer, Product, and Order are the shop.
ShopRecords is those three lists, keyed by entity name, which is how Chapter 3
imports them. Labels the shop treats as exact values are closed sets, the same
ones Chapter 3 sends as allowed_values.

SearchResults is one FT.SEARCH reply. Reply and Exchange are one HTTP call.
ChatMessage and ChatTurn are one chat turn. ToolSpec is one MCP tool.
Nothing here is checked at run time.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple, NotRequired, Protocol, TypedDict

type JsonObject = dict[str, Any]

type HelpCategory = Literal["shipping", "returns", "sizing", "orders", "warranty", "care"]
type LoyaltyTier = Literal["bronze", "silver", "gold"]
type SalesRegion = Literal["amer", "emea", "apac"]
type ProductCategory = Literal["footwear", "apparel", "camping", "accessories"]
type OrderStatus = Literal["placed", "shipped", "delivered", "returned"]


class HelpArticle(TypedDict):
    """One help-center article, before the notebook stores it as a hash."""

    id: str
    title: str
    body: str
    category: HelpCategory
    year: int


class Customer(TypedDict):
    """A shopper, such as Priya (C001) or Tom (C002)."""

    customer_id: str
    name: str
    tier: LoyaltyTier
    region: SalesRegion
    lifetime_value: float


class Product(TypedDict):
    """An item the shop sells, such as the Ultralight Tent 2P."""

    sku: str
    title: str
    category: ProductCategory
    price: float


class Order(TypedDict):
    """One purchase: a customer, a product, and where it stands."""

    order_id: str
    customer_id: str
    sku: str
    region: SalesRegion
    status: OrderStatus
    total: float


class ShopRecords(TypedDict):
    """The shop, grouped the way Chapter 3 imports it: one list per entity name."""

    Customer: list[Customer]
    Product: list[Product]
    Order: list[Order]


class Reply(NamedTuple):
    """One response: the status code, the JSON object, and the elapsed milliseconds.

    payload is None when the body is empty or is not a JSON object, including an HTML
    error page. The status code still says what happened.
    """

    status: int
    payload: JsonObject | None
    ms: int


class Exchange(NamedTuple):
    """One request and the Reply it produced, in the order recording() saw them."""

    method: str
    path: str
    body: JsonObject | None
    status: int
    payload: JsonObject | None
    ms: int


# What a chat turn returns: the reply, streamed a piece of text at a time.
ChatTurn = AsyncIterator[str]


class ChatMessage(Protocol):
    """One message in a chat: who sent it and what it says. marimo's chat messages fit this."""

    role: str  # "user" or "assistant"
    content: object  # the text, or an object whose str() is the text


class ToolSpec(TypedDict):
    """One MCP tool: its name, the JSON Schema for its arguments, and an optional description."""

    name: str
    inputSchema: dict[str, Any]
    description: NotRequired[str]


@dataclass(frozen=True, slots=True)
class SearchResults:
    """The total FT.SEARCH reported, and the hits included in this reply.

    total can be larger than len(hits) when the query uses LIMIT.
    Each hit has key (the Redis key) plus the hash fields the query returned.
    """

    total: int
    hits: list[dict[str, Any]]
