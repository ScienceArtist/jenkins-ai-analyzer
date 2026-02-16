# Jira Dashboard Setup Guide

This guide shows you how to set up a **Jira dashboard** that automatically analyzes multiple Jenkins pipelines daily and displays the results.

## 🎯 What You'll Get

- **Automated Daily Analysis**: Jira runs batch analysis of all your pipelines every day
- **Persistent Storage**: Results stored for 30 days with historical view
- **Beautiful Dashboard**: Embedded dashboard in Jira showing all pipeline health
- **Team Visibility**: Anyone with Jira access can view the dashboard

---

## 📋 Prerequisites

1. Jenkins Log Analyzer running on a server accessible from Jira
2. Jira Cloud or Data Center with automation permissions
3. Groq API key with Zero Data Retention enabled
4. List of Jenkins pipeline URLs to monitor

---

## 🚀 Step-by-Step Setup

### Step 1: Configure Your Pipelines

Create a configuration file `pipelines-config.json` with your pipelines:

```json
{
  "pipelines": [
    {
      "name": "Production API",
      "jenkins_url": "https://jenkins.example.com/job/prod-api/"
    },
    {
      "name": "Frontend Build",
      "jenkins_url": "https://jenkins.example.com/job/frontend/"
    },
    {
      "name": "Backend Services",
      "jenkins_url": "https://jenkins.example.com/job/backend-services/"
    }
  ],
  "groq_api_key": "YOUR_GROQ_API_KEY",
  "model": "llama-3.3-70b-versatile"
}
```

### Step 2: Create Jira Automation Rule

1. Go to **Jira Settings** → **System** → **Automation**
2. Click **Create Rule**
3. Name it: "Daily Jenkins Pipeline Analysis"

#### Trigger: Scheduled
- **Frequency**: Daily
- **Time**: 2:00 AM (or your preferred time)
- **Days**: Every day

#### Action 1: Send Web Request
```
URL: http://your-server:5000/api/batch-analyze
Method: POST
Headers: Content-Type: application/json

Body (Custom data):
{
  "pipelines": [
    {
      "name": "Production API",
      "jenkins_url": "https://jenkins.example.com/job/prod-api/"
    },
    {
      "name": "Frontend Build",
      "jenkins_url": "https://jenkins.example.com/job/frontend/"
    },
    {
      "name": "Backend Services",
      "jenkins_url": "https://jenkins.example.com/job/backend-services/"
    }
  ],
  "groq_api_key": "{{lookupSecrets.groqApiKey}}",
  "model": "llama-3.3-70b-versatile",
  "store_results": true
}
```

**Important**: Store your Groq API key in Jira Secrets:
- Go to **Jira Settings** → **System** → **Secrets**
- Add secret: `groqApiKey` = YOUR_GROQ_API_KEY
- Reference as `{{lookupSecrets.groqApiKey}}`

#### Action 2: Log Action (Optional)
```
Log message: Jenkins pipeline analysis completed at {{now}}
```

### Step 3: Create Jira Dashboard

1. Go to **Dashboards** → **Create Dashboard**
2. Name it: "Jenkins Pipeline Health"
3. Add **Web Panel** or **iFrame Gadget**

#### Option A: iFrame Gadget (Recommended)
- Install "iFrame Gadget" from Atlassian Marketplace (if not installed)
- Add gadget to dashboard
- Configure:
  - **Title**: Jenkins Analysis Dashboard
  - **URL**: `http://your-server:5000/dashboard`
  - **Height**: 800px (or auto)

#### Option B: Web Link
- Add "Web Link" gadget
- **Title**: View Jenkins Analysis
- **URL**: `http://your-server:5000/dashboard`
- Users click to open in new tab

### Step 4: Set Dashboard Permissions

1. Click **•••** (More) on dashboard → **Edit Permissions**
2. Set permissions:
   - **View**: All users / Specific groups
   - **Edit**: Admins only

