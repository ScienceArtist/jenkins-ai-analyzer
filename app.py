"""Jenkins Log Analyzer

Author: Chandravijay Agrawal (@ScienceArtist)
GitHub: https://github.com/ScienceArtist
Description: AI-powered Jenkins log analyzer with real-time streaming results.
"""

from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect, url_for
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv
import urllib3
import json
import time
import uuid
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from datetime import datetime, timedelta
import sqlite3
from functools import wraps
import hashlib

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

# Admin password (hashed) - set via environment variable
ADMIN_PASSWORD_HASH = os.getenv('ADMIN_PASSWORD_HASH', hashlib.sha256('admin123'.encode()).hexdigest())

# In-memory storage for async analysis tasks (for automation use cases).
# For production deployments, consider using a persistent store instead.
analysis_tasks = {}

# In-memory storage for dashboard results (stores last 30 days)
# Key: date (YYYY-MM-DD), Value: {pipelines: [...], timestamp: ...}
dashboard_results = {}

# Database lock for thread-safe operations
db_lock = Lock()

# Database file path
DB_PATH = os.getenv('DB_PATH', 'jenkins_analysis.db')

# ============================================================================
# DATABASE LAYER
# ============================================================================

def init_database():
	"""Initialize SQLite database with schema for storing analysis results"""
	with db_lock:
		conn = sqlite3.connect(DB_PATH)
		cursor = conn.cursor()

		# Table for storing job analysis results
		cursor.execute('''
			CREATE TABLE IF NOT EXISTS job_analyses (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				job_name TEXT NOT NULL,
				build_number INTEGER NOT NULL,
				jenkins_url TEXT NOT NULL,
				analysis_text TEXT,
				status TEXT,
				log_type TEXT,
				log_size INTEGER,
				passed_tests INTEGER DEFAULT 0,
				failed_tests INTEGER DEFAULT 0,
				total_tests INTEGER DEFAULT 0,
				model_used TEXT,
				analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
				date_key TEXT,
				UNIQUE(job_name, build_number, jenkins_url, date_key)
			)
		''')

		# Table for tracking daily analysis runs
		cursor.execute('''
			CREATE TABLE IF NOT EXISTS daily_runs (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				run_date TEXT UNIQUE NOT NULL,
				jenkins_url TEXT NOT NULL,
				total_jobs INTEGER DEFAULT 0,
				analyzed_jobs INTEGER DEFAULT 0,
				passed_jobs INTEGER DEFAULT 0,
				failed_jobs INTEGER DEFAULT 0,
				status TEXT,
				started_at TIMESTAMP,
				completed_at TIMESTAMP,
				model_used TEXT
			)
		''')

		# Index for faster queries
		cursor.execute('CREATE INDEX IF NOT EXISTS idx_job_date ON job_analyses(date_key, job_name)')
		cursor.execute('CREATE INDEX IF NOT EXISTS idx_analyzed_at ON job_analyses(analyzed_at DESC)')
		cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON job_analyses(status)')

		conn.commit()
		conn.close()
		print("✅ Database initialized successfully")

def save_job_analysis(job_name, build_number, jenkins_url, analysis_text, status,
                      log_type='console', log_size=0, passed_tests=0, failed_tests=0,
                      total_tests=0, model_used='', date_key=None):
	"""Save a job analysis result to the database"""
	if not date_key:
		date_key = datetime.now().strftime('%Y-%m-%d')

	with db_lock:
		conn = sqlite3.connect(DB_PATH)
		cursor = conn.cursor()

		try:
			cursor.execute('''
				INSERT OR REPLACE INTO job_analyses
				(job_name, build_number, jenkins_url, analysis_text, status, log_type,
				 log_size, passed_tests, failed_tests, total_tests, model_used, date_key)
				VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			''', (job_name, build_number, jenkins_url, analysis_text, status, log_type,
			      log_size, passed_tests, failed_tests, total_tests, model_used, date_key))

			conn.commit()
			return True
		except Exception as e:
			print(f"Error saving job analysis: {e}")
			return False
		finally:
			conn.close()

def is_build_already_analyzed(job_name, build_number, jenkins_url):
	"""Check if a specific job build was already analyzed"""
	with db_lock:
		conn = sqlite3.connect(DB_PATH)
		cursor = conn.cursor()
		try:
			cursor.execute('''
				SELECT COUNT(*) FROM job_analyses
				WHERE job_name = ? AND build_number = ? AND jenkins_url = ?
			''', (job_name, str(build_number), jenkins_url))
			count = cursor.fetchone()[0]
			return count > 0
		except Exception as e:
			print(f"Error checking if build was analyzed: {e}")
			return False
		finally:
			conn.close()

def get_analyses_by_date_range(start_date, end_date, jenkins_url=None):
	"""Get all analyses within a date range"""
	with db_lock:
		conn = sqlite3.connect(DB_PATH)
		conn.row_factory = sqlite3.Row
		cursor = conn.cursor()

		if jenkins_url:
			cursor.execute('''
				SELECT * FROM job_analyses
				WHERE date_key BETWEEN ? AND ? AND jenkins_url = ?
				ORDER BY analyzed_at DESC
			''', (start_date, end_date, jenkins_url))
		else:
			cursor.execute('''
				SELECT * FROM job_analyses
				WHERE date_key BETWEEN ? AND ?
				ORDER BY analyzed_at DESC
			''', (start_date, end_date))

		rows = cursor.fetchall()
		conn.close()

		return [dict(row) for row in rows]

def get_new_failures_since(days_ago, jenkins_url=None):
	"""Get jobs that failed in the last N days but were passing before"""
	cutoff_date = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')

	with db_lock:
		conn = sqlite3.connect(DB_PATH)
		conn.row_factory = sqlite3.Row
		cursor = conn.cursor()

		# Get recent failures
		if jenkins_url:
			cursor.execute('''
				SELECT DISTINCT job_name, MAX(analyzed_at) as last_analyzed
				FROM job_analyses
				WHERE date_key >= ? AND status IN ('success', 'failure')
				      AND failed_tests > 0 AND jenkins_url = ?
				GROUP BY job_name
			''', (cutoff_date, jenkins_url))
		else:
			cursor.execute('''
				SELECT DISTINCT job_name, MAX(analyzed_at) as last_analyzed
				FROM job_analyses
				WHERE date_key >= ? AND status IN ('success', 'failure')
				      AND failed_tests > 0
				GROUP BY job_name
			''', (cutoff_date,))

		rows = cursor.fetchall()
		conn.close()

		return [dict(row) for row in rows]

def get_latest_analyses(limit=50, jenkins_url=None):
	"""Get the most recent analyses"""
	with db_lock:
		conn = sqlite3.connect(DB_PATH)
		conn.row_factory = sqlite3.Row
		cursor = conn.cursor()

		if jenkins_url:
			cursor.execute('''
				SELECT * FROM job_analyses
				WHERE jenkins_url = ?
				ORDER BY analyzed_at DESC
				LIMIT ?
			''', (jenkins_url, limit))
		else:
			cursor.execute('''
				SELECT * FROM job_analyses
				ORDER BY analyzed_at DESC
				LIMIT ?
			''', (limit,))

		rows = cursor.fetchall()
		conn.close()

		return [dict(row) for row in rows]

# Initialize database on startup
init_database()

# ============================================================================
# AUTHENTICATION
# ============================================================================

def login_required(f):
	"""Decorator to require login for admin routes"""
	@wraps(f)
	def decorated_function(*args, **kwargs):
		if not session.get('logged_in'):
			return redirect(url_for('admin_index'))
		return f(*args, **kwargs)
	return decorated_function

