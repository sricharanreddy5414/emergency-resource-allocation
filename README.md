Emergency Resource Allocation Platform

A serverless, event-driven AWS platform for coordinating emergency
resource requests using predefined priority-based allocation rules.

📌 Project Overview

The Emergency Resource Allocation Platform is a cloud-based resource
coordination simulation designed to handle multiple resource requests
when available resources are limited.

The platform automatically:

Registers resources

Stores resource information

Accepts emergency resource requests

Checks resource availability

Applies predefined priority-based allocation rules

Matches requests with suitable resources

Safely reserves resources

Prevents double allocation during concurrent requests

Creates allocation records

Updates request status

Publishes allocation events

Sends notification emails

Monitors application activity and failures

Provides automated testing for the allocation logic

This project uses fictional/test data for educational and demonstration
purposes. It is not a real medical system, emergency dispatch system, or
medical decision-making system.

🏗️ Architecture

                         User / Client
                              |
                              v
                     Amazon API Gateway
                              |
                              v
                         AWS Lambda
                    Allocation Engine
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
        DynamoDB         DynamoDB         DynamoDB
        Resources    EmergencyRequests    Allocations
                              |
                              v
                     Amazon EventBridge
                              |
                              v
                         Amazon SNS
                              |
                              v
                    Email Notification


                         AWS Lambda
                              |
                              v
                       Amazon CloudWatch
                    Logs + Metrics + Alarms

☁️ AWS Services Used

AWS Service          Purpose

Amazon API Gateway   Receives client API requests
AWS Lambda           Runs the allocation engine and business logic
Amazon DynamoDB      Stores resources, requests, and allocation records
Amazon EventBridge   Routes resource allocation events
Amazon SNS           Sends notification emails
Amazon CloudWatch    Provides logs, metrics, and alarms
AWS IAM              Controls permissions using least-privilege access
AWS Budgets          Monitors estimated AWS spending

🔄 System Workflow

The platform follows this workflow:

Emergency Resource Request
          |
          v
   Amazon API Gateway
          |
          v
      AWS Lambda
          |
          v
Check Available Resources
          |
          v
   Apply Priority Rules
          |
          v
    Match Resource
          |
          v
    Reserve Resource
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
   Amazon EventBridge
          |
          v
       Amazon SNS
          |
          v
   Email Notification

⚙️ Allocation Logic

The allocation engine uses predefined rules rather than Artificial
Intelligence or Machine Learning.

The allocation process is:

Read pending emergency requests.

Sort requests according to priority.

Priority 1 represents the highest priority.

Check the requested resource type.

Check the requested location.

Check whether the resource is available.

Select a suitable resource.

Reserve the resource.

Create an allocation record.

Update the request status.

Publish a ResourceAllocated event.

Send a notification through SNS.

🔐 Concurrency Protection

A major technical feature of this project is protection against double
allocation.

Consider a situation where only one resource is available and two
requests arrive almost simultaneously.

Without concurrency protection:

Request A ---> Check Resource ---> Available
Request B ---> Check Resource ---> Available

Both requests may attempt to reserve the same resource.

The AWS implementation uses a DynamoDB conditional update.

The resource must currently have:

Available = true

before it can be changed to:

Available = false

The conditional update ensures that only one concurrent request can
successfully reserve the resource.

Example result:

Request A → Resource allocated successfully
Request B → No suitable resource available

This prevents the same resource from being allocated to two requests.

🗄️ Database Design

The project uses three Amazon DynamoDB tables.

1. Resources

Stores information about available resources.

Primary Key:

resource_id

Example attributes:

resource_id
type
location
available

Example resource:

resource_id = R001
type        = ICU_BED
location    = Bangalore
available   = true

2. EmergencyRequests

Stores incoming emergency resource requests.

Primary Key:

request_id

Example attributes:

request_id
resource_type
location
priority
status

Example:

request_id     = REQ001
resource_type  = ICU_BED
location       = Bangalore
priority       = 1
status         = PENDING

3. Allocations

Stores successful resource allocation records.

Primary Key:

allocation_id

Example attributes:

allocation_id
request_id
resource_id
priority
status

Example:

allocation_id = ALLOC-REQ001
request_id    = REQ001
resource_id   = R001
priority      = 1
status        = ALLOCATED

📡 API Gateway

The project uses Amazon API Gateway to expose the allocation
functionality through a REST API.

