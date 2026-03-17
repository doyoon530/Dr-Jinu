# Public Deployment Guide

## Overview

This project can be deployed publicly with a Docker-based workflow.  
The repository now includes shared deployment assets for the following platforms:

- Render
- Railway
- Azure App Service (custom container)
- AWS App Runner / ECR

## Important Constraints

This application is heavier than a typical Flask web app because it depends on:

- a local GGUF LLM model file
- Google Cloud Speech-to-Text credentials
- native Python packages such as `llama-cpp-python`

Before deploying, prepare these two runtime inputs:

1. `MODEL_PATH`
   - Absolute path to the GGUF model inside the container or mounted disk
2. Google Cloud credentials
   - either `GOOGLE_APPLICATION_CREDENTIALS`
   - or `GOOGLE_APPLICATION_CREDENTIALS_JSON`

## Shared Environment Variables

Set these on any deployment platform:

```text
HOST=0.0.0.0
PORT=8000
WAITRESS_THREADS=8
MODEL_PATH=/data/models/EXAONE-3.5-7.8B-Instruct-Q8_0.gguf
GOOGLE_APPLICATION_CREDENTIALS_JSON={...}
```

Optional:

```text
MODEL_DOWNLOAD_URL=https://...
```

If `MODEL_DOWNLOAD_URL` is set and `MODEL_PATH` does not exist, the container startup script downloads the model file automatically.

## Health Check

Use:

```text
/health
```

The endpoint returns:

- `status`
- `ready`
- model file status
- Google credential status

## Files Added For Deployment

- `Dockerfile`
- `.dockerignore`
- `start_container.sh`
- `render.yaml`
- `railway.toml`
- `apprunner.yaml`

---

## 1. Render

### Recommended 방식

- Docker Web Service
- Persistent Disk attached at `/data`

### Included config

- `render.yaml`

### Required environment variables

- `GOOGLE_APPLICATION_CREDENTIALS_JSON`
- `MODEL_DOWNLOAD_URL` or manually uploaded model on the disk

### Notes

- The included `render.yaml` mounts a disk at `/data`
- The default `MODEL_PATH` points to `/data/models/...`
- Render free-style lightweight plans may not be sufficient for a large GGUF model

### Deploy flow

1. Push this repository to GitHub
2. In Render, create a new Blueprint or Web Service from the repo
3. Confirm the detected `render.yaml`
4. Add secret env vars
5. Deploy
6. Verify `/health`

---

## 2. Railway

### Recommended 방식

- Docker deployment using `railway.toml`

### Included config

- `railway.toml`
- `Dockerfile`

### Required environment variables

- `GOOGLE_APPLICATION_CREDENTIALS_JSON`
- `MODEL_PATH`
- `MODEL_DOWNLOAD_URL` if you want startup download

### Notes

- Railway ephemeral storage may not be ideal for large model files
- For stable deployment, use a mounted volume or switch to a remote model-serving approach

### Deploy flow

1. Create a new Railway project from GitHub
2. Railway detects the Dockerfile
3. Add variables in the service settings
4. Redeploy
5. Check `/health`

---

## 3. Azure App Service

### Recommended 방식

- App Service for Containers
- Container image built from this repo

### Included config

- `Dockerfile`

### Azure CLI example

```bash
az group create --name ncai-rg --location koreacentral
az acr create --resource-group ncai-rg --name <acr-name> --sku Basic
az acr build --registry <acr-name> --image ncai-monitor:latest .
az appservice plan create --name ncai-plan --resource-group ncai-rg --is-linux --sku B1
az webapp create --resource-group ncai-rg --plan ncai-plan --name <webapp-name> --deployment-container-image-name <acr-name>.azurecr.io/ncai-monitor:latest
az webapp config appsettings set --resource-group ncai-rg --name <webapp-name> --settings WEBSITES_PORT=8000 HOST=0.0.0.0 WAITRESS_THREADS=8 MODEL_PATH=/home/site/models/EXAONE-3.5-7.8B-Instruct-Q8_0.gguf
az webapp config appsettings set --resource-group ncai-rg --name <webapp-name> --settings GOOGLE_APPLICATION_CREDENTIALS_JSON='<json-string>'
```

### Notes

- Upload the model to mounted storage or bake it into a private image
- App Service custom containers work best when the model is provided through mounted storage or startup download

---

## 4. AWS

### Recommended 방식

- Amazon ECR + AWS App Runner

### Included config

- `Dockerfile`
- `apprunner.yaml` for source-based App Runner deployments

### Container-based deployment flow

1. Build the Docker image
2. Push the image to Amazon ECR
3. Create an App Runner service from the ECR image
4. Set environment variables and health check path `/health`

### AWS CLI example

```bash
aws ecr create-repository --repository-name ncai-monitor
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.ap-northeast-2.amazonaws.com
docker build -t ncai-monitor .
docker tag ncai-monitor:latest <account-id>.dkr.ecr.ap-northeast-2.amazonaws.com/ncai-monitor:latest
docker push <account-id>.dkr.ecr.ap-northeast-2.amazonaws.com/ncai-monitor:latest
```

Then create App Runner with:

- image port: `8000`
- health check path: `/health`
- env vars: `HOST`, `PORT`, `WAITRESS_THREADS`, `MODEL_PATH`, `GOOGLE_APPLICATION_CREDENTIALS_JSON`

### Notes

- `apprunner.yaml` is included for source-based deployments, but Docker + ECR is usually more predictable for native dependencies
- For large model files, attach external storage or download on startup to a writable path

---

## Practical Recommendation

For this repository in its current architecture:

1. Render or Railway are convenient for quick demos, but may struggle with the large local model
2. Azure App Service custom container is workable if you control storage and instance size
3. AWS App Runner or a VM/container host is the most realistic option for stable public access

If you want the most reliable public deployment, the next architectural step would be:

- move the LLM inference to a separate model server or external API
- keep this Flask app as the web/API layer

That change would make Render, Railway, Azure, and AWS deployment much easier and cheaper.
