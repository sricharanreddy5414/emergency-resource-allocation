# Emergency Resource Allocation Platform

A serverless, event-driven AWS application for coordinating fictional resource requests using predefined priority-based allocation rules.

> **Educational scope:** This project uses fictional/test data for learning and demonstration. It is not a real medical system, emergency-dispatch system, or medical decision-making system.

---

## Project Overview

The Emergency Resource Allocation Platform demonstrates how AWS serverless and event-driven services can be combined to build a cloud-based resource coordination application.

The platform allows users to:

- Create resource requests
- View resource availability
- View existing requests
- Process resource allocation
- Match resources by type and location
- Process pending requests using predefined priorities
- Prevent successful double allocation during concurrent reservations
- Store allocation records
- Update request status
- Publish allocation events
- Send email notifications
- Monitor backend activity with CloudWatch

---

## Architecture

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
  Amazon EventBridge
      |
      v
  Amazon SNS
      |
      v
 Email Notification

 Lambda Activity
      |
      v
 Amazon CloudWatch
 Logs + Metrics + Alarm

 AWS IAM
      |
      v
 Permission Control

 AWS Budgets
      |
      v
 Cost Monitoring

 Architecture diagram source:

architecture/architecture.mmd

Rendered diagram:

architecture/architecture-diagram.png
AWS Services Used
AWS Service	Purpose
Amazon S3	Hosts the static frontend
Amazon API Gateway	Provides REST API endpoints
AWS Lambda	Runs backend application logic
Amazon DynamoDB	Stores resources, requests, and allocations
Amazon EventBridge	Routes successful allocation events
Amazon SNS	Sends notification emails
Amazon CloudWatch	Provides logs, metrics, and alarms
AWS IAM	Controls service permissions
AWS Budgets	Monitors project spending
System Workflow
User
  |
  v
S3 Static Website
  |
  v
API Gateway
  |
  +-----------------------------+
  |              |              |
  v              v              v
/allocate     /requests     /allocate/resources
  |              |              |
  v              v              v
Allocation     Request       Resource
Lambda         Lambda        Lambda
  |
  v
Check Pending Requests
  |
  v
Apply Priority Rules
  |
  v
Match Resource Type
  |
  v
Match Location
  |
  v
Check Availability
  |
  v
Conditionally Reserve Resource
  |
  v
Create Allocation Record
  |
  v
Update Request Status
  |
  v
Publish ResourceAllocated Event
  |
  v
EventBridge
  |
  v
SNS
  |
  v
Email Notification
Allocation Logic

The allocation engine uses deterministic, predefined rules rather than Artificial Intelligence or Machine Learning.

The process is:

Read pending requests.
Sort pending requests by priority.
Process higher-priority requests first.
Match the requested resource type.
Match the requested location.
Check whether the resource is available.
Conditionally reserve the resource.
Create an allocation record.
Update the request status to ALLOCATED.
Publish a ResourceAllocated event.
Deliver a notification through SNS.

A suitable resource must satisfy:

Resource Type matches requested type
AND
Location matches requested location
AND
Available = true
Concurrency Protection

The allocation Lambda uses a DynamoDB conditional update when reserving a resource.

Conceptually:

Condition:
Available == true

Update:
Available = false

If another request has already reserved the resource, the condition is no longer satisfied.

This provides protection against successful double allocation during the tested concurrent scenario.

Database Design

The project uses three DynamoDB tables.

1. Resources

Primary key:

resource_id

Important attributes:

resource_id
Type
Location
Available

Example:

{
  "resource_id": "R001",
  "Type": "ICU_BED",
  "Location": "Bangalore",
  "Available": true
}
2. EmergencyRequests

Primary key:

request_id

Important attributes:

request_id
ResourceType
Location
Priority
Status
CreatedAt
3. Allocations

Primary key:

allocation_id

Important attributes:

allocation_id
request_id
resource_id
resource_type
location
priority
status

Detailed database documentation is available in:

docs/database-design.md
API

The deployed API uses Amazon API Gateway.

Base URL:

https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev
Available Endpoints
Method	Endpoint	Purpose
POST	/allocate	Process resource allocation
GET	/requests	Retrieve requests
POST	/requests	Create a request
OPTIONS	/requests	CORS support
GET	/allocate/resources	Retrieve resources
GET	/allocate/allocations	Retrieve allocations
Allocation Request

Endpoint:

POST /allocate

Example:

{
  "request_id": "REQ009",
  "resource_type": "ICU_BED",
  "location": "Bangalore",
  "priority": 1
}

Successful allocation response:

{
  "message": "Resource allocated successfully",
  "request_id": "REQ009",
  "resource_id": "R001",
  "allocation_id": "ALLOC-REQ009",
  "status": "ALLOCATED"
}

When no suitable resource exists:

{
  "message": "No suitable resource available",
  "request_id": "REQ009"
}
Event-Driven Notifications

After a successful allocation, the Allocation Lambda publishes a ResourceAllocated event to Amazon EventBridge.

Event source:

emergency.resource.allocation

Detail type:

ResourceAllocated

Example:

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

EventBridge rule:

EmergencyResourceAllocatedRule

SNS topic:

EmergencyResourceNotifications

Notification flow:

Lambda
  |
  v
EventBridge
  |
  v
SNS
  |
  v
Confirmed Email Subscription
Monitoring

Amazon CloudWatch is used for backend monitoring.

Lambda log group:

/aws/lambda/emergency-resource-allocation

CloudWatch provides:

Lambda execution logs
Error monitoring
Metrics
Troubleshooting information

CloudWatch alarm:

EmergencyResourceAllocation-Lambda-Errors

The alarm monitors Lambda errors.

IAM Security

The Lambda execution role follows a least-privilege approach.

