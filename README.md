# Jenkins Log Analyzer

A powerful web application that analyzes Jenkins job logs using AI-powered insights from the Groq API.

## Features

- 🔗 **Jenkins Integration**: Connect to any Jenkins instance using just its URL (no Jenkins credentials required)
- 🤖 **AI Analysis**: Uses Groq chat models (LLaMA 3.3, Mixtral, Gemma) for intelligent log analysis
- 🔄 **Automatic Model Fallback**: Automatically switches to alternative models when rate limits or errors occur
- 📊 **Pipeline Detection**: Automatically detects and analyzes pipeline jobs (including `Test-pipelines` view)
- ⚡ **Real-time Streaming**: Server-Sent Events (SSE) for live progress updates during analysis
- 💡 **Smart Insights**: Provides:
  - Build status summaries
  - Key errors and warnings identification
  - Performance insights
  - Improvement recommendations
- 📥 **Export Options**: Export analysis results as JSON or XML
- 🔌 **Async API**: Automation-friendly async analysis endpoint with tracking URLs
- 🎨 **Beautiful UI**: Modern, responsive web interface

## Prerequisites

- Python 3.11+
- Flask and dependencies (see requirements.txt)
- Groq API key
- Jenkins instance reachable from the machine running this app

## Installation

1. Clone or navigate to the project directory:
```bash
cd jenkins-log-analyzer
```

2. Install dependencies:
```bash
pip3.11 install -r requirements.txt
```

3. Set up environment variables:
```bash
# Create .env file with your Groq API key
echo "GROQ_API_KEY=your_api_key_here" > .env
```

## Running the Application

Start the Flask development server:
```bash
python3.11 app.py
```

The application will be available at `http://localhost:5000`

## Usage

### Web UI (Single Analysis)

1. Open `http://localhost:5000` in your browser
2. Paste your Groq API key and click **Fetch Available Models**
3. Select a chat model (**⭐ models** are recommended for larger logs)
4. Enter your Jenkins URL (e.g., `https://jenkins.example.com` or a specific job URL)
5. Click **Start Analysis** and watch real-time streaming results
6. Export results as JSON or XML once analysis completes

### Dashboard (Multi-Pipeline Monitoring)

1. Open `http://localhost:5000/dashboard` in your browser
2. View latest batch analysis results for all monitored pipelines
3. Click pipeline cards to expand and view detailed job analyses
4. Use date selector to view historical results (last 30 days)
5. Export results as JSON for external processing

**For Jira Integration**: See [JIRA_DASHBOARD_SETUP.md](JIRA_DASHBOARD_SETUP.md) for automated daily analysis and dashboard embedding.

### 🔄 Automatic Model Fallback

The analyzer automatically handles rate limits and model errors by trying alternative models:

**When Fallback Triggers:**
- ⏱️ Rate limit exceeded (`rate_limit_exceeded`)
- 🚫 Model decommissioned (`model_decommissioned`)
- ❌ Model not found (`model_not_found`)

**Fallback Order (Prioritizes Accuracy):**
1. Your selected model (e.g., `llama-3.3-70b-versatile`)
2. `llama-3.3-70b-specdec` - Speculative decoding variant
3. `mixtral-8x7b-32768` - Fast, efficient, 32K context
4. `gemma2-9b-it` - Good balance of speed/quality
5. `llama3-70b-8192` - Older but stable
6. `llama3-8b-8192` - Smaller, faster
7. `llama-3.1-8b-instant` - Fastest (last resort for accuracy)

**Note:** Decommissioned models (`llama-3.1-70b-versatile`, `gemma-7b-it`) have been removed from the fallback list.

**Example Output:**
```
**BEGINNING SECTION:** (using mixtral-8x7b-32768)
Build completed successfully with warnings...
```

The system will automatically retry with the next available model until analysis succeeds or all models are exhausted.

## How It Works

