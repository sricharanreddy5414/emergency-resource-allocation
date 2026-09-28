# ERAP frontend design system

ERAP is an emergency operations workspace. The interface is calm, dense enough for operations, and consistent across Overview, Resources, Requests, Allocations, and Organization.

## Principles

Information comes before decoration. Status is readable without flashing color. Motion stays short. The frontend does not invent metrics, notifications, or historical trends.

## Color

Tokens live in `frontend/style.css`.

- Background is a cool neutral.
- Surfaces are white.
- Text is deep navy.
- Primary is an operational blue.
- Success, warning, danger, and emergency are reserved for status.
- Gradients are limited to a faint page wash.

## Typography

The UI uses Segoe UI, then Inter, then Arial. Page titles stay compact. Labels and table headers are smaller and uppercase. Metadata is muted.

## Spacing and shape

The base step is 8px. Cards and controls use a 12px radius and a light shadow. Tables scroll horizontally on small screens instead of crushing columns.

## Status

Badges use the existing status values:

- AVAILABLE is green.
- ALLOCATED and RELEASED are blue.
- PENDING and WAITING are amber.
- PUBLIC and PRIVATE are separate visibility badges.
- Priority uses a restrained emergency tint.

## Components

Buttons, inputs, selects, panels, tables, badges, modals, toasts, and empty states share the same tokens. Member removal keeps the existing confirmation dialog. Role changes, reactivation, invitation cancellation, and release keep their existing confirmation behavior.

## Motion and accessibility

Transitions are about 180ms. `prefers-reduced-motion` removes animation and transitions. Interactive controls keep visible focus, labels, and button semantics.

## Responsive behavior

From 1100px, metric grids become two columns. From 780px, navigation collapses behind the menu button, the top bar stacks, and the profile name hides so the switchers remain usable.

## What the interface does not do

It does not chart history that the API does not provide. It does not add a public discovery page beyond the existing public API. It does not treat client-side role checks as authorization. Organization, location, request type, and resource type values still come from the existing APIs as `organization_id`, `location_id`, `request_type_id`, and `resource_type_id`.
