# V-Assure: Veeva Vault Test Automation Suite

V-Assure is an end-to-end solution designed to streamline the creation of test automation scripts for Veeva Vault. It consists of a high-performance Chrome Extension for recording UI interactions and a robust FastAPI backend that leverages Large Language Models (LLMs) and Retrieval-Augmented Generation (RAG) to generate precise, structured test scripts.

## 🚀 Features

- **Intelligent Recording:** Capture clicks, inputs, and navigations directly within Veeva Vault.
- **AI-Powered Generation:** Transform raw UI interactions into polished test scripts using state-of-the-art LLMs.
- **Enhanced RAG Engine:** Utilizes a custom Knowledge Base (KB) to ensure generated scripts follow established patterns and terminology.
- **Multi-Provider Support:** Seamlessly switch between LLM providers like **Groq**, **AWS Bedrock**, or **Local** servers via LiteLLM.
- **Real-time Streaming:** View script generation progress in real-time with Server-Sent Events (SSE).
- **Session Management:** Track, store, and download your generated scripts in both TXT and JSON formats.
- **Docker Ready:** Easy deployment using Docker and Docker Compose.

---

## 🛠️ Project Structure

- **`veeva-scraper/`**: The Chrome Extension (Manifest v3) responsible for UI recording and interacting with the backend.
- **`veeva-backend/`**: The FastAPI server handling the logic for script generation, RAG, and session management.
- **`docker-compose.yml`**: Orchestration for running both services in a containerized environment.

---

## 🚦 Getting Started

### Prerequisites

- Python 3.9+
- Google Chrome Browser
- Docker & Docker Compose (optional)

### Backend Setup

1. Navigate to the backend directory:
   ```bash
   cd veeva-backend
   ```
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Configure your environment variables in a `.env` file:
   ```env
   GROQ_API_KEY=your_groq_api_key
   # Optional: BEDROCK_CREDENTIALS, LOCAL_API_BASE, etc.
   ```
4. Start the server:
   ```bash
   uvicorn main:app --reload
   ```

### Chrome Extension Setup

1. Open Chrome and navigate to `chrome://extensions/`.
2. Enable **Developer mode** in the top right corner.
3. Click **Load unpacked**.
4. Select the `veeva-scraper` folder from this repository.
5. The V-Assure icon should now appear in your browser's extension bar.

### Using Docker

To run the entire suite using Docker:
```bash
docker-compose up --build
```

---

## 📖 Usage

1. **Record:** Open Veeva Vault, click the V-Assure extension icon, and start recording your test flow.
2. **Generate:** Once finished, click "Generate Script". The extension will send the captured steps to the backend.
3. **Refine:** The backend uses RAG to compare your steps against the Knowledge Base for maximum accuracy.
4. **Export:** Download the final script as a structured TXT report or a raw JSON file for further processing.

---

## 🔧 Technical Stack

- **Frontend:** HTML5, CSS3, Vanilla JavaScript, Chrome Extension API (v3).
- **Backend:** Python, FastAPI, Pydantic, LiteLLM.
- **AI/ML:** RAG (Custom implementation with TF-IDF/BM25), LLM Integration (Groq, Bedrock).
- **DevOps:** Docker, Docker Compose.

---

## 🛡️ License

This project is proprietary and confidential. Unauthorized copying or distribution is strictly prohibited.
