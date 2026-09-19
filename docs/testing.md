# Testing and Validation

## Overview

The Emergency Resource Allocation Platform was tested using multiple scenarios to verify resource matching, priority-based allocation, resource exhaustion handling, concurrency protection, event-driven notifications, monitoring, and IAM permissions.

The tests use fictional/test data and are intended to validate the cloud application architecture and allocation workflow.

---

## Testing Objectives

The main testing objectives are:

- Verify that suitable resources can be allocated.
- Verify that request priority is considered.
- Verify that unavailable resources are not allocated.
- Verify that concurrent requests do not successfully allocate the same resource twice.
- Verify that successful allocations create allocation records.
- Verify that request status is updated after allocation.
- Verify that EventBridge receives allocation events.
- Verify that SNS sends notification emails.
- Verify that CloudWatch records Lambda execution activity.
- Verify that Lambda errors can be monitored.
- Verify that required IAM permissions are sufficient.

---

# Test Case 1 — Normal Resource Allocation

## Objective

Verify that a pending request can receive a suitable available resource.

## Input

```text
Request ID: REQ003
Resource Type: ICU_BED
Location: Bangalore
Priority: 1
Status: PENDING

Expected Result

A matching available resource should be allocated.

Actual Result

The request was successfully allocated.

Example response:

{
  "message": "Resource allocated successfully",
  "request_id": "REQ003",
  "resource_id": "R001",
  "allocation_id": "ALLOC-REQ003",
  "status": "ALLOCATED"
}
Status

PASS

Test Case 2 — Resource Matching
Objective

Verify that the allocation engine selects a resource with matching type and location.

Matching Conditions
Resource Type = Requested Resource Type
AND
Resource Location = Requested Location
AND
Available = true
Example
Request:
ICU_BED
Bangalore

Resource:
ICU_BED
Bangalore
Available = true

The resource satisfies all matching conditions.

Status

PASS

Test Case 3 — Priority-Based Allocation
Objective

Verify that requests are processed according to predefined priority rules.

Priority Model
Priority 1 → Higher priority
Priority 2 → Medium priority
Priority 3 → Lower priority
Example
REQ001 → Priority 2
REQ002 → Priority 1
REQ003 → Priority 3

The allocation engine sorts pending requests by priority before processing them.

Therefore, higher-priority requests are processed before lower-priority requests.

Status

PASS

Test Case 4 — Resource Exhaustion
Objective

Verify system behavior when no suitable resource is available.

Scenario

All matching resources are unavailable.

Example:

R001 → ICU_BED → Available = false
R002 → ICU_BED → Available = false

A new ICU bed request is submitted.

Expected Result

The system should not allocate an unavailable resource.

Actual Result

The API returned:

{
  "message": "No suitable resource available"
}
Status

PASS

Test Case 5 — Concurrent Requests
Objective

Verify that two requests arriving at approximately the same time cannot successfully reserve the same resource.

Scenario

Two API requests are sent concurrently while one suitable resource is available.

Request A ────────┐
                  ├──> Same available resource
Request B ────────┘
Expected Result

Only one request should successfully reserve the resource.

Actual Result

The concurrent test produced:

Request 1 → Resource allocated successfully
Request 2 → No suitable resource available

Example successful response:

{
  "message": "Resource allocated successfully",
  "request_id": "REQ005",
  "resource_id": "R001",
  "allocation_id": "ALLOC-REQ005",
  "status": "ALLOCATED"
}

The second request did not successfully allocate the same resource.

Why It Works

The Lambda function uses a DynamoDB conditional update:

Condition:
Available == true

Update:
Available = false

If another request has already changed the value to false, the conditional operation fails.

This provides protection against double allocation during the tested concurrent scenario.

Status

PASS

Test Case 6 — Allocation Record Creation
Objective

Verify that a successful allocation creates a record in the Allocations table.

Expected Result

After successful allocation, the following information should be stored:

Allocation ID
Request ID
Resource ID
Resource Type
Location
Priority
Status
Example
{
  "allocation_id": "ALLOC-REQ003",
  "request_id": "REQ003",
  "resource_id": "R001",
  "resource_type": "ICU_BED",
  "location": "Bangalore",
  "priority": 1,
  "status": "ALLOCATED"
}
Status

PASS

Test Case 7 — Request Status Update
Objective

Verify that a successfully processed request changes from PENDING to ALLOCATED.

Before Allocation
Status = PENDING
After Successful Allocation
Status = ALLOCATED
Status

PASS

Test Case 8 — EventBridge Integration
Objective

Verify that a successful allocation publishes an event to Amazon EventBridge.

Event Details
Source:
emergency.resource.allocation

Detail Type:
ResourceAllocated
Expected Flow
Lambda
   |
   v
EventBridge
Actual Result

The EventBridge integration was tested using the allocation event workflow.

The event publishing operation is configured through the Lambda events:PutEvents permission.

A successful EventBridge API response reports:

FailedEntryCount = 0
Status

PASS

Test Case 9 — SNS Notification
Objective

Verify that a successful allocation triggers an email notification through the EventBridge and SNS integration.

Expected Flow
Lambda
   |
   v
EventBridge
   |
   v
SNS
   |
   v
Email
Actual Result

The SNS notification workflow was tested using the confirmed email subscription.

The notification was successfully delivered to the configured email endpoint.

Status

PASS

Test Case 10 — CloudWatch Logging
Objective

Verify that Lambda execution activity is recorded in Amazon CloudWatch.

Log Group
/aws/lambda/emergency-resource-allocation
Important Log Activities

The CloudWatch logs provide visibility into activities such as:

Request received
Request validation
Resource scanning
Resource matching
Resource reservation
Allocation creation
Request status update
EventBridge event publishing
Execution result

These logs provide visibility into the allocation workflow and help with troubleshooting.

Status

PASS

Test Case 11 — Lambda Error Monitoring
Objective

Verify that Lambda errors can be monitored through a CloudWatch alarm.

Alarm
EmergencyResourceAllocation-Lambda-Errors
Configuration
Metric:
Errors

Statistic:
Sum

Period:
5 minutes

Condition:
Greater than or equal to 1

The alarm is connected to the configured SNS notification topic.

Purpose

If Lambda errors occur, the monitoring system can generate an alert.

Status

CONFIGURED

Test Case 12 — IAM Permission Validation
Objective

Verify that the Lambda function operates using the required restricted permissions.

Security Configuration

The Lambda execution role was configured using a least-privilege approach.

Required DynamoDB operations include:

dynamodb:Scan
dynamodb:UpdateItem
dynamodb:PutItem

EventBridge permission:

events:PutEvents
Validation

After unnecessary broad DynamoDB and EventBridge permissions were removed, the Lambda function was tested again.

The function successfully accessed the required AWS resources without an AccessDeniedException.

Status

PASS

Test Results Summary
Test Case	Result
Normal Resource Allocation	PASS
Resource Matching	PASS
Priority-Based Allocation	PASS
Resource Exhaustion	PASS
Concurrent Requests	PASS
Allocation Record Creation	PASS
Request Status Update	PASS
EventBridge Integration	PASS
SNS Notification	PASS
CloudWatch Logging	PASS
Lambda Error Monitoring	CONFIGURED
IAM Permission Validation	PASS
End-to-End Validation

The complete workflow was validated as:

Client
   |
   v
API Gateway
   |
   v
Lambda
   |
   +----> DynamoDB
   |
   v
Resource Allocation
   |
   v
EventBridge
   |
   v
SNS
   |
   v
Email Notification

CloudWatch logs were used to verify Lambda execution and allocation processing.

Testing Tools

The following tools were used during testing:

AWS Management Console
Amazon API Gateway
AWS Lambda
Amazon DynamoDB
Amazon EventBridge
Amazon SNS
Amazon CloudWatch
PowerShell
curl.exe
Testing Conclusion

The testing demonstrates that the platform can:

Match requests with suitable resources.
Process requests using predefined priority rules.
Detect resource exhaustion.
Prevent successful double allocation during the tested concurrent scenario.
Store successful allocation records.
Update request status.
Publish allocation events.
Send automated SNS notifications.
Generate CloudWatch logs.
Monitor Lambda errors.
Operate with restricted IAM permissions.

The tests provide evidence that the implemented AWS architecture and allocation workflow are functioning as designed for the project's fictional/test-data scenario.

Project Testing Scope

All testing was performed using fictional/test data.

The testing validates the technical AWS workflow and is not evidence of suitability for real medical, emergency-dispatch, or patient-specific applications.