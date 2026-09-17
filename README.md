# Emergency Resource Allocation Platform

A serverless, event-driven AWS platform for coordinating emergency resource requests using predefined priority-based allocation rules.

## 📌 Project Overview

The Emergency Resource Allocation Platform is designed to solve a resource coordination problem where multiple requests may arrive at the same time while available resources are limited.

The platform automatically:

- Registers and stores resources
- Accepts emergency resource requests
- Checks resource availability
- Applies priority-based allocation rules
- Safely reserves resources
- Prevents double allocation during concurrent requests
- Creates allocation records
- Publishes allocation events
- Sends notification emails
- Monitors application activity and failures

This project uses fictional/test data and is intended as a cloud architecture and resource-coordination simulation. It does not provide medical decisions or replace real emergency-response systems.

---

## 🏗️ Architecture

```text
User / Client
     |
     v
Amazon API Gateway
     |
     v
AWS Lambda
     |
     +--------------------+
     |                    |
     v                    v
DynamoDB              DynamoDB
Resources          EmergencyRequests
     |
     v
DynamoDB
Allocations
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