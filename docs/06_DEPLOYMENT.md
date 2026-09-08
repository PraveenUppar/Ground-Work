# 06 — Deploying it

Two ways, for two different purposes.

- **A permanent link** anyone can open — Streamlit Community Cloud, free, about
  fifteen minutes.
- **A live demo** during an interview, straight from your laptop — one command,
  about two minutes.

---

## Before either: the things that will bite you

**The API allowance is yours, and it is small.** The free Gemini tier gives 500
requests a day on the model we use. Every upload spends some. A public link
means strangers spending your allowance, and one 400-page document would take
the lot in a single click. `MOST_PAGES_ONE_UPLOAD_MAY_PROCESS` caps this at 25
pages by default — leave it low anywhere a stranger can reach it.

**Everyone shares one database.** Uploads land in the same Supabase instance and
are compared against everything already there. That is the point of the system,
but it also means an uploaded document is visible to the next visitor. Do not
put a link to this anywhere a confidential PDF might be dropped into it.

**Supabase free projects pause after a week of no activity.** A paused project
refuses connections, and the app will show a database error. Open the Supabase
dashboard to wake it. If this is going to sit unused between an interview being
booked and it happening, wake it the morning of.

**`.env` must never be committed.** It is already in `.gitignore`. Check before
you push:

```bash
git status --short
```

If `.env` appears in that list, stop and fix `.gitignore` before continuing.

---

## Option A — Streamlit Community Cloud (a permanent link)

### 1. Put the code on GitHub

```bash
git init
git add .
git commit -m "Ground Work: a fact knowledge layer for PDFs"
```

Create an empty repository on GitHub, then:

```bash
git remote add origin https://github.com/<you>/ground-work.git
git branch -M main
git push -u origin main
```

The repo may be private — Community Cloud can deploy from private repos.

**Note on the sample PDFs.** `.gitignore` currently excludes
`data/input_pdfs/*.pdf`, so they are not pushed. The deployed app will start
with an empty database unless it points at the Supabase instance you have
already filled — which it will, since the connection string is the same one.
If you want a reviewer to be able to re-run the pipeline from scratch, delete
that line from `.gitignore` and commit the PDFs (about 10 MB).

### 2. Deploy

Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.

**New app** → pick your repository → set:

| Field | Value |
|---|---|
| Branch | `main` |
| Main file path | `groundwork/app/streamlit_app.py` |
| Python version | 3.13 |

### 3. Add the secrets

Before clicking deploy, open **Advanced settings → Secrets** and paste this,
with your real values:

```toml
DATABASE_URL = "postgresql://postgres.xxxx:PASSWORD@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
GOOGLE_API_KEY = "your-key"
GEMINI_MODEL = "gemini-3.5-flash-lite"
ADJUDICATION_MODEL = "gemini-3.5-flash-lite"
LLM_REQUESTS_PER_MINUTE = "8"
MOST_PAGES_ONE_UPLOAD_MAY_PROCESS = "15"
```

Use the **session pooler** connection string, not the direct one — Supabase's
direct connections are IPv6-only on the free tier.

`groundwork/shared/config.py` reads environment variables first and Streamlit's
secrets store second, so the same code runs unchanged locally and deployed.

### 4. Deploy, then check

You get a URL like `https://ground-work.streamlit.app`. Open it and confirm the
Overview page shows your document counts. If it shows a database error, the
connection string is wrong or the Supabase project is paused.

### What to expect

- **It sleeps.** After a few days idle, the first visitor sees a "waking up"
  screen for thirty seconds. Harmless, but open it yourself before sending the
  link to anyone.
- **1 GB of memory.** The app fits comfortably. `scikit-learn` was removed from
  `requirements.txt` because it was never used and cost 40 MB of install for
  nothing.
- **Uploads are slow.** Fifteen pages is roughly two to four minutes, almost all
  of it waiting on the model. The progress panel exists so that wait is
  understood rather than merely endured.

---

## Option B — a tunnel from your laptop (a live demo)

For showing it in an interview, this beats deploying: no build, no sleep, and
the machine doing the work is yours.

Start the app as usual:

```bash
.\.venv\Scripts\streamlit.exe run groundwork\app\streamlit_app.py
```

Then, in a second terminal, expose it. With
[cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/):

```bash
cloudflared tunnel --url http://localhost:8501
```

It prints a public `https://something.trycloudflare.com` URL. No account needed.
Close the terminal and the link dies — which is exactly what you want for a
temporary demo.

`ngrok http 8501` does the same thing if you already have it.

---

## Option C — anywhere else

The app is an ordinary Streamlit application with no special requirements, so
Hugging Face Spaces, Render, Railway and Fly.io all work. Two things every one
of them needs:

- **Start command:** `streamlit run groundwork/app/streamlit_app.py --server.port $PORT --server.address 0.0.0.0`
- **Environment variables:** the same keys as the secrets block above. Every
  platform sets these as real environment variables, which `config.py` reads
  first, so nothing needs changing.

No system packages are required — PyMuPDF and pdfplumber both install from
wheels.

---

## If you would rather not host the pipeline at all

There is a smaller option worth considering: deploy the interface **read-only**,
with the upload page removed, and run the pipeline yourself when a new document
needs adding.

It removes every problem above at once — no shared quota, no stranger
uploading anything, no abuse surface — and a reviewer still sees the facts, the
evidence, the four cases and the reasoning, which is what they came for. To do
it, delete `"Upload a PDF": page_upload` from the `PAGES` dictionary at the
bottom of `streamlit_app.py`.

The brief does ask for upload, so this is the fallback rather than the plan —
but it is the right answer if the link is going somewhere public and you cannot
watch it.
