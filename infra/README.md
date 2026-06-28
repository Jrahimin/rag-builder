# Infrastructure

Deployment and infrastructure assets for self-hosting the AI Platform Engine.

The local development stack is defined by the root
[`docker-compose.yml`](../docker-compose.yml) and the backend image at
[`backend/Dockerfile`](../backend/Dockerfile):

| Service   | Image               | Purpose                          | Local port(s) |
| --------- | ------------------- | -------------------------------- | ------------- |
| backend   | `ape-backend:dev`   | FastAPI application              | 8000          |
| postgres  | `postgres:16`       | Relational database              | 5432          |
| redis     | `redis:7`           | Cache + background job queue     | 6379          |
| qdrant    | `qdrant/qdrant`     | Vector database                  | 6333 / 6334   |
| minio     | `minio/minio`       | S3-compatible object storage     | 9000 / 9001   |

This directory will hold additional infrastructure-as-code over time
(production compose overrides, Kubernetes manifests, Terraform, service
configuration files, etc.).
