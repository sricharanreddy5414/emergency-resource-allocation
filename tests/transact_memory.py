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
        update = entry["Update"]
        names = update.get("ExpressionAttributeNames") or {}
        values = _decode(update.get("ExpressionAttributeValues") or {})
        key = _decode(update["Key"])
        current = _find(tables[update["TableName"]], key)
        condition = _substitute(update.get("ConditionExpression") or "", names)
        if current is None or not _holds(current, condition, values):
            reasons.append({"Code": "ConditionalCheckFailed"})
            failed = True
            pending.append(None)
            continue
        reasons.append({"Code": "None"})
        expression = _substitute(update.get("UpdateExpression") or "", names)
        pending.append((current, _assignments(expression, values)))

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

    for current, fields in pending:
        current.update(fields)


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
