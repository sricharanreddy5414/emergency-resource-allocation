# Cost Analysis

## Overview

The Emergency Resource Allocation Platform uses primarily serverless and managed AWS services.

The architecture was designed to avoid maintaining continuously running application servers and to keep the project suitable for learning, development, and demonstration purposes.

Actual AWS charges depend on usage, AWS region, service configuration, and applicable AWS pricing or free-tier eligibility.

---

## AWS Services and Cost Considerations

| AWS Service        | Usage in Project          | Cost Consideration                                                 |
| ------------------ | ------------------------- | ------------------------------------------------------------------ |
| Amazon API Gateway | Receives API requests     | Charges can depend on API request volume                           |
| AWS Lambda         | Runs allocation logic     | Charges depend on requests and compute duration                    |
| Amazon DynamoDB    | Stores application data   | Charges depend on read/write usage and table configuration         |
| Amazon EventBridge | Routes allocation events  | Charges depend on event volume                                     |
| Amazon SNS         | Sends notification emails | Charges depend on notification usage                               |
| Amazon CloudWatch  | Logs, metrics, and alarms | Charges can depend on log ingestion, storage, and monitoring usage |
| AWS IAM            | Controls permissions      | IAM itself generally has no additional charge                      |
| AWS Budgets        | Monitors spending         | Used for budget monitoring and alerts                              |

---

## Serverless Cost Model

The project uses a serverless architecture:

```text
API Gateway
     |
     v
Lambda
     |
     v
DynamoDB
```

Instead of maintaining a continuously running server, the application uses managed services that execute when required.

This is useful for a project where request volume may be variable.

---

## Cost Monitoring

AWS Budgets was configured for the project.

### Budget Name

```text
Emergency-Resource-Allocation-Budget
```

### Budget Amount

A development budget of approximately:

```text
₹500 per month
```

was configured as a spending-monitoring threshold.

### Alert Thresholds

Alerts were configured at:

```text
80%
100%
```

These alerts are intended to notify the account owner when spending approaches the configured budget.

> AWS Budgets provides spending monitoring and alerts. It does not automatically stop all AWS resources when the budget amount is reached.

---

## Cost Control Measures

The following practices can help control costs during development:

### 1. Monitor Lambda Usage

CloudWatch can be used to monitor Lambda invocations, errors, and execution behavior.

### 2. Monitor DynamoDB Usage

DynamoDB tables should be monitored and configured appropriately for the expected development workload.

### 3. Control API Requests

Unnecessary API requests should be avoided during testing.

### 4. Monitor CloudWatch Logs

Logs should be reviewed and unnecessary long-term log retention should be avoided when appropriate.

### 5. Remove Unused Resources

AWS resources that are no longer required for testing should be removed or disabled where applicable.

### 6. Use AWS Budgets

Budget alerts provide an additional layer of spending awareness.

---

## Development vs Production

This project is currently a learning and demonstration implementation.

A production deployment would require a separate cost analysis based on:

* Expected API request volume
* Lambda execution frequency
* Lambda memory allocation
* Lambda execution duration
* DynamoDB read/write workload
* Data storage requirements
* EventBridge event volume
* SNS notification volume
* CloudWatch log volume
* Data transfer
* Security and operational requirements

Therefore, a fixed production cost should not be assumed from this development project.

---

## Cost Optimization Opportunities

Future versions could improve cost efficiency through:

* Optimized Lambda execution time
* Efficient DynamoDB access patterns
* Avoiding unnecessary table scans
* Appropriate log retention
* Monitoring unused resources
* Efficient API request handling
* Appropriate capacity configuration
* Infrastructure automation for consistent environments

One important future optimization is replacing broad DynamoDB `Scan` operations with more targeted access patterns as the data model grows.

---

## Cost Monitoring Workflow

```text
AWS Services
     |
     v
Usage Generated
     |
     v
AWS Billing
     |
     v
AWS Budgets
     |
     v
Budget Threshold
     |
     v
Alert / Notification
```

---

## Cost Analysis Summary

The project demonstrates how serverless AWS services can be combined while maintaining awareness of cloud spending.

The main cost-management practices implemented are:

* AWS Budget configuration
* Budget threshold alerts
* Serverless architecture
* CloudWatch monitoring
* Removal of unnecessary permissions
* Awareness of unused AWS resources

Actual costs should always be verified using the AWS Billing and Cost Management console for the specific AWS account and region.

---

## Project Scope

This cost analysis is intended for the project's development and demonstration environment.

It does not represent a guaranteed production price estimate.