# ============================================================================
# JENKINS ANALYZER CLASS
# ============================================================================

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
    
    def get_jobs(self, filter_failed=False):
        """Get jobs, optionally filtering for failed/unstable builds only"""
        try:
            # Try to get jobs from Test-pipelines view first
            view_url = f"{self.jenkins_url}/view/Test-pipelines/api/json?tree=jobs[name,url,color,lastBuild[number,result]]"
            response = self.session.get(view_url, timeout=30)

            if response.status_code == 200:
                data = response.json()
                jobs = data.get('jobs', [])
            else:
                # Fallback to all jobs if view doesn't exist
                url = f"{self.jenkins_url}/api/json?tree=jobs[name,url,color,lastBuild[number,result]]"
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                data = response.json()
                jobs = data.get('jobs', [])

            if filter_failed:
                # Filter for failed/unstable builds only
                # color: red (failed), yellow (unstable), blue (success), grey (not built)
                failed_jobs = []
                for job in jobs:
                    color = job.get('color', '')
                    last_build = job.get('lastBuild', {})
                    result = last_build.get('result', '') if last_build else ''

                    # Include if: red/yellow color OR result is FAILURE/UNSTABLE
                    if (color in ['red', 'yellow', 'red_anime', 'yellow_anime'] or
                        result in ['FAILURE', 'UNSTABLE']):
                        failed_jobs.append(job)

                return failed_jobs

            return jobs
        except Exception as e:
            print(f"Error fetching jobs: {e}")
            return []
    
    def get_job_logs(self, job_name, limit=1):
        """Fetch complete logs for a job's builds - prioritize Robot Framework logs"""
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
                    # First, try to get Robot Framework logs (much better for analysis)
                    robot_log = self._get_robot_framework_log(job_name, build_number)

                    if robot_log and robot_log.startswith('PASSED|||'):
                        # All tests passed - return special marker
                        parts = robot_log.split('|||')
                        passed_count = parts[1] if len(parts) > 1 else '0'
                        total_count = parts[2] if len(parts) > 2 else '0'
                        logs.append({
                            'build': build_number,
                            'log': robot_log,
                            'job_name': job_name,
                            'log_type': 'passed',
                            'passed_tests': int(passed_count),
                            'total_tests': int(total_count)
                        })
                    elif robot_log:
                        # Robot logs with failures - use them!
                        logs.append({
                            'build': build_number,
                            'log': robot_log,
                            'job_name': job_name,
                            'log_type': 'robot'
                        })
                    else:
                        # Fall back to console logs (no Robot Framework artifacts found)
                        console_url = f"{self.jenkins_url}/job/{job_name}/{build_number}/consoleText"
                        log_response = self.session.get(console_url, timeout=30)

                        if log_response.status_code == 200:
                            # Return complete log - we'll chunk it during analysis
                            logs.append({
                                'build': build_number,
                                'log': log_response.text,
                                'job_name': job_name,
                                'log_type': 'console'
                            })
        except Exception as e:
            print(f"Error fetching logs for {job_name}: {e}")

        return logs

    def _get_robot_framework_log(self, job_name, build_number):
        """
        Intelligent Robot Framework log extraction:
        1. Parse output.xml to find failed test names
        2. Extract ONLY those specific test logs from DEBUG.log and console
        3. Reduces log size by ~90% - only analyze what failed!

        Returns:
            - String with extracted logs if there are failed tests
            - "PASSED" if all tests passed (special marker)
            - None if Robot Framework artifacts not found
        """
        try:
            # Step 1: Get output.xml to find failed test names
            # Try multiple possible locations for output.xml (most common first)
            possible_urls = [
                f"{self.jenkins_url}/job/{job_name}/{build_number}/robot/report/output.xml",  # Most common
                f"{self.jenkins_url}/job/{job_name}/{build_number}/robot/output.xml",
                f"{self.jenkins_url}/job/{job_name}/{build_number}/artifact/output.xml",
            ]

            response = None
            robot_xml_url = None

            for url in possible_urls:
                try:
                    resp = self.session.get(url, timeout=30)
                    if resp.status_code == 200:
                        response = resp
                        robot_xml_url = url
                        print(f"Found Robot output.xml at: {url}")
                        break
                except:
                    continue

            if not response or response.status_code != 200:
                print(f"Robot output.xml not found for {job_name}/{build_number} - tried {len(possible_urls)} locations")
                return None

            # Parse XML to extract failed test names and test statistics
            import xml.etree.ElementTree as ET
            root = ET.fromstring(response.text)

            failed_test_names = []
            failed_test_details = []
            total_tests = 0
            passed_tests = 0

            # Find all tests and count pass/fail
            for test in root.findall('.//test'):
                total_tests += 1
                status = test.find('status')
                if status is not None:
                    if status.get('status') == 'FAIL':
                        test_name = test.get('name', 'Unknown')
                        msg = status.text or 'No message'
                        failed_test_names.append(test_name)
                        failed_test_details.append({
                            'name': test_name,
                            'message': msg
                        })
                    elif status.get('status') == 'PASS':
                        passed_tests += 1

            if not failed_test_names:
                print(f"✅ All tests passed for {job_name}/{build_number} ({passed_tests}/{total_tests} tests passed) - skipping analysis")
                # Return special marker with test statistics
                return f"PASSED|||{passed_tests}|||{total_tests}"

            print(f"Found {len(failed_test_names)} failed tests out of {total_tests} total tests: {failed_test_names}")

            # Step 2: Extract specific test logs from DEBUG.log and console
            extracted_logs = []
            extracted_logs.append("=== ROBOT FRAMEWORK ANALYSIS - FAILED TESTS ONLY ===\n")
            extracted_logs.append(f"Test Statistics: {passed_tests} passed, {len(failed_test_names)} failed, {total_tests} total\n")
            extracted_logs.append(f"Failed tests: {len(failed_test_names)}\n")

            # Add failed test summary
            for detail in failed_test_details:
                extracted_logs.append(f"\n--- FAILED TEST: {detail['name']} ---")
                extracted_logs.append(f"Error: {detail['message']}\n")

            # Step 3: Try to get DEBUG.log and extract only failed test sections
            # First, try to discover DEBUG.log location from Jenkins artifacts API
            debug_log_urls = []

            try:
                artifacts_url = f"{self.jenkins_url}/job/{job_name}/{build_number}/api/json?tree=artifacts[relativePath]"
                artifacts_response = self.session.get(artifacts_url, timeout=10)
                if artifacts_response.status_code == 200:
                    artifacts_data = artifacts_response.json()
                    artifacts = artifacts_data.get('artifacts', [])

                    # Look for DEBUG.log in artifacts
                    for artifact in artifacts:
                        rel_path = artifact.get('relativePath', '')
                        if 'DEBUG.log' in rel_path:
                            debug_log_urls.append(f"{self.jenkins_url}/job/{job_name}/{build_number}/artifact/{rel_path}")
                            print(f"Discovered DEBUG.log at: artifact/{rel_path}")
                            break
            except Exception as e:
                print(f"Could not discover artifacts via API: {e}")

            # Add standard fallback locations if not discovered
            if not debug_log_urls:
                debug_log_urls.extend([
                    f"{self.jenkins_url}/job/{job_name}/{build_number}/robot/log/DEBUG.log",
                    f"{self.jenkins_url}/job/{job_name}/{build_number}/robot/DEBUG.log",
                    f"{self.jenkins_url}/job/{job_name}/{build_number}/artifact/DEBUG.log",
                    f"{self.jenkins_url}/job/{job_name}/{build_number}/artifact/logs/DEBUG.log",
                ])

            debug_log = None
            for url in debug_log_urls:
                try:
                    debug_response = self.session.get(url, timeout=30)
                    if debug_response.status_code == 200:
                        debug_log = debug_response.text
                        print(f"Found DEBUG.log at: {url}")
                        break
                except:
                    continue

            if debug_log:
                extracted_logs.append("\n=== EXTRACTED FROM DEBUG.LOG (FAILED TESTS ONLY) ===\n")

                # Extract logs for each failed test
                for test_name in failed_test_names[:5]:  # Limit to first 5 failed tests
                    test_section = self._extract_test_section_from_log(debug_log, test_name)
                    if test_section:
                        extracted_logs.append(f"\n--- DEBUG LOG FOR: {test_name} ---")
                        extracted_logs.append(test_section)
            else:
                print(f"DEBUG.log not found - tried {len(debug_log_urls)} locations")

            # Step 4: Try to get console log and extract only failed test sections
            console_url = f"{self.jenkins_url}/job/{job_name}/{build_number}/consoleText"
            console_response = self.session.get(console_url, timeout=30)

            if console_response.status_code == 200:
                console_log = console_response.text
                extracted_logs.append("\n=== EXTRACTED FROM CONSOLE LOG (FAILED TESTS ONLY) ===\n")

                # Extract logs for each failed test
                for test_name in failed_test_names[:5]:  # Limit to first 5 failed tests
                    test_section = self._extract_test_section_from_log(console_log, test_name)
                    if test_section:
                        extracted_logs.append(f"\n--- CONSOLE LOG FOR: {test_name} ---")
                        extracted_logs.append(test_section)

            result = '\n'.join(extracted_logs)

            # Only return if we got meaningful content
            if len(result) > 200:
                print(f"Extracted {len(result)} chars of focused logs (vs full log size)")
                return result

        except Exception as e:
            print(f"Error extracting Robot Framework logs: {e}")
            import traceback
            traceback.print_exc()

        return None

    def _extract_test_section_from_log(self, log_content, test_name):
        """
        Extract a specific test's log section from start to finish.
        Uses pattern matching to find test boundaries.
        """
        try:
            lines = log_content.split('\n')
            test_lines = []
            in_test = False
            test_started = False

            # Common patterns that indicate test start/end
            # Robot Framework typically logs test names in specific formats
            test_name_clean = test_name.strip()

            for i, line in enumerate(lines):
                # Check if this line starts the test
                if test_name_clean in line and not test_started:
                    # Found the test - start capturing
                    in_test = True
                    test_started = True
                    # Include some context before (5 lines)
                    start_idx = max(0, i - 5)
                    test_lines.extend(lines[start_idx:i+1])
                    continue

                if in_test:
                    test_lines.append(line)

                    # Check if test ended (common end patterns)
                    # Robot Framework logs often have clear test boundaries
                    if any(pattern in line for pattern in [
                        'FAIL', 'PASS', 'Test execution ended',
                        '=' * 20,  # Separator lines
                        'Ending test:',
                        '| FAIL |', '| PASS |'
                    ]):
                        # Include some context after (10 lines for error details)
                        end_idx = min(len(lines), i + 10)
                        test_lines.extend(lines[i+1:end_idx])
                        break

                    # Safety limit - don't extract more than 500 lines per test
                    if len(test_lines) > 500:
                        test_lines.append("\n... (truncated - test log too long) ...")
                        break

            if test_lines:
                return '\n'.join(test_lines)

        except Exception as e:
            print(f"Error extracting test section for {test_name}: {e}")

        return None
    
    def _get_fallback_models(self, primary_model, enable_fallback=True):
        """Get ordered list of fallback models to try if primary fails"""
        # If fallback is disabled, only return the primary model
        if not enable_fallback:
            return [primary_model]

        # Ordered by preference: larger models first for better accuracy
        # Only active models (as of Feb 2026) - removed decommissioned models
        all_models = [
            'gpt-oss-120b',                 # Best quality, largest open-source model
            'llama-3.3-70b-versatile',      # Excellent quality, primary choice
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

    def _analyze_chunk_with_fallback(self, chunk, chunk_label, job_name, build_number, model, groq_api_key, rca_mode=False, enable_fallback=True, max_retries=3):
        """Analyze a single chunk with automatic model fallback on errors and rate limit retry

        Args:
            max_retries: Maximum number of retry attempts for rate limits (default: 3)
        """
        models_to_try = self._get_fallback_models(model, enable_fallback)

        for attempt, current_model in enumerate(models_to_try):
            # Retry loop for rate limits
            retry_count = 0
            while retry_count <= max_retries:
                try:
                    headers = {
                        'Authorization': f'Bearer {groq_api_key}',
                        'Content-Type': 'application/json'
                    }

                    if rca_mode:
                        # RCA-focused prompt
                        system_prompt = '''You are an expert QA engineer performing Root Cause Analysis (RCA) on Jenkins build failures.

CRITICAL RULES FOR ACCURACY:
1. ONLY state facts you can verify from the logs - NEVER guess or assume
2. If uncertain about root cause, explicitly say "Unable to determine from available logs"
3. Distinguish clearly between symptoms and actual root causes
4. When suggesting actions, be honest if you're not 100% certain
5. If logs are incomplete or unclear, state what additional information is needed

ANALYSIS FOCUS:
1. Identify the PRIMARY root cause of the failure
2. List ALL failed tests/errors with exact names and error messages
3. Trace the failure chain (what failed first, what failed as a consequence)
4. Suggest specific actionable steps: "Retest", "Create bug", "Check config", "Manual investigation needed", etc.
5. Focus on actionable insights for QA team, not generic observations'''

                        user_prompt = f'''Perform Root Cause Analysis on this failure section from Jenkins build:
Job: "{job_name}" Build #{build_number}
Section: {chunk_label}

{chunk}

Provide (BE HONEST - say "unclear" or "unable to determine" if you cannot be certain):
1. **Root Cause**: Primary reason for failure (or state if unclear from logs)
2. **Failed Tests/Errors**: Complete list with exact error messages
3. **Failure Chain**: Sequence of events leading to failure
4. **Recommended Actions**: Specific steps (e.g., "Retest - transient issue", "Create bug - code defect", "Manual investigation needed - unclear from logs")
5. **Confidence Level**: State if you're certain, somewhat certain, or uncertain about the analysis
6. **Related Issues**: Any warnings or secondary problems'''
                    else:
                        # Standard analysis prompt
                        system_prompt = '''You are an expert Jenkins build log analyzer for QA teams. Analyze logs with EXTREME ACCURACY.

CRITICAL RULES FOR ACCURACY:
1. ONLY state facts you can verify from the logs - NEVER guess or assume
2. If uncertain, explicitly say "Unable to determine" or "Unclear from this section"
3. ALWAYS look for final build status (SUCCESS, FAILURE, UNSTABLE, ABORTED)
4. Count and list ALL failed tests/errors explicitly
5. Never say "build is running" if you see completion markers
6. Focus on FACTS from the log, not assumptions
7. If analyzing a partial section, clearly state what you can/cannot determine
8. When in doubt, recommend manual investigation rather than making uncertain claims'''

                        user_prompt = f'''Analyze this {chunk_label.upper()} section of Jenkins build log:
Job: "{job_name}" Build #{build_number}

{chunk}

Provide (BE HONEST - say "unclear" or "not visible in this section" if uncertain):
1. **Build Status**: (if determinable from this section, otherwise state "Not visible in this section")
2. **Errors/Failures**: List specific errors with line context (or "None found in this section")
3. **Warnings**: List any warnings found (or "None found")
4. **Key Insights**: Important observations based on facts
5. **Test Results**: Pass/fail counts if visible (or "Not visible in this section")
6. **Confidence**: State if this section provides complete or partial information'''

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

                        # Special handling for rate limits - automatic retry with backoff
                        if error_code == 'rate_limit_exceeded':
                            error_message = error_data.get('error', {}).get('message', '')
                            # Try to extract wait time from error message or headers
                            wait_seconds = None

                            # Check X-RateLimit-Reset header (Unix timestamp)
                            if 'X-RateLimit-Reset' in response.headers:
                                try:
                                    reset_timestamp = int(response.headers['X-RateLimit-Reset'])
                                    reset_time = datetime.fromtimestamp(reset_timestamp)
                                    wait_seconds = (reset_time - datetime.now()).total_seconds()
                                except:
                                    pass

                            # If no header, try to parse from error message
                            if not wait_seconds:
                                import re
                                # Look for patterns like "try again in 5m30s" or "retry after 330 seconds"
                                time_match = re.search(r'try again in (\d+)m(\d+(?:\.\d+)?)s|retry after (\d+)\s*seconds?', error_message)
                                if time_match:
                                    if time_match.group(1):  # Format: 5m30s
                                        minutes = int(time_match.group(1))
                                        seconds = float(time_match.group(2))
                                        wait_seconds = minutes * 60 + seconds
                                    else:  # Format: 330 seconds
                                        wait_seconds = float(time_match.group(3))

                            # If we can retry, wait and try again
                            if retry_count < max_retries and wait_seconds:
                                # Add a small buffer (5 seconds) to ensure rate limit has reset
                                wait_seconds = wait_seconds + 5

                                minutes = int(wait_seconds // 60)
                                seconds = int(wait_seconds % 60)
                                wait_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

                                print(f"⏳ Rate limit hit for {current_model}. Waiting {wait_str} before retry {retry_count + 1}/{max_retries}...")
                                print(f"   Error: {error_message}")

                                time.sleep(wait_seconds)
                                retry_count += 1
                                continue  # Retry the same request
                            else:
                                # Max retries reached or can't determine wait time
                                if retry_count >= max_retries:
                                    error_msg = f"RATE_LIMIT_STOP|||Max retries ({max_retries}) reached|||{error_message}"
                                else:
                                    error_msg = f"RATE_LIMIT_STOP|||Cannot determine wait time|||{error_message}"

                                return f"**{chunk_label.upper()} SECTION:** {error_msg}"

                        # Errors that should trigger fallback to next model (excluding rate limits)
                        recoverable_errors = ['model_decommissioned', 'model_not_found']

                        if error_code in recoverable_errors and attempt < len(models_to_try) - 1:
                            next_model = models_to_try[attempt + 1]
                            print(f"{error_code} for {current_model}, trying fallback model {next_model}...")
                            time.sleep(1)  # Brief pause before trying next model
                            break  # Break retry loop to try next model
                        else:
                            # Last model or non-recoverable error
                            return f"**{chunk_label.upper()} SECTION:** Error - {response.text}"

                except Exception as e:
                    if retry_count < max_retries:
                        # Retry on network errors with exponential backoff
                        wait_time = (2 ** retry_count) * 2  # 2s, 4s, 8s
                        print(f"⚠️  Network error with {current_model}: {e}. Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        retry_count += 1
                        continue
                    elif attempt < len(models_to_try) - 1:
                        print(f"Error with {current_model}: {e}, trying fallback...")
                        time.sleep(1)
                        break  # Break retry loop to try next model
                    else:
                        return f"**{chunk_label.upper()} SECTION:** Error - {str(e)}"

                # If we successfully processed or need to move to next model, break retry loop
                break

        return f"**{chunk_label.upper()} SECTION:** All models failed"

    def _extract_failure_sections(self, log_text):
        """Extract sections containing failures, errors, and test results"""
        failure_sections = []
        lines = log_text.split('\n')

        # Keywords that indicate failures
        failure_keywords = [
            'FAIL', 'ERROR', 'Exception', 'failed', 'UNSTABLE', 'ABORTED',
            'Test Results:', 'tests total', 'passed', 'Build step', 'changed build result'
        ]

        # Extract context around failures (50 lines before and after)
        context_size = 50
        for i, line in enumerate(lines):
            if any(keyword in line for keyword in failure_keywords):
                start = max(0, i - context_size)
                end = min(len(lines), i + context_size)
                section = '\n'.join(lines[start:end])
                failure_sections.append(section)

        # Also always include the last 100 lines (build summary)
        if len(lines) > 100:
            failure_sections.append('\n'.join(lines[-100:]))

        return failure_sections

    def analyze_log_chunked(self, log_text, job_name, build_number, model, groq_api_key, rca_mode=False, enable_fallback=True):
        """Analyze a single log by chunking it if needed

        Args:
            rca_mode: If True, focus on failure analysis and root cause
            enable_fallback: If True, cycle through fallback models on errors
        """
        try:
            chunk_size = 4000
            chunks = []

            if len(log_text) <= chunk_size:
                chunks = [log_text]
            else:
                if rca_mode:
                    # For RCA: Extract failure-specific sections
                    failure_sections = self._extract_failure_sections(log_text)

                    if failure_sections:
                        # Combine failure sections (up to chunk_size each)
                        for section in failure_sections[:3]:  # Max 3 failure sections
                            if len(section) > chunk_size:
                                chunks.append(section[:chunk_size])
                            else:
                                chunks.append(section)
                    else:
                        # No failures found, analyze end section
                        chunks.append(log_text[-chunk_size:])
                else:
                    # Standard mode: Get beginning, middle samples, and end
                    chunks.append(log_text[:chunk_size])  # Beginning
                    mid_point = len(log_text) // 2
                    chunks.append(log_text[mid_point:mid_point + chunk_size])
                    chunks.append(log_text[-chunk_size:])  # End (most important)

            # Analyze each chunk with automatic fallback
            analyses = []
            for i, chunk in enumerate(chunks):
                if rca_mode:
                    chunk_label = f"failure_section_{i+1}"
                else:
                    chunk_label = "beginning" if i == 0 else ("middle" if i == 1 else "end")

                analysis = self._analyze_chunk_with_fallback(chunk, chunk_label, job_name, build_number, model, groq_api_key, rca_mode, enable_fallback)
                analyses.append(analysis)

                # Small delay to avoid rate limits
                time.sleep(0.5)

            return "\n\n".join(analyses)

        except Exception as e:
            print(f"Exception in analyze_log_chunked: {e}")
            return f"Error analyzing logs: {str(e)}"

    def analyze_job_parallel(self, job_name, model, groq_api_key, rca_mode=False, enable_fallback=True):
        """Analyze a single job (helper for parallel processing)"""
        try:
            logs = self.get_job_logs(job_name, limit=1)

            if not logs:
                return {
                    'job': job_name,
                    'build': 'N/A',
                    'analysis': 'No builds found',
                    'log_size': 0,
                    'status': 'no_builds'
                }

            log_entry = logs[0]
            build_number = log_entry['build']
            log_type = log_entry.get('log_type', 'console')

            # Check if all tests passed (skip AI analysis to save credits)
            if log_type == 'passed':
                passed_tests = log_entry.get('passed_tests', 0)
                total_tests = log_entry.get('total_tests', 0)
                return {
                    'job': job_name,
                    'build': build_number,
                    'analysis': f'✅ All tests passed ({passed_tests}/{total_tests} tests)',
                    'log_size': 0,
                    'status': 'passed',
                    'passed_tests': passed_tests,
                    'total_tests': total_tests
                }

            log_text = log_entry['log']

            analysis = self.analyze_log_chunked(
                log_text, job_name, build_number, model, groq_api_key, rca_mode, enable_fallback
            )

            return {
                'job': job_name,
                'build': build_number,
                'analysis': analysis,
                'log_size': len(log_text),
                'status': 'success'
            }
        except Exception as e:
            return {
                'job': job_name,
                'build': 'N/A',
                'analysis': f'Error: {str(e)}',
                'log_size': 0,
                'status': 'error'
            }

@app.route('/logout')
def logout():
	"""Logout"""
	session.pop('logged_in', None)
	return redirect(url_for('index'))

@app.route('/admin', methods=['GET', 'POST'])
def admin_index():
	"""Admin login page - accessible directly at /admin"""
	if request.method == 'POST':
		password = request.form.get('password', '')
		password_hash = hashlib.sha256(password.encode()).hexdigest()

		if password_hash == ADMIN_PASSWORD_HASH:
			session['logged_in'] = True
			return redirect(url_for('admin_panel'))
		else:
			return render_template('login.html', error='Invalid password', show_back=False)

	# If already logged in, redirect to admin panel
	if session.get('logged_in'):
		return redirect(url_for('admin_panel'))

	# Show login form
	return render_template('login.html', show_back=False)

@app.route('/admin/panel')
@login_required
def admin_panel():
	"""Admin panel with API key management (password protected)"""
	return render_template('admin.html')

@app.route('/')
def index():
	"""Main user analyzer page where users can input their own credentials"""
	return render_template('index.html')

@app.route('/dashboard')
def dashboard():
	"""Enhanced dashboard with database-backed results"""
	return render_template('dashboard.html')

@app.route('/jira-guide')
def jira_guide():
	"""Interactive step-by-step guide for Jira dashboard integration"""
	return render_template('jira-guide.html')

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
                # gpt-oss-120b is the default and should be first
                if 'gpt-oss-120b' in model_id.lower():
                    priority_models.insert(0, {'id': model_id, 'name': f"⭐ {model_id} (Default)"})
                elif any(x in model_id.lower() for x in ['llama-3.3-70b', 'llama-3.1-70b', 'mixtral-8x7b', 'gemma2-9b']):
                    priority_models.append({'id': model_id, 'name': f"⭐ {model_id}"})
                else:
                    chat_models.append({'id': model_id, 'name': model_id})

        # Return priority models first
        return jsonify({'models': priority_models + chat_models})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
def analyze():
    """Streaming analysis endpoint with smart filtering and parallel processing"""
    data = request.json
    jenkins_url = data.get('jenkins_url')
    groq_api_key = data.get('groq_api_key')
    model = data.get('model', 'gpt-oss-120b')  # Default: gpt-oss-120b
    filter_failed = data.get('filter_failed', True)  # Default: only analyze failed jobs
    enable_rca = data.get('enable_rca', True)  # Default: enable RCA mode
    enable_fallback = data.get('enable_fallback', True)  # Default: enable model fallback
    parallel_workers = data.get('parallel_workers', 3)  # Default: 3 parallel workers
    specific_job = data.get('specific_job')  # Optional: analyze only this specific job
    completed_jobs = data.get('completed_jobs', [])  # Optional: list of already-analyzed jobs to skip

    if not all([jenkins_url, groq_api_key, model]):
        return jsonify({'error': 'Missing required fields'}), 400

    def generate():
        try:
            analyzer = JenkinsAnalyzer(jenkins_url)

            # Override specific_job if provided in request
            analyze_single_job_only = False
            if specific_job:
                analyzer.specific_job = specific_job
                analyze_single_job_only = True  # User explicitly selected a job - don't analyze pipeline

            # Determine which jobs to analyze
            jobs_to_analyze = []

            if analyzer.specific_job:
                if analyze_single_job_only:
                    # User explicitly selected a job - analyze ONLY this job, not the pipeline
                    yield f"data: {json.dumps({'type': 'status', 'message': f'Analyzing selected job only: {analyzer.specific_job}'})}\n\n"
                    jobs_to_analyze = [{'name': analyzer.specific_job}]
                else:
                    # Job from URL - check if it's part of a pipeline
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
                # All jobs from base URL - with smart filtering
                if filter_failed:
                    yield f"data: {json.dumps({'type': 'status', 'message': '🔍 Smart filtering: Fetching failed/unstable jobs only...'})}\n\n"
                    all_jobs = analyzer.get_jobs(filter_failed=True)
                    failed_count = len(all_jobs)

                    # Also get total count for comparison
                    total_jobs = analyzer.get_jobs(filter_failed=False)
                    total_count = len(total_jobs)

                    reduction_pct = ((total_count - failed_count) / total_count * 100) if total_count > 0 else 0
                    yield f"data: {json.dumps({'type': 'optimization', 'message': f'✅ Reduced analysis by {reduction_pct:.0f}%: {failed_count}/{total_count} jobs need analysis'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'status', 'message': 'Fetching all jobs...'})}\n\n"
                    all_jobs = analyzer.get_jobs(filter_failed=False)

                if not all_jobs:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'No jobs found'})}\n\n"
                    return

                jobs_to_analyze = all_jobs
                yield f"data: {json.dumps({'type': 'status', 'message': f'Found {len(jobs_to_analyze)} jobs. Starting parallel analysis...'})}\n\n"

            # Filter out already-completed jobs (for retry functionality)
            if completed_jobs:
                original_count = len(jobs_to_analyze)
                jobs_to_analyze = [job for job in jobs_to_analyze if job.get('name') not in completed_jobs]
                skipped_count = original_count - len(jobs_to_analyze)

                if skipped_count > 0:
                    yield f"data: {json.dumps({'type': 'status', 'message': f'🔄 Resuming analysis: Skipping {skipped_count} already-completed jobs, analyzing {len(jobs_to_analyze)} remaining jobs'})}\n\n"

                if len(jobs_to_analyze) == 0:
                    yield f"data: {json.dumps({'type': 'complete', 'message': 'All jobs already analyzed!'})}\n\n"
                    return

            # Parallel analysis with ThreadPoolExecutor
            if len(jobs_to_analyze) > 1 and parallel_workers > 1:
                yield f"data: {json.dumps({'type': 'status', 'message': f'🚀 Using {parallel_workers} parallel workers for 10x faster analysis'})}\n\n"

                with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
                    # Submit all jobs for parallel processing
                    future_to_job = {
                        executor.submit(analyzer.analyze_job_parallel, job.get('name'), model, groq_api_key, enable_rca, enable_fallback): job
                        for job in jobs_to_analyze if job.get('name')
                    }

                    completed = 0
                    for future in as_completed(future_to_job):
                        completed += 1
                        job = future_to_job[future]
                        job_name = job.get('name')

                        yield f"data: {json.dumps({'type': 'progress', 'current': completed, 'total': len(jobs_to_analyze), 'job': job_name})}\n\n"

                        try:
                            result = future.result()
                            mode_label = "🔬 RCA" if enable_rca else "📊 Analysis"
                            # Properly encode the result to avoid JSON parsing errors
                            result_data = {
                                'type': 'job_result',
                                'job': result['job'],
                                'build': result['build'],
                                'analysis': result['analysis'],
                                'log_size': result.get('log_size', 0),
                                'mode': mode_label
                            }
                            yield f"data: {json.dumps(result_data, ensure_ascii=False)}\n\n"
                        except Exception as e:
                            error_data = {
                                'type': 'job_result',
                                'job': job_name,
                                'build': 'N/A',
                                'analysis': f'Error: {str(e)}',
                                'log_size': 0
                            }
                            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            else:
                # Sequential analysis for single job or when parallel is disabled
                for idx, job in enumerate(jobs_to_analyze):
                    job_name = job.get('name')
                    if not job_name:
                        continue

                    yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': len(jobs_to_analyze), 'job': job_name})}\n\n"

                    result = analyzer.analyze_job_parallel(job_name, model, groq_api_key, enable_rca, enable_fallback)
                    mode_label = "🔬 RCA" if enable_rca else "📊 Analysis"
                    # Properly encode the result to avoid JSON parsing errors
                    result_data = {
                        'type': 'job_result',
                        'job': result['job'],
                        'build': result['build'],
                        'analysis': result['analysis'],
                        'log_size': result.get('log_size', 0),
                        'mode': mode_label
                    }
                    yield f"data: {json.dumps(result_data, ensure_ascii=False)}\n\n"

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
	        }
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


