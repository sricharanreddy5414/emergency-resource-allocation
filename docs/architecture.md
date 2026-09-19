# Architecture

## Overview

The Emergency Resource Allocation Platform is a serverless, event-driven AWS application designed for fictional/test resource-coordination scenarios.

The platform accepts resource requests, checks available resources, applies predefined priority-based allocation rules, stores allocation data in DynamoDB, publishes successful allocation events through EventBridge, and sends notifications through SNS.

The system is intended for educational and demonstration purposes and is not a real medical or emergency-dispatch system.

## AWS Architecture

```text
                         User / Client
                              |
                              v
                    Amazon S3 Static Website
                              |
                              v
                     Amazon API Gateway
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
      POST /allocate    /requests       /allocate/resources
             |                |                |
             v                v                v
     Allocation Lambda   Request Lambda   Resource Lambda
             |
             |
             v
          DynamoDB
      +------+-------+----------------+
      |              |                |
      v              v                v
 Resources    EmergencyRequests   Allocations
      |
      |
      v
 Conditional Resource Update
      |
      v
  EventBridge
      |
      v
 SNS Topic
      |
      v
 Email Notification

 Lambda / API activity
          |
          v
     CloudWatch
   Logs + Metrics
        + Alarm

 IAM
  |
  +--> Controls Lambda and AWS service permissions

 AWS Budgets
  |
  +--> Cost monitoring and spending alerts

Main Components
1. Amazon S3

Amazon S3 hosts the static frontend website of the application.

The frontend provides the user interface for:

Viewing the dashboard
Viewing available resources
Viewing emergency requests
Viewing allocation records
Viewing notifications
Searching and filtering project data

The current frontend is deployed as an S3 static website.

CloudFront HTTPS deployment is planned as a future enhancement.

2. Amazon API Gateway

Amazon API Gateway provides the REST API layer between the frontend and the AWS backend.

The current API routes are:

Method	Endpoint	Purpose
POST	/allocate	Process a resource allocation
GET	/requests	Retrieve emergency requests
POST	/requests	Create an emergency request
OPTIONS	/requests	Provide CORS support
GET	/allocate/resources	Retrieve resources
GET	/allocate/allocations	Retrieve allocation records

API Gateway forwards requests to the appropriate Lambda functions.

3. AWS Lambda

The backend uses separate Lambda functions to divide responsibilities.

Allocation Lambda

Function:

emergency-resource-allocation

The Allocation Lambda is responsible for:

Validating allocation requests
Checking request status
Processing pending requests
Applying priority-based processing
Matching resource type
Matching resource location
Checking resource availability
Reserving resources
Creating allocation records
Updating request status
Publishing successful allocation events
Request Lambda

The Request Lambda handles creation of emergency resource requests through:

POST /requests

It stores request information in the EmergencyRequests DynamoDB table.

Resource Lambda

The Resource Lambda handles retrieval of resource information through:

GET /allocate/resources

It retrieves resource information from the Resources DynamoDB table.

4. Amazon DynamoDB

The application uses three DynamoDB tables.

Resources Table

The Resources table stores information about resources.

Primary key:

resource_id

Important attributes include:

resource_id
Type
Location
Available

Example resource:

resource_id: R001
Type: ICU_BED
Location: Bangalore
Available: false
EmergencyRequests Table

The EmergencyRequests table stores incoming resource requests.

Primary key:

request_id

Important attributes include:

request_id
ResourceType
Location
Priority
Status
CreatedAt

Example request:

request_id: REQ001
ResourceType: ICU_BED
Location: Bangalore
Priority: 1
Status: PENDING
Allocations Table

The Allocations table stores successful allocation records.

Primary key:

allocation_id

Important attributes include:

allocation_id
request_id
resource_id
resource_type
location
priority
status

Example:

allocation_id: ALLOC-REQ001
request_id: REQ001
resource_id: R001
status: ALLOCATED
Resource Matching

The allocation engine searches for resources that satisfy the request requirements.

A resource is considered suitable when:

Resource Type matches request
AND
Location matches request
AND
Available = true

For example:

Request:
Resource Type = ICU_BED
Location = Bangalore

Resource:
Type = ICU_BED
Location = Bangalore
Available = true

The resource can then be considered for allocation.

Priority-Based Processing

Pending requests are processed using their predefined priority values.

The allocation engine sorts pending requests by priority before attempting resource allocation.

This provides deterministic processing based on the priority value stored with each request.

The system does not use artificial intelligence or machine learning for prioritization.

Conditional Resource Reservation

When a suitable resource is found, the allocation Lambda performs a conditional DynamoDB update.

The update requires the resource to still have:

Available = true

The resource is then changed to:

Available = false

This conditional update helps reduce the risk of the same resource being allocated to multiple requests during concurrent processing.

Allocation Record

After successfully reserving a resource, the system creates an allocation record in the Allocations table.

The allocation record connects:

Request
   |
   v
Resource
   |
   v
Allocation

The request status is then updated to:

ALLOCATED

The resource remains unavailable until it is changed back to an available state.

Amazon EventBridge

After a successful allocation, the Allocation Lambda publishes an event to Amazon EventBridge.

The event uses:

source:
emergency.resource.allocation

and:

detail-type:
ResourceAllocated

Example event structure:

{
  "detail-type": "ResourceAllocated",
  "source": "emergency.resource.allocation",
  "detail": {
    "request_id": "REQ001",
    "resource_id": "R001",
    "allocation_id": "ALLOC-REQ001",
    "resource_type": "ICU_BED",
    "location": "Bangalore",
    "priority": 1,
    "status": "ALLOCATED"
  }
}
EventBridge Rule

The project uses the rule:

EmergencyResourceAllocatedRule

The rule matches events with:

source = emergency.resource.allocation

and:

detail-type = ResourceAllocated

When a matching event is received, EventBridge forwards the event to the configured SNS topic.

Amazon SNS

The SNS topic used by the project is:

EmergencyResourceNotifications

SNS receives successful allocation events from EventBridge.

A confirmed email subscription is configured for the topic.

The notification flow is:

Allocation Lambda
       |
       v
  EventBridge
       |
       v
      SNS
       |
       v
 Email Notification

This provides asynchronous notification without requiring the Allocation Lambda to directly manage email delivery.

Amazon CloudWatch

Amazon CloudWatch provides monitoring and logging for the backend.

The project uses CloudWatch for:

Lambda execution logs
Lambda metrics
Error monitoring
Operational troubleshooting
Lambda error alarms

The project includes the alarm:

EmergencyResourceAllocation-Lambda-Errors

The alarm monitors Lambda errors and can send notifications through the configured SNS topic.

IAM

AWS Identity and Access Management controls permissions between the Lambda functions and AWS services.

The Allocation Lambda role provides the permissions required for:

DynamoDB operations
EventBridge event publishing
CloudWatch logging

The project was configured to remove unnecessary broad permissions and use more specific permissions for the resources required by the application.

AWS Budgets

AWS Budgets is used for cost monitoring.

The budget helps track AWS spending during development and testing and can provide alerts when spending approaches configured thresholds.

This is particularly useful for preventing unexpected cloud costs in a student project.

End-to-End Workflow

The complete application workflow is:

1. User interacts with the frontend
             |
             v
2. Frontend sends API request
             |
             v
3. API Gateway receives request
             |
             v
4. Appropriate Lambda function processes request
             |
             v
5. DynamoDB stores or retrieves application data
             |
             v
6. Allocation Lambda checks pending requests
             |
             v
7. Requests are processed according to priority
             |
             v
8. Resource type and location are matched
             |
             v
9. Resource availability is checked
             |
             v
10. Suitable resource is conditionally reserved
             |
             v
11. Allocation record is created
             |
             v
12. Request status is updated
             |
             v
13. ResourceAllocated event is published
             |
             v
14. EventBridge matches the event
             |
             v
15. SNS receives the event
             |
             v
16. Email notification is delivered
             |
             v
17. CloudWatch records backend activity
Request Creation Flow

A new request can be created through the API.

Frontend
   |
   v
POST /requests
   |
   v
API Gateway
   |
   v
Request Lambda
   |
   v
EmergencyRequests Table
   |
   v
Request stored with PENDING status
Resource Retrieval Flow

The frontend can retrieve the current resource information through:

GET /allocate/resources

The flow is:

Frontend
   |
   v
API Gateway
   |
   v
Resource Lambda
   |
   v
Resources Table
   |
   v
Resource information returned to frontend
Allocation Retrieval Flow

Existing allocation records can be retrieved through:

GET /allocate/allocations

The flow is:

Frontend
   |
   v
API Gateway
   |
   v
Allocation Lambda
   |
   v
Allocations Table
   |
   v
Allocation records returned
Monitoring Flow

Backend activity is monitored through CloudWatch.

Lambda Functions
      |
      v
CloudWatch Logs
      |
      +----> Metrics
      |
      +----> Error Monitoring
      |
      +----> Lambda Error Alarm
      |
      v
SNS Notification
Security Architecture

The project uses AWS IAM to control access between services.

The main security principles are:

Use IAM roles for Lambda execution
Limit DynamoDB permissions to required operations
Limit EventBridge permissions to event publishing
Avoid unnecessary broad AWS managed permissions
Keep frontend and backend responsibilities separated
Validate API input before allocation processing

For a production deployment, additional security controls would be required.

These could include:

Strong authentication
Role-based authorization
Encryption requirements
Detailed audit logging
Infrastructure as Code
Secrets management
Network security controls
Compliance controls
Advanced monitoring
Backup and disaster recovery
Scalability

The architecture uses serverless AWS services.

API Gateway and Lambda can handle varying request volumes without requiring the project to manually manage servers.

DynamoDB provides managed database infrastructure, while EventBridge and SNS provide asynchronous event and notification processing.

This architecture reduces the need to maintain traditional server infrastructure.

Reliability Considerations

The architecture separates resource allocation, request management, resource retrieval, event processing, and notification responsibilities.

The conditional resource update provides protection against allocating a resource that is no longer available at the time of the update.

EventBridge and SNS also separate notification processing from the main allocation operation.

However, the project is an educational prototype and does not claim production-grade reliability or exactly-once processing.

Cost Management

The project primarily uses managed/serverless AWS services.

Cost monitoring is supported through AWS Budgets.

During development, unused AWS resources should be reviewed regularly to avoid unnecessary charges.

The project should be shut down or cleaned up when cloud resources are no longer required for testing.

Current Deployment

The backend is deployed in AWS Europe (Stockholm):

Region:
eu-north-1

The frontend is hosted using Amazon S3 static website hosting.

The application is accessed through Amazon API Gateway.

The current architecture uses:

S3
API Gateway
Lambda
DynamoDB
EventBridge
SNS
CloudWatch
IAM
AWS Budgets
Future Enhancements

Possible future improvements include:

Amazon CloudFront HTTPS delivery
Infrastructure as Code using AWS CloudFormation or Terraform
CI/CD pipeline
More comprehensive automated testing
Advanced authentication and authorization
Improved frontend analytics
Detailed audit history
Automatic resource release workflows
More advanced monitoring dashboards
Disaster recovery mechanisms
Production-grade security and compliance controls
Project Scope and Disclaimer

This project uses fictional/test data to demonstrate AWS cloud architecture and serverless application development.

It is an educational prototype and is not intended to:

Diagnose medical conditions
Recommend medical treatment
Dispatch real emergency services
Make patient-specific medical decisions
Replace professional emergency-response systems

The project demonstrates cloud-based resource coordination concepts using AWS services.