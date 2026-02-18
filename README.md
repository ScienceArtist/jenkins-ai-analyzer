# Jenkins Log Analyzer

<div align="center">

[![GitHub stars](https://img.shields.io/github/stars/ScienceArtist/jenkins-ai-analyzer?style=for-the-badge&logo=github&color=yellow)](https://github.com/ScienceArtist/jenkins-ai-analyzer/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/ScienceArtist/jenkins-ai-analyzer?style=for-the-badge&logo=github&color=blue)](https://github.com/ScienceArtist/jenkins-ai-analyzer/network/members)
[![GitHub issues](https://img.shields.io/github/issues/ScienceArtist/jenkins-ai-analyzer?style=for-the-badge&logo=github&color=red)](https://github.com/ScienceArtist/jenkins-ai-analyzer/issues)
[![License](https://img.shields.io/badge/license-MIT-green.svg?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.6+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org)

**⭐ If you find this project useful, please consider giving it a star! ⭐**

</div>

---

<p align="center">
  <strong>AI-powered Jenkins log analysis using Groq LLMs</strong><br>
  Automatically analyzes build failures, identifies root causes, and integrates with Jira
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-key-benefits">Key Benefits</a> •
  <a href="#-features">Features</a> •
  <a href="#-how-it-works">How It Works</a> •
  <a href="#-api-endpoints">API</a> •
  <a href="#-show-your-support">Support</a>
</p>

---

## 🚀 Quick Start

See **[SETUP.md](SETUP.md)** for installation and configuration.

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
python3 app.py
```

## 💎 Key Benefits

- **🤖 Automated Analysis**: Daily automated analysis of all Jenkins jobs
- **🎯 Smart Filtering**: Only analyzes failed/unstable jobs (80% reduction in volume)
- **⚡ Parallel Processing**: 10x faster with concurrent job analysis
- **🧠 Intelligent Extraction**: Extracts only relevant failure logs (90% size reduction)
- **💰 Cost Efficient**: ~$0.01-0.02 per job vs $0.10-0.20 manual analysis
- **🔗 Jira Integration**: Automatic ticket updates with analysis results

---

## ✨ Features

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

## 📦 Installation

See [SETUP.md](SETUP.md) for detailed setup instructions.

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
python3 app.py
```

Access at `http://localhost:5000`

## 📖 Usage

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

## 🔧 How It Works

1. **🔌 Jenkins Connection**: Connects using URL only (no Jenkins credentials needed)
2. **🎯 Smart Filtering**: Identifies failed/unstable jobs (80% volume reduction)
3. **🔍 Pipeline Detection**: Automatically detects and analyzes pipeline jobs
4. **🧠 Intelligent Extraction**:
   - Parses Robot Framework `output.xml` for failed tests
   - Extracts only relevant failure sections (90% size reduction)
   - Falls back to console logs if needed
5. **⚡ Parallel Processing**: Analyzes multiple jobs concurrently
6. **🤖 AI Analysis**: Sends focused logs to Groq API with RCA mode
7. **📡 Real-time Streaming**: SSE for live progress updates
8. **🔗 Jira Integration**: Posts results, searches similar issues, attaches logs

### 📊 Efficiency Comparison

| Metric | Manual Process | Automated Process | Savings |
|--------|---------------|-------------------|---------|
| **Time** | 15-30 min/failure | Instant | **95%** ⚡ |
| **Cost** | $0.10-0.20/job | $0.01-0.02/job | **90%** 💰 |
| **Log Size** | 800KB+ | ~100KB | **90%** 📉 |

## 🔌 API Endpoints

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

## 🔗 Jira Integration

See [JIRA_INTEGRATION.md](JIRA_INTEGRATION.md) and [JIRA_DASHBOARD_SETUP.md](JIRA_DASHBOARD_SETUP.md) for detailed setup.

**Quick Setup:**
1. Get Jira Personal Access Token
2. Configure in analysis page or admin panel
3. Enable "Post results to Jira ticket"
4. Run analysis

## 🐛 Troubleshooting

See [SETUP.md](SETUP.md) for common issues and solutions.

## ⭐ Show Your Support

If this project helped you save time analyzing Jenkins logs, please consider:

- ⭐ **Starring this repository** - it helps others discover this tool!
- 🐛 **Reporting bugs** - help us improve
- 💡 **Suggesting features** - we'd love to hear your ideas
- 🔀 **Contributing** - PRs are welcome!
- 📢 **Sharing** - tell your team about it

<div align="center">

### [⭐ Star this repo](https://github.com/ScienceArtist/jenkins-ai-analyzer) • [🐛 Report Bug](https://github.com/ScienceArtist/jenkins-ai-analyzer/issues) • [💡 Request Feature](https://github.com/ScienceArtist/jenkins-ai-analyzer/issues)

</div>

---

## 🎯 Why Star This Project?

- ✅ **Save 90% of log analysis time** - Smart extraction focuses only on failures
- ✅ **Reduce costs by 90%** - $0.01-0.02 per job vs $0.10-0.20 manual
- ✅ **Unique IP** - Robot Framework smart extraction not found elsewhere
- ✅ **Production-ready** - SQLite DB, dashboard, Jira integration, daily automation
- ✅ **Active development** - Regular updates and improvements
- ✅ **MIT Licensed** - Free to use, modify, and distribute

**Your star motivates us to keep improving this tool!** 🚀

---

## License

MIT License - see [LICENSE](LICENSE) file for details

## Author

**Chandravijay Agrawal** ([@ScienceArtist](https://github.com/ScienceArtist))

---

<div align="center">

**Made with ❤️ for DevOps and QA teams**

[![GitHub stars](https://img.shields.io/github/stars/ScienceArtist/jenkins-ai-analyzer?style=social)](https://github.com/ScienceArtist/jenkins-ai-analyzer/stargazers)
[![GitHub followers](https://img.shields.io/github/followers/ScienceArtist?style=social)](https://github.com/ScienceArtist)

</div>