def extract_error_keywords(analysis_text):
	"""Extract key error terms from analysis for Jira search (no AI, just text extraction)."""
	if not analysis_text:
		return []

	keywords = []
	analysis_lower = analysis_text.lower()

	# Extract important phrases (2-4 words) that might be in Jira tickets
	import re

	# Look for key technical terms (2-4 word phrases)
	# Common error patterns
	common_phrases = [
		r'(timeout|connection|network|storage|backend|database|server|service)\s+\w+',
		r'\w+\s+(error|failure|failed|exception|issue)',
		r'(cannot|unable to|failed to)\s+\w+',
	]

	for pattern in common_phrases:
		matches = re.findall(pattern, analysis_lower, re.IGNORECASE)
		for match in matches[:3]:
			if isinstance(match, tuple):
				match = ' '.join(match)
			clean = match.strip()
			# Only keep phrases between 6-30 chars (not too short, not too long)
			if 6 <= len(clean) <= 30 and clean not in keywords:
				keywords.append(clean)

	# Also extract specific error codes or test names
	specific_patterns = [
		r'test[_\s]+([a-z0-9_]{5,20})',  # test names
		r'error[:\s]+([a-z0-9_\s]{5,30})',  # error messages
	]

	for pattern in specific_patterns:
		matches = re.findall(pattern, analysis_lower, re.IGNORECASE)
		for match in matches[:2]:
			clean = match.strip()
			if 5 <= len(clean) <= 30 and clean not in keywords:
				keywords.append(clean)

	return keywords[:5]  # Return max 5 keywords


