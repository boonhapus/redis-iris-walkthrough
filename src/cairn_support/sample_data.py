"""Cairn Outfitters sample content, as Python literals.

Cairn Outfitters is a made-up outdoor gear shop. Everything the notebook loads, caches, indexes,
or remembers comes from this module, so nothing here is Redis or Iris protocol: no command, no
request body, no key template. Those stay in the notebook cells where a reader can see them.

The tests in tests/test_cairn_support.py hold this data to the constraints the examples rely on:

- no help article says "cramped" or "shoes", exactly one says "Trailhead", and none says
  "waterproofng", so each search preset shows a different engine winning;
- every shop id matches ^[A-Za-z]+[0-9]+$, so a TAG query never meets a hyphen;
- every THRESHOLD_PROBES target is a GEAR_CARE_ANSWERS key or None;
- each memory question shares no word stem with the fact it is after;
- SESSION_TURNS[-1]["text"] == MEMORY_QUESTIONS[0]["text"].
"""

# ---- Redis Search: the help centre ----

# 36 short articles, six in each of six categories (shipping, returns, care, sizing, orders,
# warranty). The boot guide answers "trail shoes feel cramped" without using any of those words.
HELP_ARTICLES: list[dict] = [
    {
        "id": "ship-times",
        "title": "How long does delivery take?",
        "body": (
            "Standard orders leave our Denver warehouse within one business day and arrive in "
            "three to five. Express upgrades arrive in two."
        ),
        "category": "shipping",
        "year": 2025,
    },
    {
        "id": "ship-express",
        "title": "Upgrading to express or overnight delivery",
        "body": (
            "Choose express at checkout for two-day arrival, or overnight for orders placed "
            "before noon Mountain time. Rural addresses may need an extra day."
        ),
        "category": "shipping",
        "year": 2024,
    },
    {
        "id": "ship-international",
        "title": "Shipping outside the United States",
        "body": (
            "We ship to Canada, the UK and the EU. Duties and import taxes are collected at "
            "checkout so nothing is due when the courier arrives."
        ),
        "category": "shipping",
        "year": 2025,
    },
    {
        "id": "ship-overdue",
        "title": "Missing and overdue shipments",
        "body": (
            "If a tracking page has not moved for five days, the carrier has probably lost it. "
            "Contact us and we will reship at no charge or refund the order."
        ),
        "category": "shipping",
        "year": 2026,
    },
    {
        "id": "ship-address",
        "title": "Changing a delivery address",
        "body": (
            "Addresses can be edited until the order is packed. After that the carrier can "
            "redirect it, but only while it is still in the local depot."
        ),
        "category": "shipping",
        "year": 2023,
    },
    {
        "id": "ship-free",
        "title": "Free shipping threshold",
        "body": (
            "Orders over 75 dollars ship free within the contiguous states. Oversized items such "
            "as tents and kayaks always carry a freight surcharge."
        ),
        "category": "shipping",
        "year": 2026,
    },
    {
        "id": "returns-window",
        "title": "How long do I have to return something?",
        "body": (
            "Unworn, unwashed gear can go back within 60 days of delivery. Tags must be attached "
            "and the original packaging kept."
        ),
        "category": "returns",
        "year": 2025,
    },
    {
        "id": "returns-refund",
        "title": "When will the refund reach my card?",
        "body": (
            "Once the warehouse scans your parcel, the credit is released within two days and "
            "your bank usually posts it in five to seven."
        ),
        "category": "returns",
        "year": 2024,
    },
    {
        "id": "returns-exchange",
        "title": "Exchanging for a different size or color",
        "body": (
            "Start an exchange from your order page and we send the replacement first, so you are"
            " never left without gear before a trip."
        ),
        "category": "returns",
        "year": 2025,
    },
    {
        "id": "returns-label",
        "title": "Printing a prepaid return label",
        "body": (
            "Open the order, choose Return, and print the label. Drop the box at any carrier "
            "location; there is nothing to pay."
        ),
        "category": "returns",
        "year": 2023,
    },
    {
        "id": "returns-final",
        "title": "Final sale and clearance items",
        "body": (
            "Items marked final sale cannot be returned, but defective ones are always covered by"
            " the warranty regardless of the sale price."
        ),
        "category": "returns",
        "year": 2022,
    },
    {
        "id": "returns-damaged",
        "title": "Something arrived damaged",
        "body": (
            "Take a photo of the box and the item and send it to us within 14 days. We will "
            "replace it immediately and arrange a pickup."
        ),
        "category": "returns",
        "year": 2026,
    },
    {
        "id": "care-waterproof",
        "title": "Restoring waterproofing on shells and boots",
        "body": (
            "When rain stops beading and starts soaking in, wash the garment and apply a spray-on"
            " or wash-in treatment. A short tumble in the dryer sets it."
        ),
        "category": "care",
        "year": 2024,
    },
    {
        "id": "care-down",
        "title": "Washing a down sleeping bag",
        "body": (
            "Use a front-loading machine with a down-specific soap, then dry on low heat with "
            "clean tennis balls to break up the clumps."
        ),
        "category": "care",
        "year": 2023,
    },
    {
        "id": "care-boots",
        "title": "Cleaning and drying hiking boots",
        "body": (
            "Brush off the mud, rinse with lukewarm water and dry them at room temperature. Never"
            " put leather next to a heater or campfire."
        ),
        "category": "care",
        "year": 2025,
    },
    {
        "id": "care-tent",
        "title": "Storing a tent between trips",
        "body": (
            "Pitch it once at home until completely dry, then keep it loosely packed in a cool "
            "cupboard. Damp fabric grows mildew within days."
        ),
        "category": "care",
        "year": 2022,
    },
    {
        "id": "care-stove",
        "title": "Cleaning a camp stove",
        "body": (
            "Clear the jet with the pricking wire, wipe the burner and check the fuel hose for "
            "cracks before every season."
        ),
        "category": "care",
        "year": 2024,
    },
    {
        "id": "care-pack",
        "title": "Washing a backpack",
        "body": (
            "Empty every pocket, scrub the mesh with mild soap and a soft brush, and hang the "
            "pack upside down to air dry away from direct sun."
        ),
        "category": "care",
        "year": 2026,
    },
    {
        "id": "size-boots",
        "title": "Finding your boot size",
        "body": (
            "Boots run a half size small. Measure your foot at the end of the day, wear your "
            "hiking socks, and leave a thumb of room at the toe."
        ),
        "category": "sizing",
        "year": 2025,
    },
    {
        "id": "size-jackets",
        "title": "Jacket and shell fit guide",
        "body": (
            "Layer a midweight fleece underneath when you try a shell on. Sleeves should cover "
            "your wrists when you reach forward."
        ),
        "category": "sizing",
        "year": 2024,
    },
    {
        "id": "size-packs",
        "title": "Choosing a pack size by torso length",
        "body": (
            "Volume matters less than fit. Measure from the bone at the base of your neck down to"
            " the hip line, then match it to the frame size chart."
        ),
        "category": "sizing",
        "year": 2026,
    },
    {
        "id": "size-bags",
        "title": "Picking a sleeping bag length and temperature rating",
        "body": (
            "Long bags fit people over six feet tall. The comfort rating, not the limit rating, "
            "is the temperature you will actually sleep warm at."
        ),
        "category": "sizing",
        "year": 2023,
    },
    {
        "id": "size-kids",
        "title": "Sizing for children",
        "body": (
            "Kids grow quickly, so buy jackets a size up and pick boots with a removable insole "
            "you can swap for a thinner one later."
        ),
        "category": "sizing",
        "year": 2022,
    },
    {
        "id": "size-gloves",
        "title": "Glove and mitten sizing",
        "body": (
            "Measure around your palm below the knuckles. Mittens are warmer than gloves because "
            "your fingers share heat."
        ),
        "category": "sizing",
        "year": 2025,
    },
    {
        "id": "order-cancel",
        "title": "Canceling an order",
        "body": (
            "Orders can be canceled from your account until they are packed, which is usually "
            "within two hours of checkout."
        ),
        "category": "orders",
        "year": 2025,
    },
    {
        "id": "order-change",
        "title": "Adding an item to an order I already placed",
        "body": (
            "We cannot edit a paid order, but you can place a second one and we will combine the "
            "two into a single box while both are still unpacked."
        ),
        "category": "orders",
        "year": 2024,
    },
    {
        "id": "order-status",
        "title": "Checking the status of an order",
        "body": (
            "Sign in and open Orders to see whether yours is processing, packed or shipped. A "
            "tracking number appears the moment it ships."
        ),
        "category": "orders",
        "year": 2023,
    },
    {
        "id": "order-gift",
        "title": "Sending a gift with a message",
        "body": (
            "Tick the gift box at checkout to hide prices on the packing slip and add a note of "
            "up to 200 characters."
        ),
        "category": "orders",
        "year": 2026,
    },
    {
        "id": "order-promo",
        "title": "Using a promo code",
        "body": (
            "Enter the code in the box above the total. Only one code applies per order, and "
            "codes cannot be added once the order is placed."
        ),
        "category": "orders",
        "year": 2022,
    },
    {
        "id": "order-backorder",
        "title": "Backordered and out of stock items",
        "body": (
            "A backordered item ships as soon as it lands in our warehouse. Choose split shipping"
            " if you would rather have the rest first."
        ),
        "category": "orders",
        "year": 2025,
    },
    {
        "id": "warranty-lifetime",
        "title": "Lifetime warranty on Ridgeline packs",
        "body": (
            "Every Ridgeline pack is guaranteed against failed seams, buckles and fabric for as "
            "long as you own it. Wear and tear from use is not covered."
        ),
        "category": "warranty",
        "year": 2026,
    },
    {
        "id": "warranty-zipper",
        "title": "A zipper that no longer closes",
        "body": (
            "Most jammed or splitting zips can be fixed with a new slider. Send the item in and "
            "we repair it for free within the warranty."
        ),
        "category": "warranty",
        "year": 2024,
    },
    {
        "id": "warranty-claim",
        "title": "Making a warranty claim",
        "body": (
            "Fill in the claim form with photos of the fault and your order number. Our team "
            "answers within two business days."
        ),
        "category": "warranty",
        "year": 2025,
    },
    {
        "id": "warranty-resole",
        "title": "Resoling worn boots",
        "body": (
            "Trailhead boots use Vibram Megagrip outsoles that a cobbler can replace. Send them "
            "to our repair shop and they come back within three weeks."
        ),
        "category": "warranty",
        "year": 2023,
    },
    {
        "id": "warranty-tent",
        "title": "Tent poles and fabric coverage",
        "body": (
            "A snapped pole is replaced free for five years. Sun damage, and punctures caused by "
            "sharp rocks, are not covered."
        ),
        "category": "warranty",
        "year": 2022,
    },
    {
        "id": "warranty-repair",
        "title": "Paid repairs outside the warranty",
        "body": (
            "Not covered? Our repair shop patches, re-stitches and re-proofs gear for a flat fee,"
            " quoted before we start any work."
        ),
        "category": "warranty",
        "year": 2024,
    },
]

