# Database Design

## Overview

The Emergency Resource Allocation Platform uses Amazon DynamoDB as its primary database.

DynamoDB is a fully managed NoSQL database that provides low-latency data access and integrates directly with AWS Lambda.

The project uses three separate DynamoDB tables:

1. `Resources`
2. `EmergencyRequests`
3. `Allocations`

---

## 1. Resources Table

### Table Name

`Resources`

### Primary Key

`resource_id`

### Key Type

`String`

### Purpose

The `Resources` table stores information about resources that can be allocated.

### Attributes

| Attribute   | Type    | Description                                 |
| ----------- | ------- | ------------------------------------------- |
| resource_id | String  | Unique resource identifier                  |
| Type        | String  | Type of resource                            |
| Location    | String  | Resource location                           |
| Available   | Boolean | Indicates whether the resource is available |

### Example Item

```json
{
  "resource_id": "R001",
  "Type": "ICU_BED",
  "Location": "Bangalore",
  "Available": true
}
```

---

## 2. EmergencyRequests Table

### Table Name

`EmergencyRequests`

### Primary Key

`request_id`

### Key Type

`String`

### Purpose

The `EmergencyRequests` table stores incoming resource requests.

### Attributes

| Attribute    | Type   | Description                |
| ------------ | ------ | -------------------------- |
| request_id   | String | Unique request identifier  |
| ResourceType | String | Required resource type     |
| Location     | String | Required resource location |
| Priority     | Number | Request priority           |
| Status       | String | Current request status     |

### Example Item

```json
{
  "request_id": "REQ004",
  "ResourceType": "ICU_BED",
  "Location": "Bangalore",
  "Priority": 1,
  "Status": "PENDING"
}
```

---

## 3. Allocations Table

### Table Name

`Allocations`

### Primary Key

`allocation_id`

### Key Type

`String`

### Purpose

The `Allocations` table stores successful resource allocation records.

### Attributes

| Attribute     | Type   | Description                  |
| ------------- | ------ | ---------------------------- |
| allocation_id | String | Unique allocation identifier |
| request_id    | String | Associated request           |
| resource_id   | String | Allocated resource           |
| resource_type | String | Type of allocated resource   |
| location      | String | Resource location            |
| priority      | Number | Request priority             |
| status        | String | Allocation status            |

### Example Item

```json
{
  "allocation_id": "ALLOC-REQ003",
  "request_id": "REQ003",
  "resource_id": "R001",
  "resource_type": "ICU_BED",
  "location": "Bangalore",
  "priority": 1,
  "status": "ALLOCATED"
}
```

---

## Database Relationship

DynamoDB is a NoSQL database, so these tables do not use traditional relational foreign-key constraints.

However, the application logically connects records using identifiers.

```text
EmergencyRequests
       |
       | request_id
       v
Allocations
       |
       | resource_id
       v
Resources
```

Example:

```text
REQ003
   |
   v
ALLOC-REQ003
   |
   v
R001
```

This allows the application to determine which resource was allocated to a particular request.

---

## Request Lifecycle

An emergency request follows a simple lifecycle:

```text
PENDING
   |
   v
Allocation Processing
   |
   +------ Resource Available ------> ALLOCATED
   |
   +------ No Resource --------------> Remains PENDING
```

### PENDING

The request is waiting for allocation.

### ALLOCATED

A suitable resource has been successfully reserved and an allocation record has been created.

---

## Resource Lifecycle

Resources have an availability state.

```text
Available = true
       |
       v
Allocation
       |
       v
Available = false
```

A resource marked as unavailable cannot be selected by the allocation engine.

---

## Conditional Resource Reservation

The allocation engine uses a DynamoDB conditional update when reserving a resource.

Conceptually:

```text
IF Available == true
        |
        v
Set Available = false
```

If another request has already reserved the resource:

```text
Available == false
        |
        v
Conditional check fails
        |
        v
Try another resource
```

This provides protection against double allocation during concurrent requests.

---

## Allocation Identifier

Each successful allocation receives a unique identifier based on the request.

Example:

```text
Request ID:
REQ003

Allocation ID:
ALLOC-REQ003
```

This provides a simple way to associate an allocation record with its original request.

---

## Database Operations During Allocation

The Lambda function performs the following database operations:

```text
Lambda
   |
   +----> Read EmergencyRequests
   |
   +----> Read Resources
   |
   +----> Update selected Resource
   |
   +----> Create Allocation
   |
   +----> Update EmergencyRequest
```

The resource update uses a conditional expression so that a resource is reserved only when its availability condition is satisfied.

---

## Current Example Data

Example resources used during testing:

| Resource ID | Type        | Location  | Availability        |
| ----------- | ----------- | --------- | ------------------- |
| R001        | ICU_BED     | Bangalore | Available/Allocated |
| R002        | ICU_BED     | Bangalore | Available/Allocated |
| R003        | GENERAL_BED | Bangalore | Available/Allocated |

Example request identifiers used during testing:

```text
REQ001
REQ002
REQ003
REQ004
REQ005
REQ006
```

---

## Why DynamoDB?

DynamoDB was selected because:

* It is fully managed.
* It integrates directly with AWS Lambda.
* It provides fast key-value and document access.
* It supports conditional writes.
* It scales without requiring database server management.
* It fits well with a serverless architecture.

---

## Database Design Summary

The database separates the three main concepts:

```text
Resources
    |
    +---- Available resources

EmergencyRequests
    |
    +---- Incoming allocation requests

Allocations
    |
    +---- Successful allocation records
```

This separation keeps application state organized and allows the Lambda allocation engine to process requests efficiently.

---

## Project Scope

The database contains fictional/test data for demonstrating cloud resource coordination.

It is not designed as a production medical database and does not store real patient information.
