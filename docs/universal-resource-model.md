# Universal resource model

Organizations define resource types and request types. ERAP no longer treats a fixed list such as ICU beds as the platform model. Existing type-name matching still works for records that have no type id.

## ResourceTypes and RequestTypes

Both tables use `organization_id` as the partition key and `resource_type_id` or `request_type_id` as the sort key. Types are not shared across organizations. `DELETE` sets `INACTIVE`. Existing resources and requests may keep the reference. New records require an `ACTIVE` type.

## Attributes

A type stores `attributes_schema.fields`. Each field has a `key`, a `type` of `string`, `number`, or `boolean`, and an optional `required` flag. Values are rejected when the key is unknown, the shape is nested, or the payload exceeds the size limit. There are at most 12 fields.

## Matching

Inside one organization, a typed request matches a resource when:

- the resource type id is listed in `matching_config.compatible_resource_type_ids`, or the legacy type names are equal when that list is empty
- the resource is available
- required numeric attributes meet their minimum
- the same location is preferred unless `same_location_preferred` is false

Resources from another organization are ignored. `match` on an allocation response lists the reasons.

## Visibility

Resources default to `PRIVATE`. `PUBLIC` is explicit. Public items are written with `visibility_key=PUBLIC` and `discovery_key`, which feeds `PublicDiscoveryIndex`. Private items omit those keys, so they are not in the index.

`GET /public/resources` does not use Cognito. It queries only that index and returns resource type, public name, city, state, description, contact, and availability when the organization opted to show availability. It does not return organization ids, resource ids, attributes, or actor ids.

## Compatibility

The four legacy resources, 31 requests, 22 allocations, and 17 history rows stay unchanged and unscoped. Tenant queries use organization indexes, so those rows stay out of organization operations. No ownership is invented for them.

## Roles

Owners and admins manage types. Operators and members can read types. Resource and request operations keep the existing role checks. The API ignores client-supplied role and user id.

## Pagination

Catalog and public lists accept `limit` and an opaque `page_token`. A token for another organization is rejected. Authenticated resource listing stays a JSON array unless `limit` or `page_token` is sent, so the current dashboard can keep reading an array. Each query is limited to 100 items.