1. **Jenkins Connection**: Connects to Jenkins using only the URL (no Jenkins credentials required)
2. **Pipeline Detection**: Automatically detects if a job is part of a pipeline and analyzes related jobs
3. **Log Collection**: Fetches recent build logs for selected jobs (chunked to ~4000 chars for token limits)
4. **Automatic Model Fallback**: If rate limits or errors occur, automatically tries alternative models in this order:
   - Primary model (your selection)
   - `llama-3.1-70b-versatile` (similar capability, different rate limit pool)
   - `mixtral-8x7b-32768` (fast, efficient)
   - `gemma2-9b-it` (smaller, higher rate limits)
   - `llama-3.1-8b-instant` (fastest fallback)
   - Additional models as available
4. **AI Analysis**: Sends log chunks (beginning, middle, end sections) to Groq API for intelligent analysis
5. **Real-time Streaming**: Uses Server-Sent Events (SSE) to stream progress and results live to the UI
6. **Results Display**: Shows analysis results with expandable job cards and export options

## API Endpoints

### GET /
Returns the main web interface.

### POST /api/models
Fetches available Groq models for the provided API key.

**Request Body:**
```json
{
  "groq_api_key": "your_groq_api_key"
}
```

**Response:**
```json
{
  "models": [
    {"id": "llama-3.3-70b-versatile", "name": "⭐ llama-3.3-70b-versatile"},
    {"id": "mixtral-8x7b-32768", "name": "⭐ mixtral-8x7b-32768"}
  ]
}
```

### POST /api/analyze
Streaming (Server-Sent Events) analysis endpoint used by the web UI.

**Request Body:**
```json
{
  "jenkins_url": "https://jenkins.example.com",
  "groq_api_key": "your_groq_api_key",
  "model": "llama-3.3-70b-versatile"
}
```

**Response:** SSE stream with events:
- `status`: Progress messages
- `pipeline_info`: Pipeline detection results
- `progress`: Job count and progress percentage
- `job_result`: Individual job analysis results
- `complete`: Analysis completion message

### POST /api/analyze-async
Starts an asynchronous analysis for automation use cases.

**Request Body:**
```json
{
  "jenkins_url": "https://jenkins.example.com/job/SomeJob/123/",
  "groq_api_key": "your_groq_api_key",
  "model": "llama-3.3-70b-versatile"
}
```

**Response (202 Accepted):**
```json
{
  "tracking_id": "<uuid>",
  "status": "pending",
  "status_url": "http://localhost:5000/api/status/<uuid>",
  "results_url": "http://localhost:5000/api/status/<uuid>"
}
```

### GET /api/status/<tracking_id>
Returns the current status and any collected results for an async analysis.

**Response:**
```json
{
  "tracking_id": "<uuid>",
  "status": "complete",
  "created_at": 1234567890.0,
  "updated_at": 1234567890.0,
  "jenkins_url": "https://jenkins.example.com",
  "model": "llama-3.3-70b-versatile",
  "results": [
    {
      "job": "JobName",
      "build": 123,
      "analysis": "...",
      "log_size": 12345
    }
  ],
  "pipeline": {
    "name": "PipelineName",
    "job_count": 5
  }
}
```

### GET /api/jira/<tracking_id>
Returns analysis results formatted for Jira comments (Atlassian Document Format).

**Response:**
```json
{
  "tracking_id": "<uuid>",
  "status": "complete",
  "jira_adf": {
    "version": 1,
    "type": "doc",
    "content": [...]
  },
  "plain_text_summary": "Analyzed 3 job(s) in pipeline 'MyPipeline'"
}
```

Use the `jira_adf` field directly in Jira API comment body.

### POST /api/batch-analyze
Analyze multiple pipelines and store results for dashboard viewing.

**Request Body:**
```json
{
  "pipelines": [
    {"name": "Pipeline1", "jenkins_url": "https://jenkins.example.com/job/Job1/"},
    {"name": "Pipeline2", "jenkins_url": "https://jenkins.example.com/job/Job2/"}
  ],
  "groq_api_key": "your_groq_api_key",
  "model": "llama-3.3-70b-versatile",
  "store_results": true
}
```