def search_jira_for_similar_issues(jira_url, jira_pat, error_keywords, max_results=3):
	"""Search Jira for similar issues using error keywords (simple text search, no AI)."""
	if not error_keywords or not jira_url or not jira_pat:
		return []

	try:
		# Use Jira search API with JQL
		search_url = f"{jira_url}/rest/api/2/search"
		headers = {
		    "Authorization": f"Bearer {jira_pat}",
		    "Content-Type": "application/json"
		}

		# Build JQL query - search in summary and description
		# Only use keywords that are likely to be meaningful (6-30 chars)
		valid_keywords = [kw for kw in error_keywords[:3] if 6 <= len(kw) <= 30]

		if not valid_keywords:
			return []

		# Use text search (searches summary, description, comments)
		# This is more comprehensive than just summary search
		search_terms = ' OR '.join([f'text ~ "{kw}"' for kw in valid_keywords])

		# Don't filter by status - show all tickets (even resolved ones might be helpful)
		# Just order by most recent
		jql = f"({search_terms}) ORDER BY updated DESC"

		params = {
		    'jql': jql,
		    'maxResults': max_results,
		    'fields': 'key,summary,status'
		}

		response = requests.get(search_url, headers=headers, params=params, timeout=10)

		# Check if response is JSON (not HTML error page)
		content_type = response.headers.get('Content-Type', '')
		if 'application/json' not in content_type:
			app.logger.warning(f"Jira search returned non-JSON response: {content_type}")
			return []

		if response.status_code == 200:
			data = response.json()
			issues = data.get('issues', [])

			results = []
			for issue in issues:
				results.append({
				    'key': issue.get('key'),
				    'summary': issue.get('fields', {}).get('summary', 'No summary'),
				    'status': issue.get('fields', {}).get('status', {}).get('name', 'Unknown')
				})

			return results
		else:
			app.logger.warning(f"Jira search failed: {response.status_code}")
			return []

	except Exception as e:
		app.logger.warning(f"Error searching Jira for similar issues: {str(e)}")
		return []


