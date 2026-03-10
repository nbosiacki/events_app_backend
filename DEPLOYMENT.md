# Backend Deployment Guide — events.nicholasbosiacki.com

This guide covers deploying the FastAPI backend and MongoDB database to a Google Cloud
Compute Engine VM. Complete each section in order.

The backend runs as two Docker containers (backend + mongodb) managed by Docker Compose.
nginx on the VM acts as the public-facing reverse proxy: it handles SSL and forwards
`/api/*` traffic to the backend container. The frontend is deployed separately (see
`frontend/DEPLOYMENT.md` in the frontend repo).

---

## Architecture overview

```
Internet
    │
    ▼
nginx (port 443, SSL)          ← installed directly on the VM
    │
    ├── /api/* ──────────────→ FastAPI container (127.0.0.1:8000, not public)
    │
    └── /*  ─────────────────→ static files on disk (/opt/events_frontend/dist/)
                                 (deployed separately via rsync)

FastAPI container
    └── mongodb:27017          ← MongoDB container (internal Docker network only)
```

The backend container binds to `127.0.0.1:8000`, not `0.0.0.0:8000`. This means it is
**not reachable from the internet directly** — only nginx on the same VM can connect to
it. This is intentional: the VM's firewall only allows ports 80 and 443.

---

## Pre-flight checklist

Before starting, confirm you have:

- [ ] A Google Cloud account and a project created
- [ ] The `gcloud` CLI installed and authenticated locally (see SSH section in Step 6 below)
- [ ] Access to your domain registrar for nicholasbosiacki.com
- [ ] The Gmail account you want to use for password reset emails
- [ ] 2FA enabled on that Gmail account (required for App Passwords — see Step 1)
- [ ] The `events_app_backend` repo pushed to GitHub (see Step 2)

---

## Step 1 — Create a Gmail App Password

The app sends password reset emails via Gmail SMTP. Gmail does not allow your normal
account password to be used by third-party apps — you must generate a dedicated
16-character "App Password" instead.

1. Go to https://myaccount.google.com
2. Click **Security** in the left sidebar
3. Under "How you sign in to Google", click **2-Step Verification**
   - If it says "Off", enable it first. Google requires 2FA before App Passwords are available.
4. Scroll to the bottom of the 2-Step Verification page and click **App passwords**
   - If you don't see "App passwords", your account may have Advanced Protection enabled.
     Use a different Gmail account in that case.
5. In the "App name" field, type `Events App` and click **Create**
6. Google shows a 16-character password like `abcd efgh ijkl mnop`
7. **Copy it immediately** — it is shown only once. Store it somewhere safe (you'll need
   it in Step 6 when creating the `.env` file).

---

## Step 2 — Push the backend repo to GitHub

The VM will clone this repo during bootstrap, and pull from it on every deploy.

If you're working from the monorepo:

```bash
# One-time: add the backend prod repo as a remote
cd /path/to/events_app  # monorepo root
git remote add backend-prod https://github.com/nbosiacki/events_app_backend.git

# Push the backend/ subtree to the prod repo
git subtree push --prefix=backend backend-prod main
```

If you're working directly in a standalone `events_app_backend` checkout:

```bash
git push origin main
```

Verify on GitHub that the repo root contains `app/`, `Dockerfile`, `requirements.txt`,
`docker-compose.prod.yml`, and `deploy/`.

---

## Step 3 — Create the GCP Compute Engine VM

1. Go to https://console.cloud.google.com
2. Navigate to **Compute Engine** → **VM instances**
   - If prompted to enable the Compute Engine API, click **Enable** and wait ~1 minute
3. Click **Create Instance** and fill in these exact settings:

   | Field | Value | Reason |
   |---|---|---|
   | Name | `events-app` | Identifier for gcloud SSH and console |
   | Region | `us-central1` | Lowest-latency free-tier region |
   | Zone | `us-central1-a` | Consistent zone for all operations |
   | Machine family | General purpose | Standard workloads |
   | Series | E2 | Cost-optimised; sufficient for beta traffic |
   | Machine type | `e2-small` (1 vCPU, 2 GB RAM) | Handles FastAPI + MongoDB comfortably |
   | Boot disk → OS | Ubuntu 22.04 LTS | LTS = supported until 2027; stable Docker support |
   | Boot disk → Size | 20 GB | Enough for OS + Docker images + MongoDB data |
   | Boot disk → Type | Standard persistent disk | Cheaper than SSD; sufficient for this workload |
   | Firewall | Check **Allow HTTP** AND **Allow HTTPS** | Opens ports 80 and 443 |

