# Database Design

## Overview

The Emergency Resource Allocation Platform uses Amazon DynamoDB as its primary database.

DynamoDB is a fully managed NoSQL database that provides low-latency data access and integrates directly with AWS Lambda.

The project uses three separate DynamoDB tables:

1. `Resources`
2. `EmergencyRequests`
3. `Allocations`

---

# 1. Resources Table

## Table Name

`Resources`

## Primary Key

`resource_id`

## Key Type

`String`

## Purpose

The `Resources` table stores information about resources that can be allocated.

## Attributes

| Attribute | Type | Description |
|---|---|---|
| `resource_id` | String | Unique resource identifier |
| `Type` | String | Type of resource |
| `Location` | String | Resource location |
| `Available` | Boolean | Indicates whether the resource is currently available |

## Example Item

```json
{
  "resource_id": "R001",
  "Type": "ICU_BED",
  "Location": "Bangalore",
  "Available": true
}

Resource Availability

The Available attribute controls whether a resource can be considered for allocation.

Available = true
        |
        v
Resource can be selected

After successful allocation:

Available = false
        |
        v
Resource cannot be selected
2. EmergencyRequests Table
Table Name

EmergencyRequests

Primary Key

request_id

Key Type

String

Purpose

The EmergencyRequests table stores incoming resource requests submitted to the platform.

Attributes
Attribute	Type	Description
request_id	String	Unique request identifier
ResourceType	String	Required resource type
Location	String	Required resource location
Priority	Number	Request priority
Status	String	Current request status
CreatedAt	String	Request creation timestamp
Example Item
{
  "request_id": "REQ004",
  "ResourceType": "ICU_BED",
  "Location": "Bangalore",
  "Priority": 1,
  "Status": "PENDING",
  "CreatedAt": "2026-09-19T17:34:17.657184+00:00"
}
Request Status

The application primarily uses the following request states:

PENDING
   |
   v
Allocation Processing
   |
   v
ALLOCATED

A request remains PENDING when no suitable resource is available.

3. Allocations Table
Table Name

Allocations

Primary Key

allocation_id

Key Type

String

Purpose

The Allocations table stores records of successfully allocated resources.

Attributes
Attribute	Type	Description
allocation_id	String	Unique allocation identifier
request_id	String	Associated request identifier
resource_id	String	Allocated resource identifier
resource_type	String	Type of allocated resource
location	String	Resource location
priority	Number	Request priority
status	String	Allocation status
Example Item
{
  "allocation_id": "ALLOC-REQ003",
  "request_id": "REQ003",
  "resource_id": "R001",
  "resource_type": "ICU_BED",
  "location": "Bangalore",
  "priority": 1,
  "status": "ALLOCATED"
}
Database Relationship

DynamoDB is a NoSQL database, so these tables do not use traditional relational foreign-key constraints.

However, the application logically connects records using identifiers.

EmergencyRequests
       |
       | request_id
       v
Allocations
       |
       | resource_id
       v
Resources

Example:

REQ003
   |
   v
ALLOC-REQ003
   |
   v
R001

This allows the application to determine which resource was allocated to a particular request.

Request Lifecycle

An emergency resource request follows this workflow:

PENDING
   |
   v
Allocation Processing
   |
   +------ Suitable Resource ------> ALLOCATED
   |
   +------ No Suitable Resource ---> Remains PENDING
PENDING

The request has been created and is waiting for resource allocation.

ALLOCATED

A suitable resource has been successfully reserved and an allocation record has been created.

Resource Matching

The allocation engine searches the Resources table for a resource satisfying all required conditions.

Resource Type matches request
          AND
Location matches request
          AND
Available = true

For example:

Request:
ResourceType = ICU_BED
Location = Bangalore

Resource:
Type = ICU_BED
Location = Bangalore
Available = true

The resource satisfies the required matching conditions.

Priority Processing

Pending requests are processed according to their predefined priority.

The project uses:

Priority 1 → Higher priority
Priority 2 → Medium priority
Priority 3 → Lower priority

The allocation Lambda processes pending requests in priority order before attempting allocation.

Conditional Resource Reservation

The allocation engine uses a DynamoDB conditional update when reserving a resource.

Conceptually:

IF Available == true
        |
        v
Set Available = false

If another request has already reserved the resource:

Available == false
        |
        v
Conditional check fails
        |
        v
Resource cannot be reserved by that request

The implemented condition is:

Available = true

before changing the resource to:

Available = false

This provides protection against successful double allocation during the tested concurrent scenario.

Allocation Identifier

Each successful allocation receives an identifier based on the request identifier.

Example:

Request ID:
REQ003

Allocation ID:
ALLOC-REQ003

This provides a simple logical association between the request and its allocation record.

Database Operations During Allocation

The Allocation Lambda performs the following database operations:

Allocation Lambda
       |
       +----> Scan EmergencyRequests
       |
       +----> Scan Resources
       |
       +----> Conditionally update selected Resource
       |
       +----> Put Allocation record
       |
       +----> Update EmergencyRequest

The resource update uses a conditional expression so that a resource is reserved only when it is still available.

Database Operations by Lambda
Allocation Lambda

The Allocation Lambda interacts with:

Resources
EmergencyRequests
Allocations

It performs operations required for allocation processing and allocation retrieval.

Request Lambda

The Request Lambda creates request records in:

EmergencyRequests
Resource Lambda

The Resource Lambda retrieves resource information from:

Resources
Current Example Data

Example resources used during testing include:

Resource ID	Type	Location	Availability
R001	ICU_BED	Bangalore	Allocated
R002	ICU_BED	Bangalore	Allocated
R003	GENERAL_BED	Bangalore	Allocated
R-AUDIT-001	AUDIT_BED	AUDIT-LAB	Test Resource

The R-AUDIT-001 resource was created as an isolated test resource for validating the allocation workflow.

Example request identifiers used during testing include:

REQ001
REQ002
REQ003
REQ004
REQ005
REQ006
REQ007
REQ008
REQ009
REQ010
REQ011
REQ-TEST-001
REQ-TEST-002
REQ-TEST-003
REQ-TEST-004
REQ-AUDIT-001
REQ-API-AUDIT-001
REQ-SUCCESS-AUDIT-001
DynamoDB and API Integration

The DynamoDB tables are accessed by AWS Lambda functions through API Gateway endpoints.

The high-level flow is:

Frontend
   |
   v
API Gateway
   |
   v
Lambda
   |
   v
DynamoDB

For allocation:

POST /allocate
       |
       v
Allocation Lambda
       |
       +----> EmergencyRequests
       |
       +----> Resources
       |
       +----> Allocations

For request creation:

POST /requests
       |
       v
Request Lambda
       |
       v
EmergencyRequests

For resource retrieval:

GET /allocate/resources
       |
       v
Resource Lambda
       |
       v
Resources
Why DynamoDB?

DynamoDB was selected because:

It is fully managed.
It integrates directly with AWS Lambda.
It provides fast key-value and document access.
It supports conditional writes.
It scales without requiring database server management.
It fits well with a serverless architecture.
It reduces infrastructure management requirements for the project.
Database Design Summary

The database separates the three main concepts:

Resources
    |
    +---- Available and allocated resources


EmergencyRequests
    |
    +---- Incoming resource requests


Allocations
    |
    +---- Successful allocation records

The separation keeps application state organized and allows the Lambda allocation engine to process requests using the appropriate DynamoDB tables.

Data Consistency and Concurrency

The application uses a conditional update when reserving resources.

The important operation is:

Check:
Available == true

Then:
Available = false

This ensures that the resource must still be available at the time of the conditional update.

If the condition is no longer satisfied, the update does not succeed.

This mechanism was tested during the concurrent request scenario and prevented both tested requests from successfully reserving the same resource.

Project Scope

The DynamoDB tables contain fictional/test data for demonstrating cloud-based resource coordination.

The database does not contain real patient information and is not designed as a production medical database.

For a production system, additional requirements would be needed, including stronger security controls, encryption policies, backup strategies, auditing, access controls, compliance requirements, and disaster recovery mechanisms.