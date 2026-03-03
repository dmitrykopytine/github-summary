# GitHub Summary

A FastAPI service that summarizes GitHub repositories.

## Requirements

- Python 3.10 or 3.12.12

## Setup

Create and activate a virtual environment (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Start the server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or run the module directly:

```bash
python main.py
```

The API will be available at `http://localhost:8000`.

## API

### POST /summarize

Summarize a public GitHub repository.

**Request body** (JSON or form):
| Field      | Type   | Required | Description                         |
|------------|--------|----------|-------------------------------------|
| github_url | string | yes      | URL of a public GitHub repository   |

**Example:**
```bash
curl -X POST http://localhost:8000/summarize \
  -H "Content-Type: application/json" \
  -d '{"github_url": "https://github.com/user/repo"}'
```

**Response:**
```json
{
  "summary": "abc",
  "technologies": ["Python", "urllib3", "certifi"],
  "structure": "xxx"
}
```