# ---- LangCache: gear-care answers ----

# The five answers the cache stores. Nothing here is close to another app's questions.
GEAR_CARE_ANSWERS: list[dict] = [
    {
        "key": "boots",
        "prompt": "How do I clean my Trailhead boots after a muddy hike?",
        "response": (
            "Brush off the dry mud, rinse them with lukewarm water and let them dry at room "
            "temperature. Never put leather next to a heater or a campfire."
        ),
    },
    {
        "key": "bag",
        "prompt": "How do I wash my down sleeping bag?",
        "response": (
            "Use a front-loading machine and a down-specific soap, then dry it on low heat with "
            "clean tennis balls to break up the clumps."
        ),
    },
    {
        "key": "tent",
        "prompt": "How should I store my tent between trips?",
        "response": (
            "Pitch it once at home until it is completely dry, then keep it loosely packed in a "
            "cool cupboard. Damp fabric grows mildew within days."
        ),
    },
    {
        "key": "shell",
        "prompt": "How do I restore the waterproofing on my 3-layer rain shell?",
        "response": (
            "Wash the shell, then apply a spray-on or wash-in treatment. A short tumble in the "
            "dryer sets it."
        ),
    },
    {
        "key": "stove",
        "prompt": "How do I clean my camp stove?",
        "response": (
            "Clear the jet with the pricking wire, wipe the burner and check the fuel hose for "
            "cracks before every season."
        ),
    },
]