def build_robot_framework_links(jenkins_base_url, job_name, build_number):
	"""Build Robot Framework report links for a Jenkins job."""
	if not jenkins_base_url or not job_name or not build_number:
		return []

	# Extract base Jenkins URL (remove /job/... if present)
	if '/job/' in jenkins_base_url:
		jenkins_base_url = jenkins_base_url.split('/job/')[0]

	jenkins_base_url = jenkins_base_url.rstrip('/')

	# Build the links
	links = []

	# Robot Framework log.html
	log_url = f"{jenkins_base_url}/job/{job_name}/{build_number}/robot/report/log.html"
	links.append({
		'label': 'Robot Framework Log',
		'url': log_url
	})

	# Robot Framework report directory
	report_url = f"{jenkins_base_url}/job/{job_name}/{build_number}/robot/"
	links.append({
		'label': 'Robot Framework Reports',
		'url': report_url
	})

	# Console output
	console_url = f"{jenkins_base_url}/job/{job_name}/{build_number}/console"
	links.append({
		'label': 'Console Output',
		'url': console_url
	})

	return links


def extract_main_failure_summary(analysis_text):
	"""Extract a 2-3 line summary of the main failure from analysis text."""
	if not analysis_text:
		return "No analysis available", "Unable to determine - please review attached analysis"

	# Split into lines and look for key failure indicators
	lines = analysis_text.split('\n')
	summary_lines = []
	suggested_action = "Unable to determine - please review attached analysis"

	# For multi-section analyses, find ALL root cause sections and pick the best one
	# Skip sections that say "unable to determine" or "unclear"
	all_root_causes = []

	for i, line in enumerate(lines):
		line_lower = line.lower().strip()
		if any(keyword in line_lower for keyword in ['root cause', 'main issue', 'primary failure', 'primary reason', 'key problem', 'failure reason']):
			# Extract this section (up to 5 lines)
			section = []
			section.append(line.strip())
			for j in range(1, 6):
				if i + j < len(lines) and lines[i + j].strip():
					section.append(lines[i + j].strip())
				else:
					break

			# Check if this section is useful (not "unable to determine")
			section_text = ' '.join(section).lower()
			if not any(skip_word in section_text for skip_word in ['unable to determine', 'unclear', 'cannot be identified', 'not visible', 'missing', 'insufficient']):
				all_root_causes.append(section)

	# Pick the best root cause section (prefer later sections which often have more detail)
	if all_root_causes:
		# Use the last good root cause section (usually the most detailed)
		best_section = all_root_causes[-1]
		summary_lines = best_section[:3]  # Take first 3 lines

	# If no good root cause found, look for error messages
	if not summary_lines:
		for i, line in enumerate(lines):
			line_lower = line.lower().strip()
			if any(keyword in line_lower for keyword in ['error:', 'failed:', 'failure:', 'exception:', 'cannot connect', 'connection refused']):
				summary_lines.append(line.strip())
				if i + 1 < len(lines) and lines[i + 1].strip():
					summary_lines.append(lines[i + 1].strip())
				if len(summary_lines) >= 3:
					break

	# If still nothing, take first 2-3 meaningful lines
	if not summary_lines:
		for line in lines[:10]:  # Check first 10 lines
			if line.strip() and not line.strip().startswith('#') and len(line.strip()) > 20:
				summary_lines.append(line.strip())
				if len(summary_lines) >= 3:
					break

	# Extract suggested action from analysis
	# Check the summary first (more specific), then the full analysis
	summary_text = ' '.join(summary_lines).lower() if summary_lines else ''
	analysis_lower = analysis_text.lower()

	# Priority order: specific issues first, then generic
	# Check for cluster/connection issues (very specific)
	if any(keyword in summary_text or keyword in analysis_lower for keyword in ['connection refused', 'cannot connect', 'cluster failed', 'transport endpoint', 'pacemaker', 'corosync']):
		suggested_action = "Infrastructure issue - check cluster/network connectivity"
	# Check for other infrastructure issues
	elif any(keyword in summary_text or keyword in analysis_lower for keyword in ['infrastructure', 'network', 'timeout', 'connection']):
		suggested_action = "Infrastructure issue - contact DevOps/SRE"
	# Check for configuration issues
	elif any(keyword in summary_text or keyword in analysis_lower for keyword in ['configuration', 'config', 'setup', 'environment']):
		suggested_action = "Check configuration/environment setup"
	# Check for code defects
	elif any(keyword in summary_text or keyword in analysis_lower for keyword in ['bug', 'defect', 'code issue', 'regression', 'broken']):
		suggested_action = "Create bug - code defect identified"
	# Check for test issues
	elif any(keyword in summary_text or keyword in analysis_lower for keyword in ['test issue', 'test code', 'test script', 'assertion']):
		suggested_action = "Fix test code/script"
	# Check for transient issues
	elif any(keyword in summary_text or keyword in analysis_lower for keyword in ['retest', 're-test', 'retry', 'run again', 'transient', 'intermittent', 'flaky']):
		suggested_action = "Retest - appears to be transient/intermittent issue"
	# Check for unclear cases
	elif any(keyword in summary_text or keyword in analysis_lower for keyword in ['unclear', 'uncertain', 'not sure', 'unable to determine', 'insufficient']):
		suggested_action = "Manual investigation needed - unclear from logs"

	# Limit to 3 lines max
	summary = ' '.join(summary_lines[:3]) if summary_lines else "Analysis available in attached file"
	return summary, suggested_action


