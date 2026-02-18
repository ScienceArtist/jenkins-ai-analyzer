# Jenkins Log Analyzer

AI-powered Jenkins log analysis using Groq LLMs. Automatically analyzes build failures, identifies root causes, and integrates with Jira.

## Quick Start

See **[SETUP.md](SETUP.md)** for installation and configuration.

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
python3 app.py
```

## Key Benefits

- **Automated Analysis**: Daily automated analysis of all Jenkins jobs
- **Smart Filtering**: Only analyzes failed/unstable jobs (80% reduction in volume)
- **Parallel Processing**: 10x faster with concurrent job analysis
- **Intelligent Extraction**: Extracts only relevant failure logs (90% size reduction)
- **Cost Efficient**: ~$0.01-0.02 per job vs $0.10-0.20 manual analysis
- **Jira Integration**: Automatic ticket updates with analysis results

---

## Features

### Automated Daily Analysis
- Runs analysis for all jobs automatically
- SQLite database for historical tracking
- Dashboard with date range filtering and new failure detection
- Password-protected admin panel
- Automatic rate limit handling with retry

### Analysis Capabilities
- **AI Models**: LLaMA 3.3, Mixtral, Gemma via Groq API
- **Smart Extraction**: Identifies and extracts only relevant failure logs
- **Model Fallback**: Automatically switches models on rate limits/errors
- **Pipeline Detection**: Analyzes entire pipeline jobs automatically
- **Parallel Processing**: Configurable worker pools for concurrent analysis
- **Root Cause Analysis**: Deep-dive mode for actionable insights

### Jira Integration
- Automatic ticket commenting with analysis results
- Similar issue search
- Log file attachments
- API v2 and v3 support

### User Interface
- Real-time streaming progress (Server-Sent Events)
- Dark/light mode
- Modern responsive design
- Export results as JSON/XML

## Installation

See [SETUP.md](SETUP.md) for detailed setup instructions.

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
python3 app.py
```

Access at `http://localhost:5000`

## Usage

### User Analysis Page (`/`)
Users provide their own credentials:
1. Enter Groq API key and fetch models
2. Select model (LLaMA 3.3 recommended)
3. Enter Jenkins URL
4. Configure options (RCA mode, parallel processing, etc.)
5. Start analysis and view real-time results

### Dashboard (`/dashboard`)
View automated analysis results:
- Latest analyses
- Filter by date range
- Identify new failures
- Export as JSON
- Trigger manual analysis

### Admin Panel (`/admin`)
Password-protected configuration:
- Manage API keys
- Configure Jenkins URLs
- Set daily analysis schedule
- Jira integration setup

### Automatic Model Fallback

Handles rate limits automatically:
1. Selected model → `llama-3.3-70b-specdec` → `mixtral-8x7b-32768` → `gemma2-9b-it` → `llama3-70b-8192` → `llama3-8b-8192` → `llama-3.1-8b-instant`

## How It Works

1. **Jenkins Connection**: Connects using URL only (no Jenkins credentials needed)
2. **Smart Filtering**: Identifies failed/unstable jobs (80% volume reduction)
3. **Pipeline Detection**: Automatically detects and analyzes pipeline jobs
4. **Intelligent Extraction**:
   - Parses Robot Framework `output.xml` for failed tests
   - Extracts only relevant failure sections (90% size reduction)
   - Falls back to console logs if needed
5. **Parallel Processing**: Analyzes multiple jobs concurrently
6. **AI Analysis**: Sends focused logs to Groq API with RCA mode
7. **Real-time Streaming**: SSE for live progress updates
8. **Jira Integration**: Posts results, searches similar issues, attaches logs

### Efficiency Comparison

**Manual Process**: 15-30 min/failure, $0.10-0.20/job, full logs (800KB+)
**Automated Process**: Instant analysis, $0.01-0.02/job, focused logs (~100KB)

## API Endpoints

### Core Endpoints
- `POST /api/models` - Fetch available Groq models
- `POST /api/analyze` - Streaming analysis (SSE)
- `POST /api/analyze-async` - Async analysis with tracking
- `GET /api/status/<tracking_id>` - Check async analysis status

### Dashboard Endpoints
- `POST /api/run-daily-analysis` - Trigger automated analysis
- `GET /api/dashboard/latest` - Get latest analysis results
- `GET /api/dashboard/date-range` - Filter by date range
- `GET /api/dashboard/new-failures` - Identify new failures

### Jira Endpoints
- `GET /api/jira/<tracking_id>` - Get Jira-formatted results (ADF)
- `POST /api/post-to-jira` - Post analysis to Jira ticket

See code for detailed request/response schemas.
## Jira Integration

See [JIRA_INTEGRATION.md](JIRA_INTEGRATION.md) and [JIRA_DASHBOARD_SETUP.md](JIRA_DASHBOARD_SETUP.md) for detailed setup.

**Quick Setup:**
1. Get Jira Personal Access Token
2. Configure in analysis page or admin panel
3. Enable "Post results to Jira ticket"
4. Run analysis

## Troubleshooting

See [SETUP.md](SETUP.md) for common issues and solutions.

## License

MIT License

## Author

Chandravijay Agrawal ([@ScienceArtist](https://github.com/ScienceArtist))