---

## 📊 Dashboard Features

The dashboard displays:

- **Total Pipelines**: Number of pipelines monitored
- **Total Jobs Analyzed**: Sum of all jobs across pipelines
- **Success Rate**: Percentage of successful analyses
- **Last Analysis Date**: When the last batch ran

Each pipeline card shows:
- ✅/❌ Status indicator
- Pipeline name and Jenkins URL
- Number of jobs analyzed
- Expandable job details with AI analysis

### Dashboard Controls

- **🔄 Refresh**: Manually refresh data
- **Date Selector**: View historical results (last 30 days)
- **📥 Export JSON**: Download results for external processing
- **Auto-refresh**: Dashboard auto-refreshes every 5 minutes

---

## 🔧 API Endpoints Reference

### POST /api/batch-analyze
Analyze multiple pipelines and store results.

**Request:**
```json
{
  "pipelines": [
    {"name": "Pipeline1", "jenkins_url": "https://..."},
    {"name": "Pipeline2", "jenkins_url": "https://..."}
  ],
  "groq_api_key": "gsk_...",
  "model": "llama-3.3-70b-versatile",
  "store_results": true
}
```

**Response:**
```json
{
  "batch_id": "uuid",
  "timestamp": 1234567890.0,
  "date": "2026-02-16",
  "pipelines": [
    {
      "name": "Pipeline1",
      "status": "success",
      "jobs": [...],
      "job_count": 3
    }
  ]
}
```

### GET /api/dashboard/latest
Get the most recent batch analysis results.

### GET /api/dashboard/results?date=YYYY-MM-DD
Get results for a specific date (optional).

### GET /dashboard
Render the dashboard HTML page.

---

## 🔐 Security Best Practices

1. **Use HTTPS**: Deploy behind nginx/Apache with SSL
2. **Restrict Access**: Use firewall rules to allow only Jira IP
3. **Secure API Keys**: Store in Jira Secrets, never hardcode
4. **Enable Groq ZDR**: https://console.groq.com/settings/data-controls
5. **Limit Data Retention**: Dashboard stores only 30 days

---

## 🧪 Testing

### Test Batch Analysis Manually
```bash
curl -X POST http://your-server:5000/api/batch-analyze \
  -H "Content-Type: application/json" \
  -d '{
    "pipelines": [
      {"name": "Test", "jenkins_url": "https://ci.jenkins.io"}
    ],
    "groq_api_key": "YOUR_KEY",
    "model": "llama-3.3-70b-versatile"
  }'
```

### View Dashboard
Open in browser: `http://your-server:5000/dashboard`

### Check Stored Results
```bash
curl http://your-server:5000/api/dashboard/latest
```

---

## 📈 Example Workflow

1. **2:00 AM**: Jira automation triggers batch analysis
2. **2:05 AM**: Analysis completes, results stored
3. **9:00 AM**: Team opens Jira dashboard
4. **9:01 AM**: Dashboard loads latest results automatically
5. **Throughout day**: Team views pipeline health, clicks to expand details
6. **Next day**: Process repeats, historical data available

---

## 🐛 Troubleshooting

### Dashboard shows "No results available"
- Run batch analysis manually first
- Check Jira automation logs for errors
- Verify server is accessible from Jira

### Automation fails
- Check Jira automation execution logs
- Verify Groq API key is valid
- Test endpoint manually with curl

### Dashboard not loading in iFrame
- Check browser console for CORS errors
- Verify Flask CORS is enabled (already done in app.py)
- Try Web Link option instead

---

## 🎉 You're Done!

Your team now has a beautiful, automated Jenkins pipeline health dashboard in Jira!

**Next Steps:**
- Customize pipeline list as needed
- Adjust automation schedule
- Share dashboard with team
- Monitor pipeline health trends

---

**Need help?** Check the main [README.md](README.md) or [JIRA_INTEGRATION.md](JIRA_INTEGRATION.md)

