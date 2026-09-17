Emergency Resource Allocation Platform

A serverless, event-driven AWS platform for coordinating emergency resource requests using predefined priority-based allocation rules.

Educational scope: This project uses fictional/test data for demonstration and learning. It is not a real medical system, emergency dispatch system, or medical decision-making system.

🌐 Live Demo

Public website:
http://emergency-resource-allocation-sricharan-2026.s3-website.eu-north-1.amazonaws.com

GitHub repository:
https://github.com/sricharanreddy5414/emergency-resource-allocation

The live dashboard reads resource data from AWS and sends allocation requests to the deployed AWS backend.

HTTPS note: CloudFront deployment is currently pending AWS account verification. The S3 static website is live over HTTP.

📌 Project Overview

The Emergency Resource Allocation Platform is a cloud-based resource coordination simulation designed to handle multiple requests when available resources are limited.

The platform can:

Register and store resources

Accept emergency resource requests

Check resource availability

Apply predefined priority-based allocation rules

Match requests by resource type and location

Safely reserve resources

Prevent double allocation during concurrent requests

Create allocation records

Update request status

Publish allocation events

Send email notifications

Monitor Lambda activity and errors

Run automated unit tests

🏗️ AWS Architecture

                         User / Client
                              |
                              v
                     Amazon S3 Website
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
                    Logs + Metrics + Alarm

Architecture diagram: architecture/architecture-diagram.png

☁️ AWS Services Used

AWS Service

Purpose

Amazon S3

Hosts the public static frontend

Amazon API Gateway

Exposes the REST API

AWS Lambda

Runs the allocation engine

Amazon DynamoDB

Stores resources, requests, and allocations

Amazon EventBridge

Routes ResourceAllocated events

Amazon SNS

Sends notification emails

Amazon CloudWatch

Provides logs, metrics, and alarms

AWS IAM

Controls permissions using least privilege

AWS Budgets

Monitors project spending

🔄 System Workflow

User submits request
        |
        v
S3-hosted Dashboard
        |
        v
API Gateway POST /allocate
        |
        v
AWS Lambda
        |
        v
Check available resources
        |
        v
Apply priority rules
        |
        v
Match type + location
        |
        v
Reserve resource safely
        |
        v
Create allocation record
        |
        v
Update request status
        |
        v
Publish ResourceAllocated event
        |
        v
EventBridge
        |
        v
SNS
        |
        v
Email notification

⚙️ Allocation Logic

The allocation engine uses predefined rules rather than Artificial Intelligence or Machine Learning.

The process is:

Read pending requests.

Sort requests by priority.

Priority 1 is the highest priority.

Match the requested resource type.

Match the requested location.

Check whether the resource is available.

Reserve a suitable resource.

Create an allocation record.

Update the request to ALLOCATED.

Publish a ResourceAllocated event.

Deliver a notification through SNS.

🔐 Concurrency Protection

The project uses a DynamoDB conditional update to protect against double allocation.

For example, if two requests attempt to reserve the same resource:

Request A ---> Check ---> Available
Request B ---> Check ---> Available

        Both attempt reservation
                 |
                 v
       DynamoDB conditional update
                 |
          +------+------+
          |             |
          v             v
      Request A      Request B
       succeeds        fails

The resource must currently satisfy:

Available = true

before it can be changed to:

Available = false

This prevents the same resource from being successfully reserved by two competing requests at the same time.

🗄️ Database Design

The project uses three DynamoDB tables.

Resources

Partition key: resource_id

Example attributes:

resource_id
Type
Location
Available

EmergencyRequests

Partition key: request_id

Example attributes:

request_id
ResourceType
Location
Priority
Status

Allocations

Partition key: allocation_id

Example attributes:

allocation_id
request_id
resource_id
resource_type
status
priority

📡 API

POST /allocate

Deployed endpoint:

https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/allocate

Example request:

{
  "request_id": "REQ009",
  "resource_type": "ICU_BED",
  "location": "Bangalore",
  "priority": 1
}

Successful response:

{
  "request_id": "REQ009",
  "resource_id": "R001",
  "allocation_id": "ALLOC-REQ009",
  "status": "ALLOCATED"
}

When no suitable resource exists:

{
  "message": "No suitable resource available"
}

📢 Event-Driven Notifications

After successful allocation, Lambda publishes a ResourceAllocated event to EventBridge.

Event pattern:

{
  "source": [
    "emergency.resource.allocation"
  ],
  "detail-type": [
    "ResourceAllocated"
  ]
}

EventBridge rule:

EmergencyResourceAllocatedRule

SNS topic:

EmergencyResourceNotifications

Flow:

Lambda
  ↓
EventBridge
  ↓
SNS
  ↓
Confirmed Email Subscription

Verified live event

