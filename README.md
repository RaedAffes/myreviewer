# MyReviewer

**AI code review for GitHub pull requests.**

MyReviewer reads a pull request against the **whole repository** and writes an
evidence-based, line-level review: what the change does, which concerns it
mixes together, bugs and risks, requirement compatibility, a merge-readiness
verdict, and a concrete split plan. Results are saved to your dashboard and can
optionally be posted back to the PR as a comment.

Hosted app: **https://myreviewer.tech**

---

## Features

- **Whole-repo context** — every review considers how the change fits the rest
  of the codebase, not just the diff.
- **Mixed-concern & size flags** — spots PRs that bundle unrelated topics or are
  too large to review coherently.
- **AI change log** — an "old → new" explanation of the meaningful edits.
- **Deep bug & requirement review** — severity-ranked findings, a verification
  pass, and optional specialist passes (correctness, security, tests).
- **Actionable verdicts** — `APPROVE`, `APPROVE WITH MINOR SUGGESTIONS`,
  `REQUEST CHANGES`, or `BLOCK`, plus an advisory merge-readiness rating.
- **One review per PR** — analyses are stored, listed, and re-openable; they can
  be exported.
- **Optional PR comment** — post the review back to GitHub (updates the same
  comment on re-runs).
- **Webhook mode** — a GitHub App can run reviews automatically on
  `pull_request` events.

## How it works

MyReviewer is three services behind one domain:

| Service  | What it is                                   | Port |
| -------- | -------------------------------------------- | ---- |
| `front`  | Angular app served by nginx (also proxies API) | 80   |
| `api`    | FastAPI REST API + analysis pipeline          | 8000 |
| `webhook`| FastAPI GitHub App webhook receiver           | 8001 |

The **analysis pipeline** runs seven stages:

1. Summarize the PR's intent
2. Cluster the changes into concerns (and flag mixed concerns)
3. Diagnose reviewability (size, scope, lockfile churn)
4. Build the change log (old → new)
5. Post-review the resulting code
6. Deep bug + requirement-compatibility review
7. Produce a split plan and verdict

You choose a **review effort**: `standard` (fastest), `deep` (default), or
`max` (deep + specialist passes). Analysis runs as a background job — the API
returns immediately and the dashboard polls for completion, so long reviews are
not cut off by proxy timeouts.

LLM calls go to **NVIDIA NIM** (OpenAI-compatible, free tier) by default, and
can be pointed at any compatible endpoint. The default model is
`nvidia/nemotron-3-super-120b-a12b`.

---

## Using the hosted app

1. Open **https://myreviewer.tech**.
2. Click **Connect GitHub** and authorize the app (OAuth).
3. The dashboard lists your open pull requests.
4. Pick a **Review effort**, then click **Review** next to a PR.
5. Analysis runs in the background; the report opens when it finishes and the
   review is posted to the PR as a comment.
6. Past reviews stay in **Reports** — reopen or export them any time.

---

## Run it yourself

### Prerequisites

- A **GitHub account** (for OAuth + PR access).
- A **NVIDIA NIM API key** — free at https://build.nvidia.com → *API keys*
  (`nvapi-...`).
- Either **Docker** (simplest) or **Node ≥ 20.19** + **Python ≥ 3.11** for local
  development.

### 1. Configure

```bash
cp back/.env.example back/.env
```

Edit `back/.env` and set at minimum:

```env
NIM_API_KEY=nvapi-xxxxxxxx

# GitHub OAuth App (github.com/settings/developers/apps)
#   Homepage URL:  http://localhost:8000
#   Callback URL:  http://localhost:8000/api/auth/callback
GITHUB_DEV_CLIENT_ID=...
GITHUB_DEV_CLIENT_SECRET=...
```

`back/.env` is gitignored — never commit it.

### 2. Run with Docker Compose

```bash
docker compose up --build
```

Open **http://localhost**. This builds the same `front` / `api` / `webhook`
images used in production.

### 3. Local development (no Docker)

Backend (API on `:8000`):

```bash
cd back
python -m venv .venv && . .venv/Scripts/activate   # Windows
# python -m venv .venv && source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
uvicorn api:app --reload --port 8000
```