POST /allocate

This endpoint triggers the resource allocation process.

POST /allocate

The request is forwarded to the AWS Lambda allocation engine.

Successful API Response

{
  "message": "Resource allocated successfully",
  "request_id": "REQ005",
  "resource_id": "R001",
  "allocation_id": "ALLOC-REQ005",
  "status": "ALLOCATED"
}

Resource Exhaustion Response

When no suitable resource is available:

{
  "message": "No suitable resource available"
}

📢 Event-Driven Notifications

After successful allocation, AWS Lambda publishes a ResourceAllocated
event to Amazon EventBridge.

The EventBridge rule uses:

{
  "source": [
    "emergency.resource.allocation"
  ],
  "detail-type": [
    "ResourceAllocated"
  ]
}

The event is routed to the SNS topic:

EmergencyResourceNotifications

SNS then sends an email notification to the subscribed email address.

The complete event flow is:

AWS Lambda
     |
     v
EventBridge
     |
     v
SNS Topic
     |
     v
Email Notification

The EventBridge-to-SNS notification flow was successfully tested.

📊 CloudWatch Monitoring

Amazon CloudWatch is used to monitor the Lambda application.

The project includes:

Lambda execution logs

Lambda error monitoring

Allocation activity

Event processing information

API-related activity

CloudWatch alarms

Lambda log group:

/aws/lambda/emergency-resource-allocation

CloudWatch alarm:

EmergencyResourceAllocation-Lambda-Errors

The alarm monitors Lambda errors and can notify the configured SNS
topic.

🛡️ IAM Security

The Lambda execution role follows the principle of least privilege.

The project grants only the required permissions for:

DynamoDB

dynamodb:Scan
dynamodb:UpdateItem
dynamodb:PutItem

for the required project tables.

EventBridge

events:PutEvents

for the default event bus.

The following broad permissions were removed during IAM cleanup:

AmazonEventBridgeFullAccess
AmazonDynamoDBFullAccess
AmazonDynamoDBReadOnlyAccess

This demonstrates an improvement from broad permissions toward more
restricted access.

🧪 Testing

The project was tested at both the application and AWS levels.

Test 1 --- Successful Allocation

A request with a matching available resource was successfully allocated.

Example:

Request REQ003
Resource R001
Status ALLOCATED

Test 2 --- Resource Exhaustion

When all suitable resources were unavailable, the API returned:

{
  "message": "No suitable resource available"
}

Test 3 --- Priority-Based Allocation

The local allocation engine processes requests according to priority.

Example:

Priority 1 → processed first
Priority 2 → processed next
Priority 3 → processed after that

Test 4 --- Concurrent Requests

Two API requests were sent almost simultaneously for the same available
resource.

Observed result:

Request 1 → Resource allocated successfully
Request 2 → No suitable resource available

This demonstrated that the same resource was not allocated twice.

Test 5 --- EventBridge and SNS

After a successful allocation:

Lambda
  ↓
EventBridge
  ↓
SNS
  ↓
Email

The notification email was successfully received.

Test 6 --- Automated Unit Tests

The local allocation logic contains tests for:

Successful allocation

Resource type mismatch

Location mismatch

Unavailable resource

Priority-based allocation

Pytest result:

5 passed

🧑‍💻 Local Development

The project is developed using:

Python
Boto3
Pytest
Visual Studio Code
Git
GitHub

Python version used during development:

Python 3.14.7

▶️ How to Run Locally

1. Clone the Repository

git clone https://github.com/sricharanreddy5414/emergency-resource-allocation.git
cd emergency-resource-allocation

2. Install Dependencies

py -m pip install -r requirements.txt

3. Run the Local Allocation Engine

py .\src\allocation\handler.py

Expected output begins with:

===== ALLOCATION RESULTS =====

4. Run Automated Tests

py -m pytest

Expected result:

5 passed

📁 Project Structure

emergency-resource-allocation/
│
├── architecture/
│   ├── architecture.mmd
│   └── architecture-diagram.png
│
├── src/
│   ├── allocation/
│   │   ├── __init__.py
│   │   └── handler.py
│   │
│   ├── request/
│   │   └── handler.py
│   │
│   ├── resource/
│   │   └── handler.py
│   │
│   └── __init__.py
│
├── tests/
│   └── test_allocation.py
│
├── docs/
│   ├── architecture.md
│   ├── database-design.md
│   ├── testing.md
│   └── cost-analysis.md
│
├── README.md
├── requirements.txt
└── .gitignore