Required DynamoDB operations include:

dynamodb:Scan
dynamodb:UpdateItem
dynamodb:PutItem

Required EventBridge permission:

events:PutEvents

Broad permissions that were removed during IAM cleanup include:

AmazonEventBridgeFullAccess
AmazonDynamoDBFullAccess
AmazonDynamoDBReadOnlyAccess

AWS credentials are not stored in the GitHub repository.

Testing and Validation

The project was tested at both application and AWS levels.

Automated Tests

The local Pytest suite contains 5 tests.

Latest verified result:

5 passed in 0.02s

The test suite covers scenarios including:

Successful allocation
Resource type mismatch
Location mismatch
Unavailable resource
Priority-based allocation

Run the tests with:

python -m pytest
Live API Validation

The deployed API was also tested.

Requests API

Latest verified response:

Requests retrieved successfully
Count: 18
Resources API

Latest verified resource count:

4 resources

The resources included:

R001
R002
R003
R-AUDIT-001
Allocations API

Latest verified response:

Allocations retrieved successfully
Count: 12
Successful Live Allocation Validation

A dedicated fictional audit resource was used to validate the complete allocation workflow.

Request:

REQ-SUCCESS-AUDIT-001

Resource:

R-AUDIT-001

Resource type:

AUDIT_BED

Location:

AUDIT-LAB

Successful allocation:

ALLOC-REQ-SUCCESS-AUDIT-001

Final status:

ALLOCATED

The resource was subsequently reported as:

Available = false

This confirmed the allocation and resource reservation through the deployed API.

Cost Management

AWS Budgets is configured for project spending monitoring.

Budget name:

Emergency-Resource-Allocation-Budget

The budget provides spending monitoring and threshold alerts.

AWS Budgets provides spending awareness and alerts. It is not a hard spending limit that automatically stops all AWS services.

Actual AWS charges depend on service usage, configuration, region, and applicable pricing or free-tier eligibility.

Detailed cost information is available in:

docs/cost-analysis.md
Local Development
Technologies
Python
Boto3
Pytest
Visual Studio Code
Git
GitHub
AWS

Python version used during validation:

Python 3.14.7
Clone Repository
git clone https://github.com/sricharanreddy5414/emergency-resource-allocation.git
cd emergency-resource-allocation
Install Dependencies
python -m pip install -r requirements.txt
Run Tests
python -m pytest

Expected result:

5 passed
Frontend

The frontend is located in:

frontend/

Files:

frontend/
├── index.html
├── style.css
└── app.js

The frontend provides:

Dashboard
Resource management view
Request management view
Allocation view
Notification view
Search and filtering
Help and FAQ
Authentication interface

The frontend communicates with the deployed API Gateway backend.

Project Structure
emergency-resource-allocation/
│
├── architecture/
│   ├── architecture.mmd
│   └── architecture-diagram.png
│
├── docs/
│   ├── architecture.md
│   ├── cost-analysis.md
│   ├── database-design.md
│   └── testing.md
│
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
│
├── src/
│   ├── __init__.py
│   │
│   ├── allocation/
│   │   ├── __init__.py
│   │   └── handler.py
│   │
│   ├── request/
│   │   └── handler.py
│   │
│   └── resource/
│       └── handler.py
│
├── tests/
│   └── test_allocation.py
│
├── README.md
├── requirements.txt
└── .gitignore
Project Objectives

The main objectives are to:

Build a practical serverless AWS application.
Integrate API Gateway with Lambda.
Store application data in DynamoDB.
Implement rule-based resource allocation.
Process requests using predefined priorities.
Handle concurrent reservations safely.
Use DynamoDB conditional writes.
Implement event-driven processing.
Send automated notifications using SNS.
Monitor application behavior using CloudWatch.
Apply IAM least-privilege principles.
Perform automated testing with Pytest.
Practice Git and GitHub version control.
Understand practical AWS cloud architecture.
AWS Region

The deployed backend resources used for this project are located in:

Europe (Stockholm)
eu-north-1
Current Project Status
Development              COMPLETE
AWS Deployment           COMPLETE
Frontend                 COMPLETE
Documentation            COMPLETE
Automated Testing        5/5 PASSED
Live API Validation      COMPLETE
GitHub Repository        SYNCHRONIZED

The project is currently a working educational prototype.

Future Enhancements

Possible future improvements include:

Amazon CloudFront HTTPS delivery after account verification
Stronger authentication and authorization
Additional REST API endpoints
Allocation history improvements
Advanced CloudWatch dashboards
Dead-letter queue for failed event processing
Infrastructure as Code using AWS CDK, CloudFormation, or Terraform
CI/CD pipeline
More integration and concurrency tests
More efficient DynamoDB access patterns
Production-grade security and compliance controls
Learning Outcomes

This project demonstrates practical experience with:

Serverless computing
Event-driven architecture
REST APIs
Amazon API Gateway
AWS Lambda
Amazon DynamoDB
Conditional writes
Concurrency control
Amazon EventBridge
Amazon SNS
Amazon CloudWatch
AWS IAM
AWS Budgets
Python
Boto3
Pytest
Git
GitHub
Cloud security
Cloud monitoring
Disclaimer

This project uses fictional/test data and is intended only for educational and demonstration purposes.

It does not:

Diagnose medical conditions
Recommend medical treatment
Dispatch real emergency services
Make patient-specific medical decisions
Replace professional emergency-response systems
Author

Sri Charan Reddy

BTech — Computer Science Engineering

Alliance University, Bangalore

Project

Emergency Resource Allocation Platform

A practical AWS cloud project demonstrating serverless architecture, event-driven processing, rule-based resource allocation, cloud monitoring, security, automated testing, and GitHub-based development.