Frontend (Angular dev server on `:4200`, proxies `/api` → `localhost:8000`):

```bash
cd front
npm ci
npm start
```

Open **http://localhost:4200**.

> GitHub allows only one callback URL per OAuth App, so localhost uses a
> separate app configured under `GITHUB_DEV_*`. The backend picks the right
> client automatically from the request host — no code changes needed.

---

## Configuration reference

All variables live in `back/.env` (see `back/.env.example`).

| Variable | Required | Purpose |
| -------- | -------- | ------- |
| `NIM_API_KEY` | Yes | LLM key (`nvapi-...`). Analysis fails fast with a clear message if unset. |
| `NIM_BASE_URL` | No | OpenAI-compatible endpoint (default NVIDIA NIM; must end in `/v1`). |
| `NIM_MODEL` | No | Default model id. |
| `NIM_MODELS` | No | Comma-separated extra models for the picker. |
| `LLM_TEMPERATURE`, `LLM_MAX_TOKENS` | No | Sampling controls. |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | For deployed OAuth | GitHub OAuth App (production callback). |
| `GITHUB_DEV_CLIENT_ID` / `GITHUB_DEV_CLIENT_SECRET` | For local OAuth | Separate OAuth App for `localhost`. |
| `GITHUB_TOKEN` | For webhook | PAT (`repo` scope) used by the webhook to fetch diffs and post comments. |
| `GITHUB_APP_ENABLED` | No | `1` enables the `/webhook` endpoint; `0` makes it inert (logs only). |
| `GITHUB_WEBHOOK_SECRET` | For webhook | Secret used to verify webhook payloads. |
| `GITHUB_BOT_USERNAME` | No | Bot login for posting/updating the review comment. |
| `GITHUB_PRIVATE_KEY_PATH` | No | GitHub App private key path. |
| `APP_URL` | No | Public URL (OAuth redirects; defaults to `https://myreviewer.tech`). |
| `DATA_DIR` | No | Writable dir for reports/users/sessions (default `/tmp/myreviewer-data`, Docker uses `/data`). |

---

## Project structure

```
back/                 FastAPI API + analysis pipeline (Python)
  api.py              REST API + background analysis jobs
  github_client.py    Diff fetching, PR comments, GitHub access
  github_oauth.py     OAuth flow
  github_app/         GitHub App webhook server
  myreviewer/         Core pipeline (7 stages) + report store
front/                Angular app (dashboard, reports, landing)
k8s/                  Kubernetes manifests (k3s)
scripts/              VM and self-hosted runner setup
Dockerfile            Multi-target build: front / api / webhook
docker-compose.yml    Local all-in-one run
nginx.conf            Serves the front and proxies /api and /webhook
```

API surface (all under `/api`): `health`, `models`, `analyze` (+ status),
`auth/*`, `prs`, `reports` (+ export/states), `users`.

---

## Deployment

Pushing to `main` triggers `.github/workflows/deploy.yml`:

1. **build** — builds and pushes `front`, `api`, `webhook` images to Docker Hub
   (tagged `-latest` and `-<sha>`).
2. **deploy** — on a self-hosted runner, applies `k8s/` and rolls out the
   `-<sha>` image for each service.

Runtime topology: k3s on an Azure VM, exposed via a Cloudflare Tunnel. See
`scripts/vm-setup.sh` and `scripts/runner-setup.sh` for host setup.

---

## Tech stack

- **Frontend:** Angular 20 (standalone, SSR/prerender), served by nginx.
- **Backend:** FastAPI + Uvicorn (Python 3.12), running the review pipeline.
- **LLM:** NVIDIA NIM (OpenAI-compatible), model-configurable.
- **Infra:** Docker, k3s, Cloudflare Tunnel, GitHub Actions.

## License

MIT — see [`LICENSE`](LICENSE).

## Contact

Built at ENSI, Tunisia — Raed Affes (raed.affes@ensi-uma.tn) and
Azar Benchkir (azar.benchkir@ensi-uma.tn). Collaborations welcome at
https://github.com/RaedAffes/myreviewer.