4. Leave everything else as the default and click **Create**
5. Wait ~60 seconds for the VM to appear in the list with a green circle

---

## Step 4 — Reserve a static external IP

By default, GCP assigns an ephemeral IP that changes when the VM is stopped. If the IP
changes, your DNS record would point to the wrong place and the site would go down.
Reserving a static IP prevents this.

1. In GCP Console, go to **VPC Network** → **IP addresses**
2. Find the row where **In use by** shows `events-app`
3. The **Type** column shows "Ephemeral" — click the three-dot menu (⋮) at the end of that row
4. Click **Promote to static IP**
5. Give it the name `events-app-ip` and click **Reserve**
6. **Write down the IP address** shown in the **External IP address** column — you need
   it in the next step

---

## Step 5 — Point your domain to the VM

Log in to your domain registrar for nicholasbosiacki.com and add a DNS record:

| Field | Value |
|---|---|
| Type | A |
| Host / Name | `events` |
| Value / Points to | `YOUR_VM_IP` (from Step 4) |
| TTL | 300 |

Save the record. DNS propagation typically takes 2–10 minutes.

**Verify DNS before continuing** (run this from your local machine):

```bash
dig events.nicholasbosiacki.com
```

Look for a line like:
```
events.nicholasbosiacki.com. 300 IN A YOUR_VM_IP
```

Do not proceed to Step 8 (SSL) until this resolves correctly. Certbot will fail if DNS
is not pointing at the VM when it runs.

---

## Step 6 — Bootstrap the VM

**6a. Install and authenticate gcloud (one-time local setup)**

`gcloud` is Google's CLI tool. It is used to SSH into the VM and must be installed on
your local machine. Run this **on your local machine**, not the VM:

```bash
# Ubuntu/Debian
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list
sudo apt-get update && sudo apt-get install -y google-cloud-cli

# macOS
brew install google-cloud-sdk
```

Authenticate and set your project:

```bash
gcloud auth login                          # opens browser — sign in with your Google account
gcloud config set project YOUR_PROJECT_ID  # find project ID in GCP Console top nav bar
```

Verify:

```bash
gcloud config get-value project
```

**Important:** Run `gcloud` commands in your **local terminal**, not the VM terminal.
The two terminals look similar — double-check the prompt before running commands.
Local prompt: `nicholas-bosiacki@nicholas-bosiacki-desktop`
VM prompt: `nicholas@events-app`

**6b. SSH into the VM**

```bash
gcloud compute ssh events-app --zone=us-central1-a
```

`gcloud compute ssh` looks up the VM named `events-app` in your active project via the
GCP API, retrieves its external IP, and opens an SSH connection. It also automatically
generates and uploads SSH keys on first use — accept the defaults when prompted.

After a moment you'll see a prompt like `nicholas@events-app:~$`. All subsequent
commands in this step run **on the VM**.

**6c. Run the bootstrap script**

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/nbosiacki/events_app_backend/main/deploy/setup.sh)"
```

**What the script does:**
- Installs Docker CE and the Compose plugin
- Installs nginx and Certbot
- Clones the backend repo to `/opt/events_app_backend`
- Creates `/opt/events_frontend/dist/` (where frontend files will land after rsync)
- Installs a temporary HTTP-only nginx config (the full HTTPS config is applied after SSL is issued in Step 8)

It takes about 2–3 minutes. When it finishes you'll see `=== Bootstrap complete. Next steps ===`.

---

## Step 7 — Create the .env file on the VM

The `.env` file holds all production secrets. It is **never committed to git** — you
create it manually on the VM.

Still on the VM:

```bash
sudo nano /opt/events_app_backend/.env
```

Paste the following template, replacing every placeholder:

```env
APP_ENV=production
DEBUG=false
MONGODB_URL=mongodb://mongodb:27017

# JWT secret key — used to sign authentication tokens.
# If this changes, all existing sessions are immediately invalidated (users must log in again).
# Generate with: openssl rand -hex 32
JWT_SECRET_KEY=REPLACE_WITH_OUTPUT_OF_openssl_rand_-hex_32