A successful REQ009 allocation produced:

request_id: REQ009
resource_id: R001
allocation_id: ALLOC-REQ009
resource_type: ICU_BED
location: Bangalore
priority: 1
status: ALLOCATED

The notification was successfully received by email.

📊 Monitoring

Amazon CloudWatch is used for Lambda monitoring.

Log group:

/aws/lambda/emergency-resource-allocation

Alarm:

EmergencyResourceAllocation-Lambda-Errors

The alarm is configured to monitor Lambda errors and its current verified state is:

OK

🛡️ IAM Security

The Lambda execution role follows the principle of least privilege.

Required DynamoDB actions:

dynamodb:Scan
dynamodb:UpdateItem
dynamodb:PutItem

Required EventBridge action:

events:PutEvents

Broad permissions were removed during IAM cleanup, including:

AmazonEventBridgeFullAccess
AmazonDynamoDBFullAccess
AmazonDynamoDBReadOnlyAccess

The project does not store AWS credentials in the GitHub repository.

🧪 Testing

The project was tested at application and AWS levels.

Successful allocation

Verified live:

REQ009 → R001 → ALLOC-REQ009 → ALLOCATED

Resource exhaustion

When suitable resources were unavailable, the website displayed:

No suitable resource available

Priority-based allocation

The allocation engine processes requests using predefined priority values:

Priority 1 → highest
Priority 2 → next
Priority 3 → next

Concurrent request protection

The implementation uses a DynamoDB conditional update so a resource cannot be successfully reserved twice through competing reservations.

EventBridge + SNS

A successful allocation generated a ResourceAllocated event and the subscribed email received the notification.

Automated unit tests

The local test suite covers allocation scenarios including:

Successful allocation

Resource type mismatch

Location mismatch

Unavailable resource

Priority-based allocation

Test result:

5 passed

💰 Cost Management

AWS Budgets is configured for project spending monitoring.

Configured budget:

Emergency-Resource-Allocation-Budget

Configured monthly budget amount:

$500.00

The budget currently reports:

Thresholds: OK
Health: Healthy

AWS Budgets provides spending alerts and monitoring; it is not a hard spending limit that automatically stops all AWS services.

▶️ Local Development

Technologies

Python
Boto3
Pytest
Visual Studio Code
Git
GitHub
AWS

Python version used:

Python 3.14.7

Clone

git clone https://github.com/sricharanreddy5414/emergency-resource-allocation.git
cd emergency-resource-allocation

Install dependencies

py -m pip install -r requirements.txt

Run allocation engine

py .\src\allocation\handler.py

Run tests

py -m pytest

Expected:

5 passed

📁 Project Structure

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
│   ├── allocation/
│   │   ├── __init__.py
│   │   └── handler.py
│   ├── request/
│   │   └── handler.py
│   ├── resource/
│   │   └── handler.py
│   └── __init__.py
│
├── tests/
│   └── test_allocation.py
│
├── README.md
├── requirements.txt
└── .gitignore

🎯 Project Objectives

Build a practical serverless AWS application.

Integrate API Gateway with Lambda.

Store application data in DynamoDB.

Implement rule-based resource allocation.

Handle concurrent reservations safely.

Use DynamoDB conditional writes.

Implement event-driven processing.

Send automated notifications using SNS.

Monitor application behavior using CloudWatch.

Apply IAM least-privilege principles.

Perform automated testing with Pytest.

Practice Git and GitHub version control.

Understand practical AWS cloud architecture.

🚀 Future Enhancements

Amazon CloudFront HTTPS delivery after account verification

Authentication and authorization

Additional REST API endpoints

Allocation history dashboard

Advanced CloudWatch metrics

Dead-letter queue for failed event processing

Infrastructure as Code using AWS CDK or CloudFormation

CI/CD pipeline

More integration and concurrency tests

📚 Learning Outcomes

This project demonstrates practical understanding of:

Serverless computing

Event-driven architecture

REST APIs

API Gateway

AWS Lambda

DynamoDB

Conditional writes

Concurrency control

EventBridge

SNS

CloudWatch

IAM least privilege

AWS Budgets

Python

Boto3

Pytest

Git

GitHub

Cloud security

Cloud monitoring

⭐ Key Highlights

Fully serverless AWS backend

Public S3-hosted dashboard

Live frontend-to-AWS integration

Priority-based allocation

DynamoDB conditional reservation

Concurrent request protection

Event-driven notifications

Confirmed SNS email delivery

CloudWatch logs and error alarm

IAM least-privilege cleanup

Automated unit testing

AWS cost monitoring

GitHub version control

Documented cloud architecture

👨‍💻 Project

Emergency Resource Allocation Platform

Built as an educational AWS cloud project to demonstrate practical serverless, event-driven architecture and cloud engineering concepts.