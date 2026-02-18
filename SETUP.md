# Setup Guide

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
```

Edit `.env`:
```bash
# Required for automated daily analysis
GROQ_API_KEY=your_groq_api_key_here
JENKINS_URL=https://your-jenkins-server.com

# Generate a secure secret key
FLASK_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Set admin password hash
ADMIN_PASSWORD_HASH=<see_step_3>

# Optional: Default model for daily analysis
DEFAULT_MODEL=llama-3.3-70b-versatile
```

### 3. Set Admin Password
```bash
python3 -c "import hashlib; print(hashlib.sha256('YourPassword123'.encode()).hexdigest())"
```
Copy the output and add to `.env` as `ADMIN_PASSWORD_HASH`

### 4. Run the Application
```bash
python3 app.py
```

### 5. Access the System

- **User Analysis Page**: http://localhost:5000/ (users provide their own credentials)
- **Dashboard**: http://localhost:5000/dashboard (view analysis results)
- **Admin Panel**: http://localhost:5000/admin (password protected)

---

## Daily Automated Analysis

### Setup Cron Job

1. Go to Admin Panel → Daily Analysis Schedule
2. Set your preferred time (e.g., 2:00 AM)
3. Copy the cron command
4. Add to crontab:
```bash
crontab -e
# Paste the copied command
```

### Manual Trigger
From dashboard, click "▶️ Run Analysis Now"

Or via API:
```bash
curl -X POST http://localhost:5000/api/run-daily-analysis
```

---

## Dashboard Features

- **Latest Results**: View most recent analysis
- **Date Range**: Filter by date range
- **New Failures**: Identify recently failing jobs
- **Export**: Download results as JSON

---

## Jira Integration (Optional)

See [JIRA_INTEGRATION.md](JIRA_INTEGRATION.md) for detailed setup.

Quick setup:
1. Get Jira Personal Access Token
2. Enter in analysis page:
   - Jira URL
   - Ticket ID
   - PAT token
3. Enable "Post results to Jira ticket"
4. Run analysis

---

## Troubleshooting

### Database Issues
```bash
rm jenkins_analysis.db
python3 app.py  # Recreates database
```

### Invalid Password
Verify hash matches:
```bash
python3 -c "import hashlib; print(hashlib.sha256('YourPassword123'.encode()).hexdigest())"
grep ADMIN_PASSWORD_HASH .env
```

### Rate Limits
The system automatically handles rate limits by:
- Detecting rate limit errors
- Extracting wait time
- Pausing and retrying

---

## Production Deployment

1. Use a production WSGI server (gunicorn, uWSGI)
2. Set strong admin password
3. Use HTTPS
4. Backup `jenkins_analysis.db` regularly
5. Monitor logs
6. Set up cron job for daily analysis

Example with gunicorn:
```bash
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