# Admin API key — required for /api/sync/* and /api/analytics/* endpoints.
# Keep this secret. Anyone with this key can trigger syncs and read analytics.
# Generate with: openssl rand -hex 16
ADMIN_API_KEY=REPLACE_WITH_OUTPUT_OF_openssl_rand_-hex_16

# CORS — which origins the browser is allowed to make API requests from.
# Must be the exact production URL including scheme. No trailing slash.
ALLOWED_ORIGINS=https://events.nicholasbosiacki.com

# Frontend URL — used in password reset email links.
FRONTEND_URL=https://events.nicholasbosiacki.com

# External scraper database — where synced event data is read from.
# This is a read-only connection to a separate MongoDB instance.
# If unset, the sync endpoint is a no-op (safe to leave blank until the scraper is set up).
SCRAPER_MONGODB_URL=REPLACE_WITH_YOUR_SCRAPER_MONGODB_URL
SCRAPER_MONGODB_DB_NAME=agents_server

# Gmail SMTP — used to send password reset emails.
# SMTP_PASSWORD must be the App Password from Step 1, NOT your Gmail account password.
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=REPLACE_WITH_YOUR_GMAIL_ADDRESS
SMTP_PASSWORD=REPLACE_WITH_APP_PASSWORD_FROM_STEP_1
FROM_EMAIL=REPLACE_WITH_YOUR_GMAIL_ADDRESS
```

**Generate the JWT secret and admin key** on the VM:

```bash
openssl rand -hex 32   # paste as JWT_SECRET_KEY
openssl rand -hex 16   # paste as ADMIN_API_KEY
```

**Save the file:** `Ctrl+O` then `Enter` to save, then `Ctrl+X` to exit.

**Verify it saved correctly:**

```bash
sudo cat /opt/events_app_backend/.env
```

Store the admin key somewhere safe outside the VM (e.g. a password manager) — you'll
need it to trigger syncs and access the analytics dashboard.

---

## Step 8 — Issue the SSL certificate

DNS must be resolving to this VM before running this step. Verify from your local machine:

```bash
dig events.nicholasbosiacki.com @8.8.8.8
```

The answer section must show your VM IP. If it returns `NXDOMAIN`, wait a few minutes
and retry — DNS propagation can take up to 10 minutes. Do not proceed until it resolves.

**8a. Replace the bootstrap nginx config with an HTTP-only config**

The bootstrap script installs a minimal HTTP-only nginx config so nginx starts cleanly.
Verify it is in place before running Certbot:

```bash
sudo cat /etc/nginx/sites-available/events.nicholasbosiacki.com
```

It should contain only a simple `server { listen 80; ... }` block with no SSL lines.
If it contains SSL certificate paths (from a previous bootstrap attempt), replace it:

```bash
sudo bash -c 'cat > /etc/nginx/sites-available/events.nicholasbosiacki.com << EOF
server {
    listen 80;
    server_name events.nicholasbosiacki.com;
    root /var/www/html;
}
EOF'
sudo nginx -t && sudo systemctl reload nginx
```

**8b. Run Certbot**

```bash
sudo certbot --nginx -d events.nicholasbosiacki.com
```

When prompted:
- Enter your email address (used for expiry reminders — Certbot itself handles renewal)
- Agree to the Terms of Service: `Y`
- Share email with EFF: your choice

**8c. Install the full nginx config**

Now that the SSL certificates exist, install the full production nginx config:

```bash
sudo cp /opt/events_app_backend/deploy/nginx.conf /etc/nginx/sites-available/events.nicholasbosiacki.com
sudo nginx -t && sudo systemctl reload nginx
```

Certbot will edit the nginx config to add the certificate paths, then reload nginx.

**Verify auto-renewal is active:**

```bash
sudo systemctl status certbot.timer
```

You should see `active (waiting)`. Certbot checks for renewal twice daily and renews
automatically if the cert is within 30 days of expiry. You do not need to do anything.

---

## Step 9 — Start the application

On the VM:

```bash
cd /opt/events_app_backend
sudo docker compose -f docker-compose.prod.yml up -d --build
```

This builds the backend Docker image (installs Python deps) and starts both containers:
`mongodb` and `backend`. The first build takes about 2–3 minutes while pip downloads
packages. Subsequent builds are faster because Docker caches the pip layer.

**Check all containers are running:**

```bash
sudo docker compose -f docker-compose.prod.yml ps
```

Both `mongodb` and `backend` should show status `running`.

**Check the backend started cleanly:**

```bash
sudo docker compose -f docker-compose.prod.yml logs backend
```

Look for a line like:
```
INFO:     Application startup complete.
```

**Test the API directly from the VM:**

```bash
curl http://localhost:8000/health
# Expected: {"status":"healthy"}
```

---

## Step 10 — Deploy the frontend

The frontend (React SPA) is deployed separately by building it locally and rsyncing
the compiled `dist/` files to the VM. See `frontend/DEPLOYMENT.md` in the frontend
repo for full instructions.

Quick version — run from your local machine:

```bash
# From the monorepo
./deploy/deploy-frontend.sh

