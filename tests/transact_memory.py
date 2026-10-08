"""In-memory TransactWriteItems for release tests.

Conditions are checked against the pre-transaction rows. A failed condition
writes nothing.
"""

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

_decoder = TypeDeserializer()


def apply_transact(tables, transact_items):
    pending = []
    reasons = []
    failed = False

    for entry in transact_items:
        if "Update" in entry:
            ready, action = _prepare_update(tables, entry["Update"])
        elif "Put" in entry:
            ready, action = _prepare_put(tables, entry["Put"])
        else:
            raise AssertionError("unsupported transaction action")
        if ready:
            reasons.append({"Code": "None"})
            pending.append(action)
            continue
        reasons.append({"Code": "ConditionalCheckFailed"})
        failed = True

    if failed:
        raise ClientError(
            {
                "Error": {
                    "Code": "TransactionCanceledException",
                    "Message": "Transaction cancelled",
                },
                "CancellationReasons": reasons,
            },
            "TransactWriteItems",
        )

    for action in pending:
        action()


def _prepare_update(tables, update):
    names = update.get("ExpressionAttributeNames") or {}
    values = _decode(update.get("ExpressionAttributeValues") or {})
    key = _decode(update["Key"])
    current = _find(tables[update["TableName"]], key)
    condition = _substitute(update.get("ConditionExpression") or "", names)
    if current is None or not _holds(current, condition, values):
        return False, None
    fields = _assignments(_substitute(update.get("UpdateExpression") or "", names), values)

    def apply(current=current, fields=fields):
        current.update(fields)

    return True, apply


def _prepare_put(tables, put):
    item = _decode(put["Item"])
    rows = tables[put["TableName"]]
    names = put.get("ExpressionAttributeNames") or {}
    values = _decode(put.get("ExpressionAttributeValues") or {})
    key_name = next(
        (name for name in ("allocation_id", "resource_id", "request_id") if name in item),
        None,
    )
    current = _find(rows, {key_name: item[key_name]}) if key_name else None
    condition = _substitute(put.get("ConditionExpression") or "", names)
    if not _holds(current or {}, condition, values):
        return False, None

    def apply(rows=rows, item=item):
        rows.append(dict(item))

    return True, apply


def _decode(values):
    return {key: _decoder.deserialize(value) for key, value in values.items()}


def _find(rows, key):
    for row in rows:
        if all(row.get(name) == value for name, value in key.items()):
            return row
    return None


def _substitute(expression, names):
    rendered = expression
    for placeholder, attribute in names.items():
        rendered = rendered.replace(placeholder, attribute)
    return rendered


def _split_top(expression, operator):
    needle = f" {operator} "
    parts = []
    depth = 0
    start = 0
    index = 0
    while index < len(expression):
        character = expression[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and expression.startswith(needle, index):
            parts.append(expression[start:index].strip())
            index += len(needle)
            start = index
            continue
        index += 1
    parts.append(expression[start:].strip())
    return [part for part in parts if part]


def _holds(item, expression, values):
    if not expression:
        return True
    return all(_clause(item, part, values) for part in _split_top(expression, "AND"))


def _clause(item, clause, values):
    text = clause.strip()
    if text.startswith("(") and text.endswith(")"):
        return any(_clause(item, part, values) for part in _split_top(text[1:-1], "OR"))
    if text.startswith("attribute_exists(") and text.endswith(")"):
        return text[len("attribute_exists(") : -1] in item
    if text.startswith("attribute_not_exists(") and text.endswith(")"):
        return text[len("attribute_not_exists(") : -1] not in item
    left, right = text.split(" = ", 1)
    return item.get(left.strip()) == values[right.strip()]


def _assignments(expression, values):
    body = expression.strip()
    if body.startswith("SET "):
        body = body[4:]
    assigned = {}
    for part in body.split(","):
        left, right = part.split("=", 1)
        assigned[left.strip()] = values[right.strip()]
    return assigned