**Response:**
```json
{
  "batch_id": "<uuid>",
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
Returns the most recent batch analysis results.

### GET /api/dashboard/results?date=YYYY-MM-DD
Returns all stored results or results for a specific date.

### GET /dashboard
Renders the dashboard HTML page for viewing multi-pipeline analysis results.

## Jira Integration

### 🎯 Recommended: Automated Dashboard (Best for Teams)

Set up a **Jira dashboard** that automatically analyzes multiple pipelines daily:

1. **Daily Automation**: Jira triggers batch analysis every day at 2 AM
2. **Persistent Storage**: Results stored for 30 days with historical view
3. **Team Dashboard**: Embedded dashboard in Jira showing all pipeline health
4. **Zero Manual Work**: Fully automated, team just views results

**📖 Full Setup Guide**: See [JIRA_DASHBOARD_SETUP.md](JIRA_DASHBOARD_SETUP.md)

**Quick Start:**
```yaml
# Jira Automation Rule (Daily at 2 AM)
Trigger: Scheduled (Daily, 2:00 AM)
Action: Send Web Request
  URL: http://your-server:5000/api/batch-analyze
  Body: {
    "pipelines": [
      {"name": "API", "jenkins_url": "https://jenkins.../job/api/"},
      {"name": "Frontend", "jenkins_url": "https://jenkins.../job/frontend/"}
    ],
    "groq_api_key": "{{lookupSecrets.groqApiKey}}",
    "model": "llama-3.3-70b-versatile"
  }

# Then embed dashboard in Jira
Dashboard URL: http://your-server:5000/dashboard
```

### Alternative: Per-Issue Analysis

For analyzing specific builds when issues are created:

**📖 Full Guide**: See [JIRA_INTEGRATION.md](JIRA_INTEGRATION.md)

```yaml
Trigger: Issue Created
Condition: Field "Jenkins Build URL" is not empty
Actions:
  1. POST /api/analyze-async with Jenkins URL
  2. Wait 45 seconds
  3. GET /api/jira/{tracking_id} for formatted results
  4. Add comment with analysis
```

## Project Structure

```
jenkins-log-analyzer/
├── app.py                 # Flask application and Jenkins analyzer
├── requirements.txt       # Python dependencies
├── .env                   # Environment variables (API keys)
├── templates/
│   └── index.html        # Web interface
└── README.md             # This file
```

## Technologies Used

- **Backend**: Flask (Python web framework)
- **Frontend**: HTML5, CSS3, JavaScript (single-page app)
- **AI**: Groq API (chat completions)
- **Jenkins Integration**: Jenkins REST API
- **HTTP Client**: Requests library

## Security Notes

- Never commit your API keys to version control
- Use environment variables for sensitive data
- The Groq API key is sent securely to Groq servers and should never be committed to git

## Troubleshooting

### Connection Error to Jenkins
- Verify Jenkins URL is correct and accessible
- Ensure Jenkins instance is reachable from your network
- Check if Jenkins requires authentication (this tool works best with public Jenkins instances)

### Groq API Errors
- Verify your Groq API key is valid
- Check your API quota and rate limits
- Ensure you have internet connectivity
- **Important**: Enable Zero Data Retention (ZDR) in Groq settings to protect sensitive log data

### No Jobs Found
- Verify the Jenkins instance has jobs configured
- Try using a specific job URL instead of the base URL
- Check if the Jenkins instance is publicly accessible

## Future Enhancements

- Historical trend analysis across multiple builds
- Custom analysis templates and prompts
- Export analysis results to PDF/CSV
- Webhook integration for automatic analysis on build completion
- Multi-language support
- Enhanced pipeline visualization

## Author

Created by **Chandravijay Agrawal** ([@cagrawalcode](https://github.com/cagrawalcode))

## License

MIT License

## Support

For issues or questions:
- Check the [Jenkins REST API documentation](https://www.jenkins.io/doc/book/using/remote-access-api/)
- Check the [Groq API documentation](https://console.groq.com/docs)
- Enable Zero Data Retention in [Groq Data Controls](https://console.groq.com/settings/data-controls) to protect sensitive data
