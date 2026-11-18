# BookOrchestra Publishing Studio Frontend

This directory contains the Streamlit frontend for **BookOrchestra**, a multi-agent IT & Engineering book authoring and publishing studio.

## How to Launch Locally

1. Ensure you have activated your Python environment and installed dependencies (`pip install -r requirements.txt`).
2. Navigate to the project root directory:
   ```bash
   cd book-orchestra
   ```
3. Run the Streamlit application:
   ```bash
   streamlit run frontend/app.py
   ```
4. Open the URL provided in the terminal (usually `http://localhost:8501`) in your browser.
5. Click **"Load IT/Engineering Book Example"** in the sidebar to inspect or run a complete distributed LLM systems book example.

## Stopping Unfinished Book Generation Processes

The frontend spawns `book_writing_cli.py` in a background subprocess. On Linux, terminating the Streamlit server terminates the child process automatically. On macOS or if an orphan process persists, clean up manually:

```bash
pkill -f book_writing_cli.py
```
