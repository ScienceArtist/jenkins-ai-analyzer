# Jira Integration Guide

This guide shows you how to integrate Jenkins Log Analyzer with Jira dashboards and automation.

## Quick Start

### 1. Test the API

First, verify the API works:

```bash
# Start analysis
curl -X POST http://your-server:5000/api/analyze-async \
  -H "Content-Type: application/json" \
  -d '{
    "jenkins_url": "https://ci.jenkins.io/job/Infra/job/jenkins.io/",
    "groq_api_key": "YOUR_GROQ_API_KEY",
    "model": "llama-3.3-70b-versatile"
  }'

# Response:
# {
#   "tracking_id": "abc-123-def",
#   "status": "pending",
#   "status_url": "http://your-server:5000/api/status/abc-123-def"
# }

# Wait 30-60 seconds, then get Jira-formatted results
curl http://your-server:5000/api/jira/abc-123-def
```

## Integration Methods

### Method 1: Jira Automation (Recommended)

**Use Case:** Automatically analyze Jenkins logs when a bug is reported

#### Step 1: Create Custom Field
1. Go to Jira Settings → Issues → Custom Fields
2. Create new field: "Jenkins Build URL" (type: URL)
3. Add to relevant issue types (Bug, Task, etc.)

#### Step 2: Create Automation Rule
1. Go to Project Settings → Automation
2. Create new rule: "Analyze Jenkins Logs"
3. Configure:

**Trigger:**
- Issue created OR Issue updated

**Condition:**
- Field "Jenkins Build URL" is not empty

**Action 1: Send Web Request**
```
URL: http://your-server:5000/api/analyze-async
Method: POST
Headers: Content-Type: application/json
Body (Custom data):
{
  "jenkins_url": "{{issue.customfield_10XXX}}",
  "groq_api_key": "{{lookupSecrets.groqApiKey}}",
  "model": "llama-3.3-70b-versatile"
}

Store response in: {{webhookResponse}}
```

**Action 2: Wait**
- Wait for: 45 seconds

**Action 3: Send Web Request**
```
URL: http://your-server:5000/api/jira/{{webhookResponse.body.tracking_id}}
Method: GET

Store response in: {{resultsResponse}}
```

**Action 4: Add Comment**
```
Comment body: {{resultsResponse.body.jira_adf}}
```

#### Step 3: Store API Key Securely
1. Go to Jira Settings → System → Secrets
2. Add secret: `groqApiKey` = YOUR_GROQ_API_KEY
3. Reference in automation as `{{lookupSecrets.groqApiKey}}`

---

### Method 2: Embed in Jira Dashboard

**Use Case:** Dedicated dashboard for Jenkins log analysis

#### Option A: iFrame Gadget (Requires Admin)
1. Install "iFrame Gadget" from Atlassian Marketplace
2. Add to dashboard
3. Configure URL:
```
http://your-server:5000/
```

#### Option B: Web Panel Link
1. Create dashboard
2. Add "Web Panel" gadget
3. Add link:
```
http://your-server:5000/?groq_api_key=KEY&model=llama-3.3-70b-versatile&jenkins_url=https://ci.jenkins.io
```

---

### Method 3: ScriptRunner (Advanced)

**Use Case:** Custom button in Jira issue view

```groovy
import com.atlassian.jira.component.ComponentAccessor
import groovyx.net.http.RESTClient
import groovy.json.JsonSlurper

def issueManager = ComponentAccessor.getIssueManager()
def customFieldManager = ComponentAccessor.getCustomFieldManager()

// Get Jenkins URL from custom field
def jenkinsUrlField = customFieldManager.getCustomFieldObjectByName("Jenkins Build URL")
def jenkinsUrl = issue.getCustomFieldValue(jenkinsUrlField)

if (!jenkinsUrl) {
    return "No Jenkins URL found"
}

// Start async analysis
def client = new RESTClient('http://your-server:5000')
def response = client.post(
    path: '/api/analyze-async',
    body: [
        jenkins_url: jenkinsUrl,
        groq_api_key: 'YOUR_GROQ_API_KEY',
        model: 'llama-3.3-70b-versatile'
    ],
    requestContentType: 'application/json'
)

def trackingId = response.data.tracking_id

// Wait for completion (polling)
sleep(45000) // 45 seconds

// Get Jira-formatted results
def resultsResponse = client.get(path: "/api/jira/${trackingId}")

if (resultsResponse.data.status == 'complete') {
    // Add comment with ADF
    def commentManager = ComponentAccessor.getCommentManager()
    commentManager.create(
        issue,
        ComponentAccessor.getJiraAuthenticationContext().getLoggedInUser(),
        resultsResponse.data.jira_adf,
        true
    )
    
    return "Analysis complete! Comment added."
} else {
    return "Analysis still running. Check: ${response.data.status_url}"
}
```

---

## API Response Format

### `/api/jira/<tracking_id>` Response

The Jira endpoint returns Atlassian Document Format (ADF) ready for Jira comments:

```json
{
  "tracking_id": "abc-123",
  "status": "complete",
  "jira_adf": {
    "version": 1,
    "type": "doc",
    "content": [
      {
        "type": "heading",
        "attrs": {"level": 2},
        "content": [{"type": "text", "text": "🔍 Jenkins Log Analysis Results"}]
      },
      {
        "type": "heading",
        "attrs": {"level": 3},
        "content": [{"type": "text", "text": "📦 JobName - Build #123"}]
      },
      {
        "type": "codeBlock",
        "attrs": {"language": "text"},
        "content": [{"type": "text", "text": "Analysis results here..."}]
      }
    ]
  },
  "plain_text_summary": "Analyzed 3 job(s) in pipeline 'MyPipeline'"
}
```

Use `jira_adf` directly in Jira API comment body.

---

## Security Best Practices

1. **Never hardcode API keys** - Use Jira Secrets or environment variables
2. **Enable Groq ZDR** - Go to https://console.groq.com/settings/data-controls
3. **Restrict network access** - Use firewall rules to limit who can call the API
4. **Use HTTPS** - Deploy behind nginx/Apache with SSL certificate
5. **Rate limiting** - Consider adding rate limits to prevent abuse

---

## Troubleshooting

### "Analysis not complete" error
- Wait longer (60+ seconds for large logs)
- Poll `/api/status/<tracking_id>` to check progress

### Jira comment not appearing
- Verify ADF format is correct
- Check Jira automation logs for errors
- Test with `/api/status/<tracking_id>` first (returns plain JSON)

### Connection refused
- Verify Flask server is running
- Check firewall rules
- Ensure Jira can reach your server (network connectivity)

---

## Example: Complete Workflow

1. Developer creates Jira bug: `BUG-123`
2. Adds Jenkins build URL: `https://ci.jenkins.io/job/MyJob/456/`
3. Jira automation triggers:
   - Calls `/api/analyze-async`
   - Gets tracking ID: `xyz-789`
   - Waits 45 seconds
   - Calls `/api/jira/xyz-789`
   - Adds formatted comment to BUG-123
4. Team sees AI analysis directly in Jira issue! 🎉

---

**Need help?** Check the main [README.md](README.md) or open an issue.

