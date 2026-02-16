"""Jenkins Log Analyzer

Author: Chandravijay Agrawal (@ScienceArtist)
GitHub: https://github.com/ScienceArtist
Description: AI-powered Jenkins log analyzer with real-time streaming results.
"""

from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv
import urllib3
import json
import time
import uuid
from threading import Thread

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

app = Flask(__name__)
CORS(app)

# In-memory storage for async analysis tasks (for automation use cases).
# For production deployments, consider using a persistent store instead.
analysis_tasks = {}

# In-memory storage for dashboard results (stores last 30 days)
# Key: date (YYYY-MM-DD), Value: {pipelines: [...], timestamp: ...}
dashboard_results = {}

class JenkinsAnalyzer:
    def __init__(self, jenkins_url):
        self.original_url = jenkins_url.rstrip('/')
        self.jenkins_url = self._extract_base_url(jenkins_url).rstrip('/')
        self.specific_job = self._extract_job_name(jenkins_url)
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/json'})
        self.session.verify = False

    def _extract_base_url(self, url):
        url = url.rstrip('/')
        if '/job/' in url:
            parts = url.split('/job/')
            return parts[0]
        return url

    def _extract_job_name(self, url):
        """Extract job name from job-specific URL"""
        url = url.rstrip('/')
        if '/job/' in url:
            parts = url.split('/job/')
            if len(parts) > 1:
                # Get the job name (first part after /job/)
                job_name = parts[1].split('/')[0]
                return job_name
        return None

    def get_pipeline_jobs(self, job_name):
        """Get all jobs that are part of the same pipeline as the given job"""
        try:
            # Extract pipeline prefix (everything before the last dash-number pattern)
            parts = job_name.rsplit('-', 1)
            if len(parts) == 2:
                pipeline_prefix = parts[0]
            else:
                return [{'name': job_name}]

            # Get all jobs and filter by pipeline prefix
            all_jobs = self.get_jobs()
            pipeline_jobs = [j for j in all_jobs if j.get('name', '').startswith(pipeline_prefix)]

            if pipeline_jobs:
                return pipeline_jobs
            else:
                return [{'name': job_name}]
        except Exception as e:
            print(f"Error getting pipeline jobs: {e}")
            return [{'name': job_name}]
    
    def get_jobs(self):
        try:
            # Try to get jobs from Test-pipelines view first
            view_url = f"{self.jenkins_url}/view/Test-pipelines/api/json"
            response = self.session.get(view_url, timeout=30)

            if response.status_code == 200:
                data = response.json()
                return data.get('jobs', [])

            # Fallback to all jobs if view doesn't exist
            url = f"{self.jenkins_url}/api/json"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get('jobs', [])
        except Exception as e:
            print(f"Error fetching jobs: {e}")
            return []
    
    def get_job_logs(self, job_name, limit=1):
        """Fetch complete logs for a job's builds"""
        logs = []
        try:
            job_url = f"{self.jenkins_url}/job/{job_name}/api/json"
            response = self.session.get(job_url, timeout=30)
            response.raise_for_status()
            job_data = response.json()

            builds = job_data.get('builds', [])[:limit]

            for build in builds:
                build_number = build.get('number')
                if build_number:
                    console_url = f"{self.jenkins_url}/job/{job_name}/{build_number}/consoleText"
                    log_response = self.session.get(console_url, timeout=30)

                    if log_response.status_code == 200:
                        # Return complete log - we'll chunk it during analysis
                        logs.append({
                            'build': build_number,
                            'log': log_response.text,
                            'job_name': job_name
                        })
        except Exception as e:
            print(f"Error fetching logs for {job_name}: {e}")

        return logs
    
    def _get_fallback_models(self, primary_model):
        """Get ordered list of fallback models to try if primary fails"""
        # Ordered by preference: larger models first for better accuracy
        # Only active models (as of Feb 2026) - removed decommissioned models
        all_models = [
            'llama-3.3-70b-versatile',      # Best quality, primary choice
            'llama-3.3-70b-specdec',        # Speculative decoding variant
            'mixtral-8x7b-32768',           # Fast, efficient, good for long contexts
            'gemma2-9b-it',                 # Good balance of speed and quality
            'llama3-70b-8192',              # Older but stable
            'llama3-8b-8192',               # Smaller, faster
            'llama-3.1-8b-instant'          # Fastest, highest rate limits (last resort for accuracy)
        ]

        # Remove primary model and return it first, followed by others
        fallback_order = [primary_model]
        for model in all_models:
            if model != primary_model and model not in fallback_order:
                fallback_order.append(model)

        return fallback_order

    def _analyze_chunk_with_fallback(self, chunk, chunk_label, job_name, build_number, model, groq_api_key):
        """Analyze a single chunk with automatic model fallback on errors"""
        models_to_try = self._get_fallback_models(model)

        for attempt, current_model in enumerate(models_to_try):
            try:
                headers = {
                    'Authorization': f'Bearer {groq_api_key}',
                    'Content-Type': 'application/json'
                }

                # Enhanced prompt for better accuracy
                system_prompt = '''You are an expert Jenkins build log analyzer. Analyze logs with extreme accuracy.

CRITICAL RULES:
1. ALWAYS look for final build status (SUCCESS, FAILURE, UNSTABLE, ABORTED)
2. Count and list ALL failed tests/errors explicitly
3. Never say "build is running" if you see completion markers
4. Focus on FACTS from the log, not assumptions
5. If analyzing a partial section, acknowledge what you can/cannot determine'''

                user_prompt = f'''Analyze this {chunk_label.upper()} section of Jenkins build log:
Job: "{job_name}" Build #{build_number}

{chunk}

Provide:
1. **Build Status**: (if determinable from this section)
2. **Errors/Failures**: List specific errors with line context
3. **Warnings**: List any warnings found
4. **Key Insights**: Important observations
5. **Test Results**: Pass/fail counts if visible'''

                payload = {
                    'model': current_model,
                    'messages': [
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': user_prompt}
                    ],
                    'temperature': 0.3,  # Lower temperature for more factual responses
                    'max_tokens': 1500   # More tokens for detailed analysis
                }

                response = requests.post('https://api.groq.com/openai/v1/chat/completions', headers=headers, json=payload, timeout=60)

                if response.status_code == 200:
                    result = response.json()
                    model_note = f" (using {current_model})" if current_model != model else ""
                    return f"**{chunk_label.upper()} SECTION:**{model_note}\n{result['choices'][0]['message']['content']}"
                else:
                    # Check if it's a recoverable error (rate limit, decommissioned model, etc.)
                    error_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
                    error_code = error_data.get('error', {}).get('code', '')

                    # Errors that should trigger fallback to next model
                    recoverable_errors = ['rate_limit_exceeded', 'model_decommissioned', 'model_not_found']

                    if error_code in recoverable_errors and attempt < len(models_to_try) - 1:
                        next_model = models_to_try[attempt + 1]
                        print(f"{error_code} for {current_model}, trying fallback model {next_model}...")
                        time.sleep(1)  # Brief pause before trying next model
                        continue
                    else:
                        # Last model or non-recoverable error
                        return f"**{chunk_label.upper()} SECTION:** Error - {response.text}"

            except Exception as e:
                if attempt < len(models_to_try) - 1:
                    print(f"Error with {current_model}: {e}, trying fallback...")
                    time.sleep(1)
                    continue
                else:
                    return f"**{chunk_label.upper()} SECTION:** Error - {str(e)}"

        return f"**{chunk_label.upper()} SECTION:** All models failed"

    def analyze_log_chunked(self, log_text, job_name, build_number, model, groq_api_key):
        """Analyze a single log by chunking it if needed"""
        try:
            # Split log into chunks of ~4000 chars to stay within token limits
            chunk_size = 4000
            chunks = []

            if len(log_text) <= chunk_size:
                chunks = [log_text]
            else:
                # Get beginning, middle samples, and end
                chunks.append(log_text[:chunk_size])  # Beginning

                # Sample from middle
                mid_point = len(log_text) // 2
                chunks.append(log_text[mid_point:mid_point + chunk_size])

                # End (most important - contains results)
                chunks.append(log_text[-chunk_size:])

            # Analyze each chunk with automatic fallback
            analyses = []
            for i, chunk in enumerate(chunks):
                chunk_label = "beginning" if i == 0 else ("middle" if i == 1 else "end")
                analysis = self._analyze_chunk_with_fallback(chunk, chunk_label, job_name, build_number, model, groq_api_key)
                analyses.append(analysis)

                # Small delay to avoid rate limits
                time.sleep(0.5)

            return "\n\n".join(analyses)

        except Exception as e:
            print(f"Exception in analyze_log_chunked: {e}")
            return f"Error analyzing logs: {str(e)}"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/models', methods=['POST'])