💻 Source Code Components

Resource Handler

The resource handler represents the resource registration component.

src/resource/handler.py

It provides the basic resource-side functionality used in the project.

Request Handler

The request handler represents the emergency request component.

src/request/handler.py

It accepts and represents incoming resource requests.

Allocation Handler

The main allocation logic is implemented in:

src/allocation/handler.py

It performs:

Priority sorting

Resource type matching

Location matching

Availability checking

Resource reservation

Allocation record creation

🧰 Technologies and Concepts

AWS

Amazon API Gateway

AWS Lambda

Amazon DynamoDB

Amazon EventBridge

Amazon SNS

Amazon CloudWatch

AWS IAM

AWS Budgets

Programming

Python

Boto3

Testing

Pytest

API testing

Concurrency testing

Failure testing

Development Tools

Visual Studio Code

Git

GitHub

Cloud Concepts

Serverless Architecture

Event-Driven Architecture

REST API

NoSQL Database

Conditional Writes

Concurrency Control

Priority-Based Allocation

Monitoring

IAM Least Privilege

Cost Monitoring

💰 Cost Management

AWS Budgets is configured to monitor project spending.

A monthly budget was created for the project with alerts for spending
thresholds.

The budget is intended to provide spending notifications and monitoring.

AWS Budgets provides alerts and monitoring. It does not automatically
act as a hard spending limit.

The serverless architecture also helps avoid maintaining continuously
running servers for this project.

🎯 Project Objectives

The main objectives of this project are:

Build a practical serverless AWS application.

Understand API Gateway and Lambda integration.

Store application data using DynamoDB.

Implement rule-based resource allocation.

Handle concurrent requests safely.

Use conditional database updates.

Implement event-driven processing.

Send automated notifications using SNS.

Monitor application behavior using CloudWatch.

Apply IAM least-privilege principles.

Perform automated testing using Pytest.

Practice Git and GitHub version control.

Understand practical AWS cloud architecture.

📚 Learning Outcomes

This project demonstrates understanding of:

Serverless computing

AWS Lambda

REST APIs

API Gateway

DynamoDB

DynamoDB conditional updates

EventBridge

SNS

CloudWatch

IAM

AWS Budgets

Python

Boto3

Pytest

Git

GitHub

Event-driven architecture

Concurrency handling

Cloud security

Cloud monitoring

⭐ Key Project Highlights

Serverless AWS architecture

Event-driven resource coordination

Priority-based allocation

DynamoDB-based resource management

Conditional resource reservation

Concurrent request protection

Automated allocation records

EventBridge event routing

SNS email notifications

CloudWatch monitoring

Lambda error alarm

IAM least-privilege permissions

Automated unit testing

AWS cost monitoring

GitHub version control

🚀 Future Enhancements

Possible future improvements include:

Authentication and authorization for API users

Additional API endpoints for resource registration

API endpoints for creating and viewing requests

Resource status history

Allocation history dashboard

More detailed CloudWatch metrics

Dead-letter handling for failed event processing

Infrastructure as Code using AWS CloudFormation or Terraform

CI/CD pipeline for automated deployment

Additional automated integration tests

These are future enhancements and are not required for the current
implementation.

⚠️ Disclaimer

This project uses fictional/test data for educational and demonstration
purposes.

It is a resource-coordination simulation and is not intended to:

Make medical decisions

Provide medical advice

Replace emergency services

Replace hospital management systems

Make real-world emergency resource decisions

The project demonstrates AWS cloud architecture and software engineering
concepts only.

👨‍💻 Author

Sri Charan Reddy

BTech Student
Alliance University, Bangalore

📌 Repository

GitHub Repository:

https://github.com/sricharanreddy5414/emergency-resource-allocation

📄 Project Status

Status: Working Prototype

The project currently demonstrates:

API Gateway
     ↓
Lambda
     ↓
DynamoDB
     ↓
EventBridge
     ↓
SNS
     ↓
Email

Lambda
     ↓
CloudWatch

The allocation logic has been tested locally with Pytest, and the AWS
implementation has been tested with successful allocation, resource
exhaustion, concurrent requests, EventBridge event processing, SNS email
notification, and CloudWatch monitoring.