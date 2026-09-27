# Pilot data safety

This is operational guidance for what to type into ERAP. It is not legal advice and it does not claim a compliance certification.

## What to enter

Use information the organization needs to allocate a resource:

- Organization name.
- Location name, city, and state.
- Resource type and request type names.
- Resource id and a short operational name.
- Attribute values defined by the type, such as capacity or a category.
- A contact method that the organization is willing to show to the people who should see that resource.

## What never to enter

Do not put these in any field, including attributes, descriptions, or contact:

- Passwords, API keys, or Cognito tokens.
- Government ID numbers, financial account numbers, or medical details.
- Home addresses or private phone numbers of people who have not agreed to be contacted for this operation.
- Notes that are only for one person and are not needed to match a resource.

Attributes are stored for the organization. They are not a place to hide secrets. A later edit or audit view can show them to authorized members.

## PRIVATE and PUBLIC

PRIVATE is the default. Only signed-in members of that organization can use a private resource in the application.

PUBLIC means the public discovery endpoint can return the resource without a login. The public response is limited to the fields the product allows: resource type, public name, city, state, description, contact, and availability when the resource explicitly shows availability.

The public response does not include the organization id, internal resource id, user ids, or the private attribute map. That restriction is why public text must already be safe. Do not rely on a private attribute staying hidden if you also paste it into the public description.

## Tenant isolation

Each organization sees its own locations, types, resources, requests, allocations, and audit events. Another organization's id in a request does not grant access. The API uses the Cognito user and the membership stored for that user.

Legacy rows created before organizations existed are not part of the pilot organization. Leave them as they are.

## Audit logging

Creates, updates, visibility changes, allocations, and releases write an audit event. The event records the action and identifiers. It does not record passwords, tokens, or authorization headers.

API logs record the request id, route, operation, status, and duration. They are for operators investigating a failure. They are not a copy of the form the user submitted.
