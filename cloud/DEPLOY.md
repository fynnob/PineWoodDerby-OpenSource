# Supabase Cloud Backend — Deployment Guide

This directory contains the SQL schema and Edge Function for running the
Pinewood Derby app with the **Supabase** backend module (Module 1).

---

## Prerequisites

- A free [Supabase](https://supabase.com) account
- [Supabase CLI](https://supabase.com/docs/guides/cli) installed:
  ```
  npm install -g supabase
  ```

---

## Step 1 — Create a Supabase Project

1. Go to [app.supabase.com](https://app.supabase.com) → **New Project**
2. Note your **Project URL** and **anon key** from
   *Settings → API → Project API Keys*

---

## Step 2 — Apply the Database Schema

1. In the Supabase dashboard, open **SQL Editor**
2. Paste the contents of `supabase/schema.sql` and click **Run**

This creates the following tables:
- `cars` — registered racers
- `rounds` — racing rounds
- `heats` — individual heats per round
- `heat_entries` — lane assignments per heat
- `heat_results` — finish order per heat
- `race_state` — singleton: current heat/round, email toggle

---

## Step 3 — Configure Storage Buckets

In the Supabase dashboard under **Storage**, create two buckets:

| Bucket name     | Public | Purpose                          |
|-----------------|--------|----------------------------------|
| `car-images`    | ✅ Yes | Car photos uploaded at registration |
| `announcements` | ✅ Yes | Sponsor images / announcer slides |

For each bucket, add a policy allowing anonymous uploads and reads:
- **INSERT policy** (anon): `true`
- **SELECT policy** (anon): `true`

---

## Step 4 — Deploy the Email Edge Function

The `send-registration-email` function sends QR codes to parents via SMTP.

```bash
# Link to your project (replace PROJECT_REF with your Supabase project ID)
supabase login
supabase link --project-ref YOUR_PROJECT_REF

# Set SMTP credentials as secrets
supabase secrets set SMTP_HOST=smtp.example.com
supabase secrets set SMTP_PORT=587
supabase secrets set SMTP_USER=your@email.com
supabase secrets set SMTP_PASSWORD=your-app-password
supabase secrets set SMTP_FROM=your@email.com

# Deploy the function
supabase functions deploy send-registration-email
```

The function is located at `supabase/functions/send-registration-email/index.ts`.

> **Note:** Email is optional. The app works fully without it. Toggle it on/off
> at runtime via Admin → Settings.

---

## Step 5 — Run the Setup Wizard

Back in the project root:

```bash
python3 setup.py
```

- Choose **Supabase** for the backend module
- Paste your **Project URL** and **anon key**
- Complete the wizard and run `python3 run.py` (or open `frontend/index.html` directly)

---

## Realtime

Supabase Realtime is enabled by default. Make sure the following tables have
Realtime enabled in *Database → Replication → Tables*:

- `cars`
- `heats`
- `heat_results`
- `race_state`

---

## Security Notes

The app uses Supabase Row Level Security (RLS) disabled by default for
simplicity at local events. If you deploy publicly:

1. Enable RLS on all tables
2. Add appropriate policies
3. Rotate the anon key regularly

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Registration fails | Check anon key and table permissions |
| Realtime not updating | Enable Replication for the relevant tables |
| Email not sending | Check secrets with `supabase secrets list`, verify SMTP creds |
| Images not loading | Confirm `car-images` bucket is public with anon SELECT policy |