# The threshold example's questions. target is the GEAR_CARE_ANSWERS key a correct hit returns,
# or None when the cached answer would be wrong for the question, which makes a hit a false hit.
THRESHOLD_PROBES: list[dict] = [
    {
        "text": "What's the right way to launder a goose-down sleeping bag?",
        "kind": "paraphrase",
        "target": "bag",
    },
    {
        "text": "how should i put my tent away after a trip",
        "kind": "paraphrase",
        "target": "tent",
    },
    {
        "text": "Best way to get the mud off my Trailhead boots?",
        "kind": "paraphrase",
        "target": "boots",
    },
    {
        "text": "HOW DO I WASH MY DOWN SLEEPING BAG?",
        "kind": "same words, capitals",
        "target": "bag",
    },
    {
        "text": "How do I wash my synthetic sleeping bag?",
        "kind": "swapped item",
        "target": None,
    },
    {
        "text": "How do I avoid washing my down sleeping bag?",
        "kind": "negation",
        "target": None,
    },
    {
        "text": "What is the capital of Australia?",
        "kind": "unrelated",
        "target": None,
    },
]

# The cache-aside example: a question the cache misses, the answer to store, and a paraphrase.
# prefix goes in front of the stored prompt and of both searches. It keeps the stored entry close
# only to questions that name the shop, so on a shared cache it is never served for another
# app's generic question.
CACHE_ASIDE_SCRIPT: dict = {
    "prefix": "At Cairn Outfitters: ",
    "ask": "How do I fix a snapped tent pole?",
    "answer": (
        "Splint the break with the repair sleeve from your tent's kit and tape it tight. A "
        "snapped pole is replaced free within five years of purchase."
    ),
    "again": "What should I do about a broken tent pole?",
}