def build_jira_adf_comment(analysis_results):
	"""Build Atlassian Document Format (ADF) comment for Jira API v3."""
	adf_content = []

	# Header
	adf_content.append({
	    "type": "heading",
	    "attrs": {"level": 2},
	    "content": [{"type": "text", "text": "Jenkins Build Analysis Summary"}]
	})

	# Main failure summary and suggested action
	if analysis_results:
		main_summary, suggested_action = extract_main_failure_summary(analysis_results[0].get('analysis', ''))
		adf_content.append({
		    "type": "paragraph",
		    "content": [
		        {"type": "text", "text": "Main Failure: ", "marks": [{"type": "strong"}]},
		        {"type": "text", "text": main_summary}
		    ]
		})
		adf_content.append({
		    "type": "paragraph",
		    "content": [
		        {"type": "text", "text": "Suggested Action: ", "marks": [{"type": "strong"}]},
		        {"type": "text", "text": suggested_action}
		    ]
		})

	# Summary
	adf_content.append({
	    "type": "paragraph",
	    "content": [
	        {"type": "text", "text": f"Total jobs analyzed: {len(analysis_results)}", "marks": [{"type": "strong"}]}
	    ]
	})

	# Table header
	table_rows = []
	table_rows.append({
	    "type": "tableRow",
	    "content": [
	        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Job"}]}]},
	        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Build"}]}]},
	        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Main Issue"}]}]}
	    ]
	})

	# Table rows
	for result in analysis_results:
		job_name = result.get('job') or 'Unknown'
		build = str(result.get('build', 'N/A'))
		main_issue, _ = extract_main_failure_summary(result.get('analysis', ''))
		if len(main_issue) > 100:
			main_issue = main_issue[:97] + "..."

		table_rows.append({
		    "type": "tableRow",
		    "content": [
		        {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": job_name}]}]},
		        {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": build}]}]},
		        {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": main_issue}]}]}
		    ]
		})

	adf_content.append({"type": "table", "content": table_rows})

	# Note about attachments
	adf_content.append({
	    "type": "paragraph",
	    "content": [
	        {"type": "text", "text": "Detailed analysis attached as files", "marks": [{"type": "em"}]}
	    ]
	})

	# Footer
	adf_content.append({"type": "rule"})
	adf_content.append({
	    "type": "paragraph",
	    "content": [
	        {"type": "text", "text": "Analyzed by Jenkins Log Analyzer", "marks": [{"type": "em"}]}
	    ]
	})

	return {
	    "version": 1,
	    "type": "doc",
	    "content": adf_content
	}


def build_jira_adf_comment_with_search(analysis_results, jira_url, jira_pat):
	"""Build ADF comment with similar issue search (for API v3)."""
	# Start with base ADF comment
	adf_content = []

	# Header
	adf_content.append({
	    "type": "heading",
	    "attrs": {"level": 2},
	    "content": [{"type": "text", "text": "Jenkins Build Analysis Summary"}]
	})

	# Main failure summary and suggested action
	if analysis_results:
		main_summary, suggested_action = extract_main_failure_summary(analysis_results[0].get('analysis', ''))
		adf_content.append({
		    "type": "paragraph",
		    "content": [
		        {"type": "text", "text": "Main Failure: ", "marks": [{"type": "strong"}]},
		        {"type": "text", "text": main_summary}
		    ]
		})
		adf_content.append({
		    "type": "paragraph",
		    "content": [
		        {"type": "text", "text": "Suggested Action: ", "marks": [{"type": "strong"}]},
		        {"type": "text", "text": suggested_action}
		    ]
		})

	# Summary
	adf_content.append({
	    "type": "paragraph",
	    "content": [
	        {"type": "text", "text": f"Total jobs analyzed: {len(analysis_results)}", "marks": [{"type": "strong"}]}
	    ]
	})

	# Table
	table_rows = []
	table_rows.append({
	    "type": "tableRow",
	    "content": [
	        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Job"}]}]},
	        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Build"}]}]},
	        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Main Issue"}]}]}
	    ]
	})

	for result in analysis_results:
		job_name = result.get('job') or 'Unknown'
		build = str(result.get('build', 'N/A'))
		main_issue, _ = extract_main_failure_summary(result.get('analysis', ''))
		if len(main_issue) > 100:
			main_issue = main_issue[:97] + "..."

		table_rows.append({
		    "type": "tableRow",
		    "content": [
		        {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": job_name}]}]},
		        {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": build}]}]},
		        {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": main_issue}]}]}
		    ]
		})

	adf_content.append({"type": "table", "content": table_rows})

	# Note about attachments
	adf_content.append({
	    "type": "paragraph",
	    "content": [
	        {"type": "text", "text": "Detailed analysis attached as files", "marks": [{"type": "em"}]}
	    ]
	})

	# Search for similar issues (wrapped in try-except to not break comment posting)
	try:
		if analysis_results:
			first_analysis = analysis_results[0].get('analysis', '')
			error_keywords = extract_error_keywords(first_analysis)

			if error_keywords:
				similar_issues = search_jira_for_similar_issues(jira_url, jira_pat, error_keywords, max_results=3)

				if similar_issues:
					adf_content.append({
					    "type": "heading",
					    "attrs": {"level": 3},
					    "content": [{"type": "text", "text": "Possibly Related Jira Tickets"}]
					})

					adf_content.append({
					    "type": "paragraph",
					    "content": [
					        {"type": "text", "text": "Found based on error text matching (not AI-based):", "marks": [{"type": "em"}]}
					    ]
					})

					# Bullet list of similar issues
					bullet_items = []
					for issue in similar_issues:
						issue_key = issue.get('key', 'Unknown')
						issue_summary = issue.get('summary', 'No summary')
						issue_status = issue.get('status', 'Unknown')

						if len(issue_summary) > 80:
							issue_summary = issue_summary[:77] + "..."

						bullet_items.append({
						    "type": "listItem",
						    "content": [{
						        "type": "paragraph",
						        "content": [
						            {"type": "text", "text": issue_key, "marks": [{"type": "link", "attrs": {"href": f"{jira_url}/browse/{issue_key}"}}]},
						            {"type": "text", "text": f" - {issue_summary} "},
						            {"type": "text", "text": f"(Status: {issue_status})", "marks": [{"type": "em"}]}
						        ]
						    }]
						})

					adf_content.append({"type": "bulletList", "content": bullet_items})

					adf_content.append({
					    "type": "paragraph",
					    "content": [
					        {"type": "text", "text": "Review these tickets to see if the issue is already known or has a solution.", "marks": [{"type": "em"}]}
					    ]
					})
	except Exception as e:
		# If search fails, just continue without similar issues section
		pass

	# Footer
	adf_content.append({"type": "rule"})
	adf_content.append({
	    "type": "paragraph",
	    "content": [
	        {"type": "text", "text": "Analyzed by Jenkins Log Analyzer", "marks": [{"type": "em"}]}
	    ]
	})

	return {
	    "version": 1,
	    "type": "doc",
	    "content": adf_content
	}


@app.route('/api/post-to-jira', methods=['POST'])
def post_to_jira():
		"""Post analysis results directly to a Jira ticket using a Jira PAT.

		Expected JSON body:
		{
		  "jira_url": "https://your-company.atlassian.net",
		  "jira_ticket": "PROJ-123",
		  "jira_pat": "<personal-access-token>",
		  "analysis_results": [ ... ]  # Same structure as UI results
		}
		"""
		data = request.json or {}
		jira_url = (data.get('jira_url') or '').strip()
		jira_ticket = (data.get('jira_ticket') or '').strip()
		jira_pat = data.get('jira_pat') or ''
		analysis_results = data.get('analysis_results') or []
		jenkins_url = (data.get('jenkins_url') or '').strip()  # Jenkins URL for Robot links
		api_version = data.get('api_version', '2')  # Default to v2
		attach_logs = data.get('attach_logs', False)  # Attach AI analysis files
		attach_raw_logs = data.get('attach_raw_logs', False)  # Attach raw console logs

		if not jira_url or not jira_ticket or not jira_pat:
			return jsonify({'error': 'Missing required fields: jira_url, jira_ticket, jira_pat'}), 400

		if not isinstance(analysis_results, list) or not analysis_results:
			return jsonify({'error': 'No analysis_results to post'}), 400

		# Normalize Jira base URL (remove trailing slash)
		jira_url = jira_url.rstrip('/')

		# Normalize Jenkins URL (remove trailing slash)
		if jenkins_url:
			jenkins_url = jenkins_url.rstrip('/')

		# Determine if we should use ADF (v3) or plain text (v2)
		use_adf = (api_version == '3')

		if use_adf:
			# Build ADF format for API v3 with similar issue search
			comment_body = build_jira_adf_comment_with_search(analysis_results, jira_url, jira_pat)
		else:
			# Build simple text comment (Jira API v2 format) with table
			comment_lines = []
			comment_lines.append("h2. Jenkins Build Analysis Summary")
			comment_lines.append("")

			# Main failure summary and suggested action (from first job)
			if analysis_results:
				main_summary, suggested_action = extract_main_failure_summary(analysis_results[0].get('analysis', ''))
				comment_lines.append("*Main Failure:* " + main_summary)
				comment_lines.append("")
				comment_lines.append("*Suggested Action:* " + suggested_action)
				comment_lines.append("")

			comment_lines.append(f"*Total jobs analyzed:* {len(analysis_results)}")
			comment_lines.append("")

			# Table format for job summary
			comment_lines.append("|| Job || Build || Main Issue ||")
			for result in analysis_results:
				job_name = result.get('job') or 'Unknown'
				build = result.get('build', 'N/A')
				analysis_text = result.get('analysis', '')
				main_issue, _ = extract_main_failure_summary(analysis_text)

				# Truncate main issue for table (max 100 chars)
				if len(main_issue) > 100:
					main_issue = main_issue[:97] + "..."

				comment_lines.append(f"| {job_name} | #{build} | {main_issue} |")

			comment_lines.append("")
			comment_lines.append("_Detailed analysis attached as files_")
			comment_lines.append("")

			# Search for similar Jira issues (simple text search, no AI)
			# Wrapped in try-except to ensure it doesn't break comment posting
			try:
				if analysis_results:
					# Extract error keywords from first analysis
					first_analysis = analysis_results[0].get('analysis', '')
					error_keywords = extract_error_keywords(first_analysis)

					if error_keywords:
						similar_issues = search_jira_for_similar_issues(jira_url, jira_pat, error_keywords, max_results=3)

						if similar_issues:
							comment_lines.append("h3. Possibly Related Jira Tickets")
							comment_lines.append("")
							comment_lines.append("_Found based on error text matching (not AI-based):_")
							comment_lines.append("")

							for issue in similar_issues:
								issue_key = issue.get('key', 'Unknown')
								issue_summary = issue.get('summary', 'No summary')
								issue_status = issue.get('status', 'Unknown')

								# Truncate summary if too long
								if len(issue_summary) > 80:
									issue_summary = issue_summary[:77] + "..."

								comment_lines.append(f"* [{issue_key}|{jira_url}/browse/{issue_key}] - {issue_summary} _(Status: {issue_status})_")

							comment_lines.append("")
							comment_lines.append("_Review these tickets to see if the issue is already known or has a solution._")
							comment_lines.append("")
			except Exception as e:
				app.logger.warning(f"Failed to search for similar Jira issues: {str(e)}")
				# Continue without similar issues section

			# Footer with attribution
			comment_lines.append("----")
			comment_lines.append("_Analyzed by Jenkins Log Analyzer_")

			# Join all lines into a single comment body
			comment_body = "\n".join(comment_lines)

		# Prepare comment payload based on API version
		if use_adf:
			comment_payload = {"body": comment_body}  # ADF format already built
		else:
			comment_payload = {"body": comment_body}  # Plain text format

		comment_url = f"{jira_url}/rest/api/{api_version}/issue/{jira_ticket}/comment"
		headers = {
		    "Authorization": f"Bearer {jira_pat}",
		    "Accept": "application/json",
		    "Content-Type": "application/json",
		}

		try:
			resp = requests.post(comment_url, headers=headers, json=comment_payload, timeout=20)
		except requests.RequestException as e:
			app.logger.exception("Error posting analysis to Jira")
			return jsonify({'error': f'Failed to connect to Jira: {str(e)}'}), 502

		if resp.status_code not in (200, 201):
			# Try to extract a useful error message from Jira's response
			message = None
			try:
				err_json = resp.json()
				if isinstance(err_json, dict):
					if 'errorMessages' in err_json and err_json['errorMessages']:
						message = '; '.join(err_json['errorMessages'])
					elif 'message' in err_json:
						message = err_json['message']
			except ValueError:
				pass

			if not message:
				message = resp.text[:500]

			return jsonify({'error': f'Jira API returned {resp.status_code}: {message}'}), 400

		try:
			resp_json = resp.json()
		except ValueError:
			resp_json = {}

		# Jira returns the comment ID in the response
		comment_id = resp_json.get('id') or resp_json.get('self', '').split('/')[-1] or 'created'

		# Attach AI analysis files if requested
		attachments_info = []
		if attach_logs:
			for result in analysis_results:
				analysis_text = result.get('analysis')  # Changed from log_content to analysis
				job_name = result.get('job', 'unknown')
				build = result.get('build', 'unknown')

				if analysis_text:
					try:
						# Create filename for AI analysis
						filename = f"{job_name.replace('/', '_')}_build_{build}_analysis.txt"

						# Jira attachment API endpoint
						attach_url = f"{jira_url}/rest/api/{api_version}/issue/{jira_ticket}/attachments"
						attach_headers = {
						    "Authorization": f"Bearer {jira_pat}",
						    "X-Atlassian-Token": "no-check",  # Required for attachments
						}

						# Prepare file for upload
						files = {
						    'file': (filename, analysis_text.encode('utf-8'), 'text/plain')
						}

						attach_resp = requests.post(attach_url, headers=attach_headers, files=files, timeout=30)

						if attach_resp.status_code in [200, 201]:
							attach_json = attach_resp.json()
							if isinstance(attach_json, list) and len(attach_json) > 0:
								attachments_info.append({
								    'filename': filename,
								    'id': attach_json[0].get('id'),
								    'size': attach_json[0].get('size')
								})
						else:
							app.logger.warning(f"Failed to attach analysis for {job_name} build {build}: {attach_resp.status_code}")
					except Exception as e:
						app.logger.exception(f"Error attaching analysis file for {job_name} build {build}")

		# Attach raw console/debug logs if requested (disabled by default)
		if attach_raw_logs:
			for result in analysis_results:
				log_content = result.get('log_content')  # Raw Jenkins console log
				job_name = result.get('job', 'unknown')
				build = result.get('build', 'unknown')

				if log_content:
					try:
						# Create filename for raw log
						filename = f"{job_name.replace('/', '_')}_build_{build}_console.log"

						# Jira attachment API endpoint
						attach_url = f"{jira_url}/rest/api/{api_version}/issue/{jira_ticket}/attachments"
						attach_headers = {
						    "Authorization": f"Bearer {jira_pat}",
						    "X-Atlassian-Token": "no-check",
						}

						# Prepare file for upload
						files = {
						    'file': (filename, log_content.encode('utf-8'), 'text/plain')
						}

						attach_resp = requests.post(attach_url, headers=attach_headers, files=files, timeout=30)

						if attach_resp.status_code in [200, 201]:
							attach_json = attach_resp.json()
							if isinstance(attach_json, list) and len(attach_json) > 0:
								attachments_info.append({
								    'filename': filename,
								    'id': attach_json[0].get('id'),
								    'size': attach_json[0].get('size'),
								    'type': 'raw_log'
								})
						else:
							app.logger.warning(f"Failed to attach raw log for {job_name} build {build}: {attach_resp.status_code}")
					except Exception as e:
						app.logger.exception(f"Error attaching raw log file for {job_name} build {build}")

		response_data = {
		    'status': 'ok',
		    'comment_id': comment_id,
		    'jira_ticket': jira_ticket,
		}

		if attachments_info:
			response_data['attachments'] = attachments_info
			response_data['attachments_count'] = len(attachments_info)

		return jsonify(response_data)


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
	"""Get the most recent dashboard results from database"""
	try:
		analyses = get_latest_analyses(limit=100)

		if not analyses:
			return jsonify({'error': 'No results available'}), 404

		# Group by date
		by_date = {}
		for analysis in analyses:
			date_key = analysis['date_key']
			if date_key not in by_date:
				by_date[date_key] = []
			by_date[date_key].append(analysis)

		# Get most recent date
		latest_date = max(by_date.keys())
		latest_analyses = by_date[latest_date]

		# Calculate stats
		total_jobs = len(latest_analyses)
		passed_jobs = sum(1 for a in latest_analyses if a['status'] == 'passed')
		failed_jobs = sum(1 for a in latest_analyses if a['failed_tests'] > 0)
		total_tests = sum(a['total_tests'] for a in latest_analyses)
		total_passed_tests = sum(a['passed_tests'] for a in latest_analyses)

		return jsonify({
			'date': latest_date,
			'total_jobs': total_jobs,
			'passed_jobs': passed_jobs,
			'failed_jobs': failed_jobs,
			'total_tests': total_tests,
			'passed_tests': total_passed_tests,
			'analyses': latest_analyses,
			'timestamp': latest_analyses[0]['analyzed_at'] if latest_analyses else None
		})

	except Exception as e:
		return jsonify({'error': str(e)}), 500


@app.route('/api/dashboard/date-range', methods=['GET'])
def get_dashboard_date_range():
	"""Get analyses for a specific date range"""
	start_date = request.args.get('start_date')
	end_date = request.args.get('end_date')
	jenkins_url = request.args.get('jenkins_url')

	if not start_date or not end_date:
		return jsonify({'error': 'start_date and end_date required'}), 400

	try:
		analyses = get_analyses_by_date_range(start_date, end_date, jenkins_url)

		# Group by date
		by_date = {}
		for analysis in analyses:
			date_key = analysis['date_key']
			if date_key not in by_date:
				by_date[date_key] = {
					'date': date_key,
					'jobs': [],
					'total_jobs': 0,
					'passed_jobs': 0,
					'failed_jobs': 0
				}

			by_date[date_key]['jobs'].append(analysis)
			by_date[date_key]['total_jobs'] += 1
			if analysis['status'] == 'passed':
				by_date[date_key]['passed_jobs'] += 1
			elif analysis['failed_tests'] > 0:
				by_date[date_key]['failed_jobs'] += 1

		return jsonify({
			'start_date': start_date,
			'end_date': end_date,
			'results': list(by_date.values())
		})

	except Exception as e:
		return jsonify({'error': str(e)}), 500


@app.route('/api/dashboard/filter', methods=['GET'])
def get_dashboard_filtered():
	"""Get analyses with multiple filters: date range, job names, calver"""
	start_date = request.args.get('start_date')
	end_date = request.args.get('end_date')
	job_names_str = request.args.get('job_names', '')
	calver = request.args.get('calver', '')
	hide_empty = request.args.get('hide_empty', 'false').lower() == 'true'
	jenkins_url = request.args.get('jenkins_url')

	try:
		# Get base data (all or date range)
		if start_date and end_date:
			analyses = get_analyses_by_date_range(start_date, end_date, jenkins_url)
		else:
			# Get last 30 days if no date range specified
			end_date_obj = datetime.now()
			start_date_obj = end_date_obj - timedelta(days=30)
			start_date = start_date_obj.strftime('%Y-%m-%d')
			end_date = end_date_obj.strftime('%Y-%m-%d')
			analyses = get_analyses_by_date_range(start_date, end_date, jenkins_url)

		# Parse job names filter
		job_names_filter = []
		if job_names_str:
			job_names_filter = [name.strip() for name in job_names_str.split(',') if name.strip()]

		# Apply filters
		filtered_analyses = []
		for analysis in analyses:
			# Filter out "No builds found" if requested
			if hide_empty:
				analysis_text = analysis.get('analysis_text', '').lower()
				if 'no builds found' in analysis_text or analysis.get('build_number', 0) == 0:
					continue

			# Filter by job names (partial match)
			if job_names_filter:
				job_name = analysis.get('job_name', '').lower()
				if not any(filter_name.lower() in job_name for filter_name in job_names_filter):
					continue

			# Filter by CalVer (check if build number or job name contains calver)
			if calver:
				job_name = analysis.get('job_name', '')
				build_number = str(analysis.get('build_number', ''))
				if calver not in job_name and calver not in build_number:
					continue

			filtered_analyses.append(analysis)

		# Group by date
		by_date = {}
		for analysis in filtered_analyses:
			date_key = analysis['date_key']
			if date_key not in by_date:
				by_date[date_key] = {
					'date': date_key,
					'jobs': [],
					'total_jobs': 0,
					'passed_jobs': 0,
					'failed_jobs': 0
				}

			by_date[date_key]['jobs'].append(analysis)
			by_date[date_key]['total_jobs'] += 1
			if analysis['status'] == 'passed':
				by_date[date_key]['passed_jobs'] += 1
			elif analysis['failed_tests'] > 0:
				by_date[date_key]['failed_jobs'] += 1

		return jsonify({
			'start_date': start_date,
			'end_date': end_date,
			'job_names': job_names_str,
			'calver': calver,
			'results': list(by_date.values())
		})

	except Exception as e:
		return jsonify({'error': str(e)}), 500


@app.route('/api/dashboard/new-failures', methods=['GET'])
def get_new_failures():
	"""Get new failures in the last N days"""
	days = int(request.args.get('days', 7))
	jenkins_url = request.args.get('jenkins_url')

	try:
		failures = get_new_failures_since(days, jenkins_url)

		# Get detailed info for each failure
		detailed_failures = []
		for failure in failures:
			job_name = failure['job_name']

			# Get latest analysis for this job
			with db_lock:
				conn = sqlite3.connect(DB_PATH)
				conn.row_factory = sqlite3.Row
				cursor = conn.cursor()

				cursor.execute('''
					SELECT * FROM job_analyses
					WHERE job_name = ?
					ORDER BY analyzed_at DESC
					LIMIT 1
				''', (job_name,))

				row = cursor.fetchone()
				conn.close()

				if row:
					detailed_failures.append(dict(row))

		return jsonify({
			'days': days,
			'count': len(detailed_failures),
			'failures': detailed_failures
		})

	except Exception as e:
		return jsonify({'error': str(e)}), 500


@app.route('/api/dashboard-chat', methods=['POST'])
def dashboard_chat():
	"""AI assistant to answer questions about the dashboard"""
	data = request.json or {}

	user_question = data.get('question')
	dashboard_data = data.get('dashboard_data', {})

	# Get credentials from environment
	groq_api_key = os.getenv('GROQ_API_KEY')
	# Use model from request, fallback to environment default
	model = data.get('model', os.getenv('DEFAULT_MODEL', 'llama-3.3-70b-versatile'))

	if not user_question or not groq_api_key:
		return jsonify({'error': 'Missing question or API key not configured'}), 400

	try:
		# Prepare context from dashboard data
		total_jobs = dashboard_data.get('total_jobs', 0)
		passed_jobs = dashboard_data.get('passed_jobs', 0)
		failed_jobs = dashboard_data.get('failed_jobs', 0)
		unstable_jobs = dashboard_data.get('unstable_jobs', 0)
		date = dashboard_data.get('date', 'unknown')

		# Get detailed job lists
		failed_job_list = dashboard_data.get('failed_job_list', [])
		unstable_job_list = dashboard_data.get('unstable_job_list', [])
		passed_job_list = dashboard_data.get('passed_job_list', [])

		# Format job lists for context
		failed_jobs_text = ""
		if failed_job_list:
			failed_jobs_text = "\n\nFailed Jobs:\n" + "\n".join([
				f"- {job['name']} #{job['build']} ({job['failed_tests']}/{job['total_tests']} tests failed)"
				for job in failed_job_list
			])

		unstable_jobs_text = ""
		if unstable_job_list:
			unstable_jobs_text = "\n\nUnstable Jobs (passed but with test failures):\n" + "\n".join([
				f"- {job['name']} #{job['build']}" + (f" ({job['failed_tests']}/{job['total_tests']} tests failed)" if job.get('failed_tests', 0) > 0 else "")
				for job in unstable_job_list
			])

		passed_jobs_text = ""
		if passed_job_list and len(passed_job_list) > 0:
			passed_jobs_text = f"\n\nSome Passed Jobs: {', '.join([job['name'] for job in passed_job_list[:3]])}"

		system_prompt = f'''You are a helpful AI assistant for the Jenkins Analysis Dashboard.
You help users understand their test results and build status.

Current Dashboard Summary:
- Date: {date}
- Total Jobs: {total_jobs}
- Passed: {passed_jobs}
- Failed: {failed_jobs}
- Unstable: {unstable_jobs}{failed_jobs_text}{unstable_jobs_text}{passed_jobs_text}

IMPORTANT: Be EXTREMELY ACCURATE with numbers and facts. Always use the exact numbers from the dashboard summary above.
- "Unstable" jobs are jobs that passed but have test failures
- "Failed" jobs are jobs that failed completely
- "Passed" jobs are jobs that passed with no test failures

Answer questions concisely and helpfully. Focus on:
1. Explaining build statuses and test results accurately
2. Helping users understand failure patterns
3. Suggesting next steps for investigation
4. Clarifying dashboard features
5. Providing specific job names when asked about failures or unstable builds

Keep answers brief (2-3 sentences max) unless asked for details or lists.'''

		user_prompt = f"User question: {user_question}"

		headers = {
			'Authorization': f'Bearer {groq_api_key}',
			'Content-Type': 'application/json'
		}

		payload = {
			'model': model,
			'messages': [
				{'role': 'system', 'content': system_prompt},
				{'role': 'user', 'content': user_prompt}
			],
			'temperature': 0.1,  # Low temperature for factual accuracy
			'max_tokens': 300
		}

		response = requests.post('https://api.groq.com/openai/v1/chat/completions',
		                        headers=headers, json=payload, timeout=30)

		if response.status_code == 200:
			result = response.json()
			answer = result['choices'][0]['message']['content']
			return jsonify({'answer': answer})
		else:
			return jsonify({'error': 'Failed to get response from AI'}), 500

	except Exception as e:
		print(f"Error in dashboard chat: {e}")
		return jsonify({'error': str(e)}), 500


@app.route('/api/reanalyze-job', methods=['POST'])
def reanalyze_job():
	"""Re-analyze a specific job using smart Robot Framework log extraction (failed tests only)"""
	data = request.json or {}

	job_name = data.get('job_name')
	build_number = data.get('build_number')
	jenkins_url = data.get('jenkins_url')

	# Get credentials from environment (server-side)
	groq_api_key = os.getenv('GROQ_API_KEY')
	model = data.get('model', os.getenv('DEFAULT_MODEL', 'llama-3.3-70b-versatile'))

	if not all([job_name, build_number, jenkins_url, groq_api_key]):
		return jsonify({'error': 'Missing required parameters or server not configured'}), 400

	try:
		analyzer = JenkinsAnalyzer(jenkins_url)

		# Try smart Robot Framework log extraction first (extracts only failed test chunks)
		print(f"🔍 Attempting smart Robot Framework log extraction for {job_name} #{build_number}")
		robot_log = analyzer._get_robot_framework_log(job_name, build_number)

		log_to_analyze = None
		log_type = 'console'
		log_sources = []

		if robot_log and robot_log.startswith('PASSED|||'):
			# All tests passed
			parts = robot_log.split('|||')
			passed_count = int(parts[1]) if len(parts) > 1 else 0
			total_count = int(parts[2]) if len(parts) > 2 else 0

			analysis = f'✅ All tests passed ({passed_count}/{total_count} tests) - No failures to analyze'
			log_type = 'passed'
			log_sources = ['robot_framework']

			# Update database
			save_job_analysis(
				job_name=job_name,
				build_number=build_number,
				jenkins_url=jenkins_url,
				analysis_text=analysis,
				status='reanalyzed',
				log_type=log_type,
				log_size=0,
				passed_tests=passed_count,
				failed_tests=0,
				total_tests=total_count,
				model_used=model,
				date_key=time.strftime('%Y-%m-%d')
			)

			return jsonify({
				'status': 'success',
				'job_name': job_name,
				'build_number': build_number,
				'analysis': analysis,
				'log_sources': log_sources,
				'log_size': 0
			})

		elif robot_log:
			# Smart extraction succeeded - use extracted failed test chunks only
			print(f"✅ Smart extraction succeeded - analyzing only failed test chunks ({len(robot_log)} chars)")
			log_to_analyze = robot_log
			log_type = 'robot_smart_extraction'
			log_sources = ['robot_framework', 'debug.log', 'console']
		else:
			# Fallback to console log if Robot Framework extraction failed
			print(f"⚠️ Robot Framework extraction failed - falling back to console log")
			console_url = f"{jenkins_url}/job/{job_name}/{build_number}/consoleText"
			console_response = analyzer.session.get(console_url, timeout=30)
			log_to_analyze = console_response.text if console_response.status_code == 200 else ""
			log_type = 'console'
			log_sources = ['console']

		# Perform deep RCA analysis
		print(f"🔬 Performing RCA analysis on {len(log_to_analyze)} chars of log data")
		analysis = analyzer.analyze_log_chunked(
			log_to_analyze, job_name, build_number, model, groq_api_key,
			rca_mode=True, enable_fallback=True
		)

		# Update database with new analysis
		save_job_analysis(
			job_name=job_name,
			build_number=build_number,
			jenkins_url=jenkins_url,
			analysis_text=analysis,
			status='reanalyzed',
			log_type=log_type,
			log_size=len(log_to_analyze),
			passed_tests=0,  # Will be extracted from analysis if needed
			failed_tests=0,
			total_tests=0,
			model_used=model,
			date_key=time.strftime('%Y-%m-%d')
		)

		return jsonify({
			'status': 'success',
			'job_name': job_name,
			'build_number': build_number,
			'analysis': analysis,
			'log_sources': log_sources,
			'log_size': len(log_to_analyze)
		})

	except Exception as e:
		print(f"Error re-analyzing job: {e}")
		import traceback
		traceback.print_exc()
		return jsonify({'error': str(e)}), 500


@app.route('/api/run-daily-analysis', methods=['POST'])
def run_daily_analysis():
	"""
	Trigger daily automated analysis for all jobs.
	This endpoint should be called by a cron job daily.
	Uses server-side credentials from environment variables.
	"""
	data = request.json or {}

	# Get credentials from environment (server-side)
	groq_api_key = os.getenv('GROQ_API_KEY')
	jenkins_url = os.getenv('JENKINS_URL')
	model = data.get('model', os.getenv('DEFAULT_MODEL', 'llama-3.3-70b-versatile'))

	if not groq_api_key or not jenkins_url:
		return jsonify({
			'error': 'Server not configured. Set GROQ_API_KEY and JENKINS_URL environment variables.'
		}), 500

	# Start analysis in background thread
	task_id = str(uuid.uuid4())
	analysis_tasks[task_id] = {
		'status': 'running',
		'started_at': time.time(),
		'type': 'daily_analysis'
	}

	def run_analysis():
		"""Background task for daily analysis"""
		date_key = datetime.now().strftime('%Y-%m-%d')

		try:
			analyzer = JenkinsAnalyzer(jenkins_url)

			# Get all jobs (not just failed ones for daily analysis)
			jobs = analyzer.get_jobs(filter_failed=False)

			total_jobs = len(jobs)
			analyzed_count = 0
			passed_count = 0
			failed_count = 0

			print(f"🚀 Starting daily analysis for {total_jobs} jobs...")

			for job in jobs:
				job_name = job.get('name')
				if not job_name:
					continue

				try:
					# First, get the latest build number WITHOUT analyzing
					logs = analyzer.get_job_logs(job_name, limit=1)
					if not logs:
						print(f"⏭️  Skipping {job_name} - no builds found")
						continue

					latest_build = logs[0]['build']

					# Check if this build was already analyzed
					if is_build_already_analyzed(job_name, latest_build, jenkins_url):
						print(f"⏭️  Skipping {job_name} #{latest_build} - already analyzed")
						continue

					# Analyze job
					result = analyzer.analyze_job_parallel(
						job_name, model, groq_api_key,
						rca_mode=True, enable_fallback=True
					)

					# Check for rate limit
					if 'RATE_LIMIT_STOP' in result.get('analysis', ''):
						# Extract wait time and pause
						analysis_text = result.get('analysis', '')
						if '|||' in analysis_text:
							parts = analysis_text.split('|||')
							if len(parts) >= 2:
								wait_info = parts[1]
								print(f"⏸️  Rate limit hit. {wait_info}. Pausing...")

								# Extract seconds from wait info (e.g., "Reset in 5m 30s")
								import re
								time_match = re.search(r'(\d+)m\s*(\d+)s|(\d+)s', wait_info)
								if time_match:
									if time_match.group(1):
										wait_seconds = int(time_match.group(1)) * 60 + int(time_match.group(2))
									else:
										wait_seconds = int(time_match.group(3))

									print(f"⏳ Waiting {wait_seconds} seconds for rate limit reset...")
									time.sleep(wait_seconds + 5)  # Add 5 seconds buffer
									print(f"✅ Resuming analysis...")

									# Retry this job
									result = analyzer.analyze_job_parallel(
										job_name, model, groq_api_key,
										rca_mode=True, enable_fallback=True
									)

					# Save to database
					save_job_analysis(
						job_name=job_name,
						build_number=result.get('build', 0),
						jenkins_url=jenkins_url,
						analysis_text=result.get('analysis', ''),
						status=result.get('status', 'success'),
						log_type=result.get('log_type', 'console'),
						log_size=result.get('log_size', 0),
						passed_tests=result.get('passed_tests', 0),
						failed_tests=result.get('failed_tests', 0),
						total_tests=result.get('total_tests', 0),
						model_used=model,
						date_key=date_key
					)

					analyzed_count += 1
					if result.get('status') == 'passed':
						passed_count += 1
					elif result.get('failed_tests', 0) > 0:
						failed_count += 1

					print(f"✅ [{analyzed_count}/{total_jobs}] {job_name} - {result.get('status')}")

					# Small delay between jobs
					time.sleep(1)

				except Exception as e:
					print(f"❌ Error analyzing {job_name}: {e}")
					continue

			# Update task status
			analysis_tasks[task_id]['status'] = 'complete'
			analysis_tasks[task_id]['completed_at'] = time.time()
			analysis_tasks[task_id]['results'] = {
				'total_jobs': total_jobs,
				'analyzed': analyzed_count,
				'passed': passed_count,
				'failed': failed_count,
				'date': date_key
			}

			print(f"🎉 Daily analysis complete! {analyzed_count}/{total_jobs} jobs analyzed")

		except Exception as e:
			analysis_tasks[task_id]['status'] = 'error'
			analysis_tasks[task_id]['error'] = str(e)
			print(f"❌ Daily analysis failed: {e}")

	# Start background thread
	thread = Thread(target=run_analysis)
	thread.daemon = True
	thread.start()

	return jsonify({
		'task_id': task_id,
		'status': 'started',
		'message': 'Daily analysis started in background'
	})


@app.route('/api/daily-analysis-status/<task_id>', methods=['GET'])
def get_daily_analysis_status(task_id):
	"""Get status of a daily analysis task"""
	task = analysis_tasks.get(task_id)

	if not task:
		return jsonify({'error': 'Task not found'}), 404

	return jsonify(task)


if __name__ == '__main__':
	app.run(debug=True, port=5000)