def fetch_models():
    try:
        data = request.json
        groq_api_key = data.get('groq_api_key')

        if not groq_api_key:
            return jsonify({'error': 'Groq API key is required'}), 400

        headers = {'Authorization': f'Bearer {groq_api_key}', 'Content-Type': 'application/json'}
        response = requests.get('https://api.groq.com/openai/v1/models', headers=headers, timeout=30)
        response.raise_for_status()

        models_data = response.json()

        # Filter for chat-compatible models with good token limits
        # Prioritize models with higher TPM (tokens per minute) limits
        chat_models = []
        priority_models = []

        for m in models_data.get('data', []):
            model_id = m['id']
            # Exclude non-chat models
            if 'whisper' not in model_id.lower() and 'allam' not in model_id.lower():
                # Prioritize these models (known to have better limits)
                if any(x in model_id.lower() for x in ['llama-3.3-70b', 'llama-3.1-70b', 'mixtral-8x7b', 'gemma2-9b']):
                    priority_models.append({'id': model_id, 'name': f"⭐ {model_id}"})
                else:
                    chat_models.append({'id': model_id, 'name': model_id})

        # Return priority models first
        return jsonify({'models': priority_models + chat_models})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
def analyze():
    """Streaming analysis endpoint"""
    data = request.json
    jenkins_url = data.get('jenkins_url')
    groq_api_key = data.get('groq_api_key')
    model = data.get('model')

    if not all([jenkins_url, groq_api_key, model]):
        return jsonify({'error': 'Missing required fields'}), 400

    def generate():
        try:
            analyzer = JenkinsAnalyzer(jenkins_url)

            # Determine which jobs to analyze
            jobs_to_analyze = []

            if analyzer.specific_job:
                # Check if job is part of a pipeline
                yield f"data: {json.dumps({'type': 'status', 'message': f'Checking pipeline for: {analyzer.specific_job}'})}\n\n"
                pipeline_jobs = analyzer.get_pipeline_jobs(analyzer.specific_job)

                if len(pipeline_jobs) > 1:
                    # Multiple jobs in pipeline
                    pipeline_name = analyzer.specific_job.rsplit('-', 1)[0]
                    yield f"data: {json.dumps({'type': 'status', 'message': f'Found pipeline: {pipeline_name} with {len(pipeline_jobs)} jobs'})}\n\n"
                    yield f"data: {json.dumps({'type': 'pipeline_info', 'pipeline': pipeline_name, 'job_count': len(pipeline_jobs)})}\n\n"
                    jobs_to_analyze = pipeline_jobs
                else:
                    # Single job
                    yield f"data: {json.dumps({'type': 'status', 'message': f'Analyzing single job: {analyzer.specific_job}'})}\n\n"
                    jobs_to_analyze = [{'name': analyzer.specific_job}]
            else:
                # All jobs from base URL
                yield f"data: {json.dumps({'type': 'status', 'message': 'Fetching all jobs...'})}\n\n"
                all_jobs = analyzer.get_jobs()

                if not all_jobs:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'No jobs found'})}\n\n"
                    return

                jobs_to_analyze = all_jobs
                yield f"data: {json.dumps({'type': 'status', 'message': f'Found {len(jobs_to_analyze)} jobs. Starting analysis...'})}\n\n"

            # Analyze each job
            for idx, job in enumerate(jobs_to_analyze):
                job_name = job.get('name')
                if not job_name:
                    continue

                yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': len(jobs_to_analyze), 'job': job_name})}\n\n"

                # Fetch logs
                yield f"data: {json.dumps({'type': 'status', 'message': f'Fetching logs for {job_name}...'})}\n\n"
                logs = analyzer.get_job_logs(job_name, limit=1)

                if not logs:
                    yield f"data: {json.dumps({'type': 'job_result', 'job': job_name, 'build': 'N/A', 'analysis': 'No builds found'})}\n\n"
                    continue

                # Analyze each build
                for log_entry in logs:
                    build_number = log_entry['build']
                    log_text = log_entry['log']

                    yield f"data: {json.dumps({'type': 'status', 'message': f'Analyzing {job_name} build #{build_number}...'})}\n\n"

                    analysis = analyzer.analyze_log_chunked(log_text, job_name, build_number, model, groq_api_key)

                    # Send result
                    yield f"data: {json.dumps({'type': 'job_result', 'job': job_name, 'build': build_number, 'analysis': analysis, 'log_size': len(log_text)})}\n\n"

            yield f"data: {json.dumps({'type': 'complete', 'message': 'Analysis complete!'})}\n\n"

        except Exception as e:
            print(f"Error in analyze: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


def run_async_analysis(tracking_id, jenkins_url, groq_api_key, model):
	"""Background worker for async analysis API."""
	try:
	    task = analysis_tasks.get(tracking_id)
	    if not task:
	        return

	    task['status'] = 'running'
	    task['updated_at'] = time.time()

	    analyzer = JenkinsAnalyzer(jenkins_url)
	    jobs_to_analyze = []
	    pipeline_name = None
	    pipeline_job_count = None

	    if analyzer.specific_job:
	        pipeline_jobs = analyzer.get_pipeline_jobs(analyzer.specific_job)
	        if len(pipeline_jobs) > 1:
	            pipeline_name = analyzer.specific_job.rsplit('-', 1)[0]
	            pipeline_job_count = len(pipeline_jobs)
	            jobs_to_analyze = pipeline_jobs
	        else:
	            jobs_to_analyze = [{'name': analyzer.specific_job}]
	    else:
	        all_jobs = analyzer.get_jobs()
	        if not all_jobs:
	            task['status'] = 'error'
	            task['error'] = 'No jobs found'
	            task['updated_at'] = time.time()
	            return
	        jobs_to_analyze = all_jobs

	    results = []

	    for job in jobs_to_analyze:
	        job_name = job.get('name')
	        if not job_name:
	            continue

	        logs = analyzer.get_job_logs(job_name, limit=1)

	        if not logs:
	            results.append({
	                'job': job_name,
	                'build': 'N/A',
	                'analysis': 'No builds found',
	                'log_size': 0,
	            })
	            continue

	        for log_entry in logs:
	            build_number = log_entry['build']
	            log_text = log_entry['log']
	            analysis_text = analyzer.analyze_log_chunked(
	                log_text, job_name, build_number, model, groq_api_key
	            )
	            results.append({
	                'job': job_name,
	                'build': build_number,
	                'analysis': analysis_text,
	                'log_size': len(log_text),
	            })

	    task['status'] = 'complete'
	    task['results'] = results
	    if pipeline_name:
	        task['pipeline'] = {
	            'name': pipeline_name,
	            'job_count': pipeline_job_count,
	        }
	    task['updated_at'] = time.time()

	except Exception as e:
	    task = analysis_tasks.get(tracking_id, {})
	    task['status'] = 'error'
	    task['error'] = str(e)
	    task['updated_at'] = time.time()
	    analysis_tasks[tracking_id] = task


@app.route('/api/analyze-async', methods=['POST'])
def analyze_async():
	"""Start an asynchronous analysis and return tracking URLs for automation use."""
	data = request.json or {}

	jenkins_url = data.get('jenkins_url')
	groq_api_key = data.get('groq_api_key')
	model = data.get('model')

	if not all([jenkins_url, groq_api_key, model]):
	    return jsonify({'error': 'Missing required fields'}), 400

	tracking_id = str(uuid.uuid4())
	now = time.time()
	analysis_tasks[tracking_id] = {
	    'status': 'pending',
	    'created_at': now,
	    'updated_at': now,
	    'jenkins_url': jenkins_url,
	    'model': model,
	}

	worker = Thread(
	    target=run_async_analysis,
	    args=(tracking_id, jenkins_url, groq_api_key, model),
	    daemon=True,
	)
	worker.start()

	base_url = request.host_url.rstrip('/')

	return jsonify({
	    'tracking_id': tracking_id,
	    'status': 'pending',
	    'status_url': f'{base_url}/api/status/{tracking_id}',
	    'results_url': f'{base_url}/api/status/{tracking_id}',
	}), 202


@app.route('/api/status/<tracking_id>', methods=['GET'])
def analysis_status(tracking_id):
	"""Return the status and any results for an async analysis."""
	task = analysis_tasks.get(tracking_id)
	if not task:
	    return jsonify({'error': 'Invalid tracking_id'}), 404

	payload = {
	    'tracking_id': tracking_id,
	    'status': task.get('status'),
	    'created_at': task.get('created_at'),
	    'updated_at': task.get('updated_at'),
	    'pipeline': task.get('pipeline'),
	    'results': task.get('results', []),
	    'error': task.get('error'),
	}

	return jsonify(payload)


@app.route('/api/jira/<tracking_id>', methods=['GET'])
def jira_formatted_results(tracking_id):
	"""Return analysis results formatted for Jira comments (Atlassian Document Format)."""
	task = analysis_tasks.get(tracking_id)
	if not task:
	    return jsonify({'error': 'Invalid tracking_id'}), 404

	if task.get('status') != 'complete':
	    return jsonify({
	        'error': 'Analysis not complete',
	        'status': task.get('status'),
	        'tracking_id': tracking_id
	    }), 202

	# Build Atlassian Document Format (ADF) for Jira comments
	adf_content = []

	# Header
	adf_content.append({
	    "type": "heading",
	    "attrs": {"level": 2},
	    "content": [{"type": "text", "text": "🔍 Jenkins Log Analysis Results"}]
	})

	# Pipeline info if available
	if task.get('pipeline'):
	    pipeline = task['pipeline']
	    adf_content.append({
	        "type": "paragraph",
	        "content": [
	            {"type": "text", "text": "Pipeline: ", "marks": [{"type": "strong"}]},
	            {"type": "text", "text": f"{pipeline.get('name')} ({pipeline.get('job_count')} jobs)"}
	        ]
	    })

	# Results
	results = task.get('results', [])
	if results:
	    for result in results:
	        # Job header
	        adf_content.append({
	            "type": "heading",
	            "attrs": {"level": 3},
	            "content": [{"type": "text", "text": f"📦 {result.get('job')} - Build #{result.get('build')}"}]
	        })

	        # Analysis content
	        analysis_text = result.get('analysis', 'No analysis available')
	        adf_content.append({
	            "type": "codeBlock",
	            "attrs": {"language": "text"},
	            "content": [{"type": "text", "text": analysis_text}]
	        })

	        # Metadata
	        log_size_kb = result.get('log_size', 0) / 1024
	        adf_content.append({
	            "type": "paragraph",
	            "content": [
	                {"type": "text", "text": f"Log size: {log_size_kb:.1f} KB", "marks": [{"type": "em"}]}
	            ]
	        })

	# Footer with attribution
	adf_content.append({"type": "rule"})
	adf_content.append({
	    "type": "paragraph",
	    "content": [
	        {"type": "text", "text": "Analyzed by "},
	        {
	            "type": "text",
	            "text": "Jenkins Log Analyzer",
	            "marks": [{
	                "type": "link",
	                "attrs": {"href": request.host_url.rstrip('/')}
	            }]
	        },
	        {"type": "text", "text": " powered by Groq AI"}
	    ]
	})

	# Return ADF document
	adf_document = {
	    "version": 1,
	    "type": "doc",
	    "content": adf_content
	}

	return jsonify({
	    'tracking_id': tracking_id,
	    'status': 'complete',
	    'jira_adf': adf_document,
	    'plain_text_summary': f"Analyzed {len(results)} job(s)" + (f" in pipeline '{task.get('pipeline', {}).get('name')}'" if task.get('pipeline') else "")
	})


@app.route('/api/batch-analyze', methods=['POST'])
def batch_analyze():
	"""Analyze multiple pipelines and store results for dashboard viewing."""
	data = request.json or {}

	pipelines = data.get('pipelines', [])  # List of {name: str, jenkins_url: str}
	groq_api_key = data.get('groq_api_key')
	model = data.get('model', 'llama-3.3-70b-versatile')
	store_results = data.get('store_results', True)

	if not pipelines or not groq_api_key:
		return jsonify({'error': 'Missing pipelines or groq_api_key'}), 400

	batch_id = str(uuid.uuid4())
	timestamp = time.time()
	date_key = time.strftime('%Y-%m-%d', time.localtime(timestamp))

	results = {
		'batch_id': batch_id,
		'timestamp': timestamp,
		'date': date_key,
		'pipelines': []
	}

	# Analyze each pipeline
	for pipeline_config in pipelines:
		pipeline_name = pipeline_config.get('name', 'Unknown')
		jenkins_url = pipeline_config.get('jenkins_url')

		if not jenkins_url:
			results['pipelines'].append({
				'name': pipeline_name,
				'status': 'error',
				'error': 'Missing jenkins_url'
			})
			continue

		try:
			analyzer = JenkinsAnalyzer(jenkins_url)

			# Determine jobs to analyze
			jobs_to_analyze = []
			pipeline_info = None

			if analyzer.specific_job:
				pipeline_jobs = analyzer.get_pipeline_jobs(analyzer.specific_job)
				if pipeline_jobs:
					jobs_to_analyze = pipeline_jobs
					pipeline_info = {
						'name': analyzer.specific_job,
						'job_count': len(pipeline_jobs)
					}
				else:
					jobs_to_analyze = [{'name': analyzer.specific_job}]
			else:
				jobs_to_analyze = analyzer.get_jobs()[:5]  # Limit to 5 jobs

			# Analyze jobs
			job_results = []
			for job in jobs_to_analyze:
				job_name = job.get('name')
				if not job_name:
					continue

				logs = analyzer.get_job_logs(job_name, limit=1)
				if not logs:
					continue

				for log_entry in logs:
					build_number = log_entry['build']
					log_text = log_entry['log']
					analysis = analyzer.analyze_log_chunked(
						log_text, job_name, build_number, model, groq_api_key
					)

					job_results.append({
						'job': job_name,
						'build': build_number,
						'analysis': analysis,
						'log_size': len(log_text),
						'timestamp': time.time()
					})

			results['pipelines'].append({
				'name': pipeline_name,
				'jenkins_url': jenkins_url,
				'status': 'success',
				'pipeline_info': pipeline_info,
				'jobs': job_results,
				'job_count': len(job_results)
			})

		except Exception as e:
			results['pipelines'].append({
				'name': pipeline_name,
				'jenkins_url': jenkins_url,
				'status': 'error',
				'error': str(e)
			})

	# Store results for dashboard
	if store_results:
		dashboard_results[date_key] = results

		# Clean up old results (keep last 30 days)
		cutoff_time = timestamp - (30 * 24 * 60 * 60)
		keys_to_delete = [k for k, v in dashboard_results.items()
		                  if v.get('timestamp', 0) < cutoff_time]
		for k in keys_to_delete:
			del dashboard_results[k]

	return jsonify(results)


@app.route('/api/dashboard/results', methods=['GET'])
def get_dashboard_results():
	"""Get stored dashboard results for viewing."""
	date = request.args.get('date')  # Optional: YYYY-MM-DD

	if date:
		result = dashboard_results.get(date)
		if not result:
			return jsonify({'error': 'No results found for this date'}), 404
		return jsonify(result)

	# Return all results sorted by date (newest first)
	all_results = sorted(
		dashboard_results.values(),
		key=lambda x: x.get('timestamp', 0),
		reverse=True
	)

	return jsonify({
		'results': all_results,
		'count': len(all_results)
	})


@app.route('/api/dashboard/latest', methods=['GET'])
def get_latest_dashboard():
	"""Get the most recent dashboard results."""
	if not dashboard_results:
		return jsonify({'error': 'No results available'}), 404

	latest = max(dashboard_results.values(), key=lambda x: x.get('timestamp', 0))
	return jsonify(latest)


if __name__ == '__main__':
	app.run(debug=True, port=5000)