# ---- Context Retriever: the shop ----

# 6 customers, 6 products and 8 orders, by entity name. Every id is letters plus digits with no
# hyphen. EMEA and APAC each have 3 customers and 4 orders.
SHOP_RECORDS: dict[str, list[dict]] = {
    "Customer": [
        {"customer_id": "C001", "name": "Ada Lovelace", "tier": "gold", "region": "emea", "lifetime_value": 4200.0},
        {"customer_id": "C002", "name": "Grace Hopper", "tier": "gold", "region": "apac", "lifetime_value": 3900.0},
        {"customer_id": "C003", "name": "Alan Turing", "tier": "silver", "region": "emea", "lifetime_value": 1500.0},
        {"customer_id": "C004", "name": "Katherine Johnson", "tier": "silver", "region": "apac", "lifetime_value": 1750.0},
        {"customer_id": "C005", "name": "Linus Torvalds", "tier": "bronze", "region": "emea", "lifetime_value": 300.0},
        {"customer_id": "C006", "name": "Margaret Hamilton", "tier": "bronze", "region": "apac", "lifetime_value": 420.0},
    ],
    "Product": [
        {"sku": "P100", "title": "Trail Runner Shoes", "category": "footwear", "price": 129.0},
        {"sku": "P101", "title": "Alpine Down Jacket", "category": "apparel", "price": 249.0},
        {"sku": "P102", "title": "Ultralight Tent 2P", "category": "camping", "price": 399.0},
        {"sku": "P103", "title": "Insulated Water Bottle", "category": "accessories", "price": 35.0},
        {"sku": "P104", "title": "Merino Base Layer", "category": "apparel", "price": 89.0},
        {"sku": "P105", "title": "Headlamp Pro", "category": "camping", "price": 59.0},
    ],
    "Order": [
        {"order_id": "O1001", "customer_id": "C001", "sku": "P102", "region": "emea", "status": "delivered", "total": 399.0},
        {"order_id": "O1002", "customer_id": "C001", "sku": "P104", "region": "emea", "status": "shipped", "total": 89.0},
        {"order_id": "O1003", "customer_id": "C002", "sku": "P101", "region": "apac", "status": "delivered", "total": 249.0},
        {"order_id": "O1004", "customer_id": "C002", "sku": "P100", "region": "apac", "status": "returned", "total": 129.0},
        {"order_id": "O1005", "customer_id": "C003", "sku": "P105", "region": "emea", "status": "placed", "total": 59.0},
        {"order_id": "O1006", "customer_id": "C004", "sku": "P103", "region": "apac", "status": "delivered", "total": 35.0},
        {"order_id": "O1007", "customer_id": "C005", "sku": "P100", "region": "emea", "status": "placed", "total": 129.0},
        {"order_id": "O1008", "customer_id": "C006", "sku": "P104", "region": "apac", "status": "shipped", "total": 89.0},
    ],
}

# ---- Agent Memory: Dana ----

# Five facts about Dana, a made-up Cairn customer. The notebook adds the id, owner, and
# namespace. Each question below shares no word stem with the fact it is after.
DANA_FACTS: list[dict] = [
    {
        "text": (
            "Dana is allergic to peanuts and tree nuts, so trail snacks and camp meals must not "
            "contain them."
        ),
        "topics": ["food", "allergy"],
    },
    {
        "text": "Dana has wide feet, so she needs boots half a size bigger than her usual size.",
        "topics": ["sizing", "footwear"],
    },
    {
        "text": "Dana is planning a winter trek across Patagonia in February and gets cold easily.",
        "topics": ["travel", "trip"],
    },
    {
        "text": "Dana prefers gear made from recycled materials and will pay extra for it.",
        "topics": ["preferences", "sustainability"],
    },
    {
        "text": "Dana wants parcels sent express, with no plastic packaging.",
        "topics": ["shipping", "packaging"],
    },
]

# The recall example's questions. target is the 1-based index into DANA_FACTS.
MEMORY_QUESTIONS: list[dict] = [
    {"text": "What should the kitchen avoid serving me?", "target": 1},
    {"text": "My toes feel squashed on long descents.", "target": 2},
    {"text": "Somewhere freezing to visit next year?", "target": 3},
]

# One short conversation for the session example. The two USER facts are new, so anything the
# service extracts from them is a new record under the same owner. The last turn is the first
# memory question, asked inside a session.
SESSION_TURNS: list[dict] = [
    {"role": "USER", "text": "I am training for a 50 kilometre trail race in April."},
    {
        "role": "ASSISTANT",
        "text": "Noted: a 50 km race in April. Do steep descents give you any trouble?",
    },
    {
        "role": "USER",
        "text": "My left knee complains on steep downhills, so I always carry trekking poles.",
    },
    {"role": "USER", "text": "What should the kitchen avoid serving me?"},
]