# Or from the frontend repo directly
cd path/to/events_app_frontend
./deploy/deploy.sh
```

After the rsync completes, the site is immediately live at
https://events.nicholasbosiacki.com.

---

## Step 11 — Generate invite codes

The app uses an invite-only registration model. Generate invite codes to share with
your initial users:

```bash
sudo docker compose -f docker-compose.prod.yml exec backend \
  python -m scripts.generate_invites --count 10 --env production
```

The codes are printed to the terminal. Copy them and send one to each person.

To generate more codes at any time:

```bash
sudo docker compose -f docker-compose.prod.yml exec backend \
  python -m scripts.generate_invites --count 5 --env production
```

---

## Step 12 — Trigger the first event sync

Load events into the database by triggering a sync from the external scraper database.
Run this from your **local machine**:

```bash
curl -X POST https://events.nicholasbosiacki.com/api/sync/trigger \
  -H "X-Admin-Key: YOUR_ADMIN_API_KEY"
```

Replace `YOUR_ADMIN_API_KEY` with the key from Step 7.

Expected response: `{"message": "sync started"}`. The sync runs in the background.
Watch progress on the VM:

```bash
sudo docker compose -f docker-compose.prod.yml logs -f backend
```

After the sync finishes, events will appear on the site. The sync also runs
automatically every day at 03:00 UTC.

---

## Step 13 — Final verification

Open a browser and confirm:

1. **App loads**: https://events.nicholasbosiacki.com
2. **API health**: https://events.nicholasbosiacki.com/api/health → `{"status":"healthy"}`
3. **Register**: click Register, enter an invite code from Step 11, fill the form — should log you in
4. **Password reset**: click "Forgot password", enter your email — should receive an email within a minute
5. **Events visible**: home page shows events after the sync in Step 12

---

## Deploying updates

### Backend changes (code or dependencies)

From the **monorepo root** on your local machine:

```bash
./deploy/deploy-backend.sh
```

This pushes the `backend/` subtree to the `events_app_backend` repo, SSHes to the VM,
pulls the new code, and rebuilds the Docker container. Expect ~10 seconds of downtime
while the container restarts.

From a standalone `events_app_backend` checkout:

```bash
git push origin main
./deploy/deploy.sh
```

### Frontend changes

From the **monorepo root**:

```bash
./deploy/deploy-frontend.sh
```

This builds the React app locally and rsyncs `dist/` to the VM. **Zero downtime** —
nginx serves the updated files immediately without any restart.

---

## Useful commands (run on the VM)

```bash
# View running containers
sudo docker compose -f /opt/events_app_backend/docker-compose.prod.yml ps

# Tail live backend logs
sudo docker compose -f /opt/events_app_backend/docker-compose.prod.yml logs -f backend

# Restart backend only (e.g. after editing .env)
sudo docker compose -f /opt/events_app_backend/docker-compose.prod.yml restart backend

# Stop everything
sudo docker compose -f /opt/events_app_backend/docker-compose.prod.yml down

# Start without rebuilding
sudo docker compose -f /opt/events_app_backend/docker-compose.prod.yml up -d

# Generate more invite codes
sudo docker compose -f /opt/events_app_backend/docker-compose.prod.yml exec backend \
  python -m scripts.generate_invites --count 5 --env production

# Manually trigger event sync
curl -X POST https://events.nicholasbosiacki.com/api/sync/trigger \
  -H "X-Admin-Key: YOUR_ADMIN_API_KEY"

# Check sync status
curl https://events.nicholasbosiacki.com/api/sync/status \
  -H "X-Admin-Key: YOUR_ADMIN_API_KEY"

# Check SSL certificate expiry
sudo certbot certificates
```
